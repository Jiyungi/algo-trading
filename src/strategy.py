"""Automated daily trading strategy.
Runs once at market open via GitHub Actions (free).

Architecture (~6 Alpaca API calls per day, everything else is free):
  yfinance        -> fetch bars for ~100 symbols
  signals.py      -> regime detection + confluence score (-4 to +4)
  safety.py       -> portfolio health + position size gates
  trade_log       -> circuit breaker + cooldowns
  position_state  -> trailing stop + take-profit + time-based exit
  portfolio_risk  -> correlation filter + concentration + vol sizing
  Alpaca          -> place orders only (paper trading, free)

Signal stack (each +1 / -1 / 0, regime-aware):
  EMA 5/20 crossover   -- trend direction (faster than original 10/50)
  RSI 7                -- trend: >60 bullish / mean-rev: <30 oversold
  MACD                 -- momentum shift
  Volume confirmation  -- accumulation / distribution

Entry: score >= +3 AND has_catalyst (gap >2% or volume spike)

Exit (in priority order):
  1. Time-based   -- sell 100% after MAX_HOLD_DAYS trading days
  2. Trailing stop -- sell 100% if price drops 7% below peak
  3. Tranche      -- sell 50% at +18% gain (let winners run longer)
  4. Signal exit  -- sell 100% if score <= -3
"""
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import trading_client, DRY_RUN  # noqa: E402
from orders import place_order  # noqa: E402
from safety import (  # noqa: E402
    portfolio_health_check,
    pre_trade_check,
    market_is_open,
    MAX_NEW_POSITIONS,
)
from signals import (  # noqa: E402
    compute_score,
    detect_regime,
    ema,
    has_catalyst,
    momentum_continuation,
)
from scanner import fetch_bars_yf, UNIVERSE  # noqa: E402
from trade_log import (  # noqa: E402
    log_trade,
    circuit_breaker_ok,
    is_on_cooldown,
    add_cooldown,
    can_override_cooldown,
)
from position_state import (  # noqa: E402
    init_state,
    ensure_initialized,
    update_peak,
    get_tranches,
    get_days_held,
    mark_tranche,
    clear_state,
    cleanup_closed,
    MAX_HOLD_DAYS,
)
from portfolio_risk import (  # noqa: E402
    correlation_filter,
    concentration_check,
    volatility_adjusted_qty,
    log_portfolio_metrics,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

BUY_THRESHOLD = 3    # need 3+ signals to buy
SELL_THRESHOLD = -3  # signal exit threshold

# Exit parameters
TRAIL_PCT = 0.07      # trailing stop: 7% drop from peak sells everything
TAKE_PROFIT = 18.0    # single tranche: sell 50% at +18%


def run():
    logger.info("=" * 60)
    logger.info(
        "AUTO STRATEGY  |  %s  |  %s",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        "DRY RUN" if DRY_RUN else "LIVE PAPER",
    )
    logger.info("=" * 60)

    # 1. Market hours guard
    if not market_is_open():
        logger.warning("Market is closed — exiting.")
        return

    # 2. Fetch account state (2 Alpaca API calls)
    account = trading_client.get_account()
    positions = trading_client.get_all_positions()
    portfolio_value = float(account.portfolio_value)
    held = {p.symbol: p for p in positions}

    # 3. Portfolio health gate
    ok, reason = portfolio_health_check(account, positions)
    if not ok:
        logger.warning("Portfolio health FAILED: %s — exiting.", reason)
        return
    logger.info(
        "Health OK | Portfolio: $%,.2f | Cash: $%,.2f",
        portfolio_value, float(account.cash),
    )

    # 4. Circuit breaker
    circuit_ok, circuit_reason = circuit_breaker_ok()
    if not circuit_ok:
        logger.warning("Circuit breaker TRIGGERED: %s", circuit_reason)
        logger.warning("Will check exits but skip all new buys today.")
    else:
        logger.info("Circuit breaker: %s", circuit_reason)

    # 5. Fetch bars via yfinance (free, one call)
    all_symbols = list(set(UNIVERSE) | set(held.keys()))
    bars = fetch_bars_yf(all_symbols)
    if not bars:
        logger.error("No bar data returned — exiting.")
        return

    # 6. Detect market regime from SPY
    spy_bars = bars.get("SPY")
    regime = detect_regime(spy_bars)
    logger.info("Regime: %s", regime.upper())

    # 7. Check exits on held positions (always runs, even if circuit broken)
    exited = set()
    for sym, pos in held.items():
        pl_pct = float(pos.unrealized_plpc) * 100
        qty = float(pos.qty)
        price = float(pos.current_price)
        entry = float(pos.avg_entry_price)

        # Bootstrap state for pre-existing positions (no-op if already tracked)
        ensure_initialized(sym, price, entry, pl_pct)

        peak = update_peak(sym, price, entry_price=entry)
        # Tighten trailing stop to 5% once position is up >= 10%
        trail_pct = 0.05 if pl_pct >= 10.0 else TRAIL_PCT
        trail_stop_price = peak * (1 - trail_pct)
        tranches = get_tranches(sym)
        days_held = get_days_held(sym)

        # ── Priority 1: Time-based exit ──────────────────────────────────
        # Frees capital from slow trades; critical for 1-month horizon.
        if days_held >= MAX_HOLD_DAYS:
            logger.info(
                "TIME EXIT   | %s | %d days | P&L: %+.1f%%",
                sym, days_held, pl_pct,
            )
            place_order(sym, qty, side="sell")
            log_trade(sym, "sell", qty, price,
                      f"time_exit({days_held}d)", pl_pct)
            clear_state(sym)
            exited.add(sym)
            continue

        # ── Priority 2: Trailing stop ─────────────────────────────────────
        if price <= trail_stop_price:
            drop_from_peak = ((price - peak) / peak) * 100
            logger.info(
                "TRAIL STOP  | %s | %.1f%% from peak | P&L: %+.1f%%",
                sym, drop_from_peak, pl_pct,
            )
            place_order(sym, qty, side="sell")
            log_trade(sym, "sell", qty, price, "trailing_stop", pl_pct)
            clear_state(sym)
            if pl_pct < 0:
                add_cooldown(sym, stop_price=trail_stop_price)
            exited.add(sym)
            continue

        # ── Priority 3: Early weak-momentum exit ─────────────────────────
        # Exit flat trades where price has slipped below EMA(5).
        # Avoids tying up capital in stagnant positions.
        if sym in bars and -3.0 <= pl_pct <= 3.0:
            ema5 = ema(bars[sym]["close"], 5)
            if price < ema5:
                logger.info(
                    "WEAK EXIT   | %s | below EMA5 | P&L: %+.1f%%",
                    sym, pl_pct,
                )
                place_order(sym, qty, side="sell")
                log_trade(sym, "sell", qty, price,
                          "weak_momentum_exit", pl_pct)
                clear_state(sym)
                exited.add(sym)
                continue

        # ── Priority 4: Single take-profit tranche at +18% ───────────────
        if pl_pct >= TAKE_PROFIT and tranches < 1:
            sell_qty = max(1, int(qty * 0.50))
            logger.info(
                "TAKE PROFIT | %s | +%.1f%% | selling %d of %d shares",
                sym, pl_pct, sell_qty, int(qty),
            )
            place_order(sym, sell_qty, side="sell")
            log_trade(sym, "sell", sell_qty, price, "take_profit", pl_pct)
            mark_tranche(sym, 1)

        # ── Priority 5: Signal exit ───────────────────────────────────────
        elif sym in bars:
            df = bars[sym]
            score = compute_score(df["close"], df["volume"], regime=regime)
            if score <= SELL_THRESHOLD:
                logger.info(
                    "SIGNAL SELL | %s | score=%+d | P&L: %+.1f%%",
                    sym, score, pl_pct,
                )
                place_order(sym, qty, side="sell")
                log_trade(sym, "sell", qty, price,
                          f"signal_exit(score={score})", pl_pct)
                clear_state(sym)
                if pl_pct < 0:
                    add_cooldown(sym, stop_price=price)
                exited.add(sym)
            else:
                logger.info(
                    "HOLD        | %s | score=%+d | P&L: %+.1f%% "
                    "| day %d/%d | trail $%.2f",
                    sym, score, pl_pct, days_held, MAX_HOLD_DAYS,
                    trail_stop_price,
                )

    cleanup_closed(set(held.keys()) - exited)

    # 8. Scan for buy signals (skipped if circuit breaker triggered)
    if not circuit_ok:
        logger.warning("Skipping new buys — circuit breaker is active.")
        logger.info("Done | Exits: %d | Buys: 0", len(exited))
        return

    still_held = set(held.keys()) - exited
    candidates = []

    for sym in UNIVERSE:
        if sym in still_held:
            continue
        if sym not in bars:
            continue

        df = bars[sym]
        score = compute_score(df["close"], df["volume"], regime=regime)
        price = float(df["close"].iloc[-1])
        catalyst = has_catalyst(df)
        effective_score = score + (1 if catalyst else 0)

        if is_on_cooldown(sym):
            if can_override_cooldown(sym, effective_score, price):
                logger.info(
                    "COOLDOWN OVERRIDE | %s | score=%+d | recovery",
                    sym, effective_score,
                )
            else:
                logger.info("COOLDOWN    | %s | skipping", sym)
                continue

        # Primary entry: effective score meets threshold
        if effective_score >= BUY_THRESHOLD:
            logger.info(
                "SCAN        | %s | score=%+d | $%.2f | catalyst=%s",
                sym, effective_score, price, catalyst,
            )
            candidates.append((sym, effective_score, price))
        # Secondary entry: momentum continuation
        elif score >= 2 and momentum_continuation(df["close"]):
            logger.info(
                "MOMENTUM    | %s | score=%+d | $%.2f | continuation",
                sym, score, price,
            )
            candidates.append((sym, score, price))

    candidates.sort(key=lambda x: x[1], reverse=True)
    candidates = correlation_filter(candidates, still_held, bars)
    logger.info(
        "Candidates after correlation filter: %d", len(candidates)
    )

    new_buys = 0
    for sym, score, price in candidates:
        if new_buys >= MAX_NEW_POSITIONS:
            logger.info("MAX_NEW_POSITIONS reached — done for today.")
            break

        ok, reason = concentration_check(sym, positions, portfolio_value)
        if not ok:
            logger.warning("CONCENTRATION | %s", reason)
            continue
        logger.info("CONCENTRATION | %s | %s", sym, reason)

        # Conviction-weighted, volatility-adjusted sizing
        qty = volatility_adjusted_qty(sym, bars, portfolio_value, score=score)
        ok, reason = pre_trade_check(sym, qty, price, portfolio_value)
        if not ok:
            logger.warning("Pre-trade failed | %s: %s", sym, reason)
            continue

        logger.info(
            "BUY         | %s | score=%+d | qty=%d | $%.2f | %s regime",
            sym, score, qty, price, regime,
        )
        place_order(sym, qty, side="buy")
        log_trade(sym, "buy", qty, price,
                  f"signal_entry(score={score},regime={regime})")
        init_state(sym, price)
        new_buys += 1

    if not candidates:
        logger.info(
            "No buy signals today (regime=%s, threshold=%d+catalyst).",
            regime, BUY_THRESHOLD,
        )

    log_portfolio_metrics(trading_client)

    logger.info("-" * 60)
    logger.info("Done | Regime: %s | Buys: %d | Exits: %d",
                regime.upper(), new_buys, len(exited))


if __name__ == "__main__":
    run()
