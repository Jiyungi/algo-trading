"""Automated daily trading strategy.
Runs once at market open via GitHub Actions (free).

Architecture (~5 Alpaca API calls per day, everything else is free):
  yfinance        -> fetch bars for ~100 symbols
  signals.py      -> score each symbol (-4 to +4 confluence)
  safety.py       -> portfolio health + position size gates
  trade_log       -> circuit breaker + cooldowns
  position_state  -> trailing stop + tiered take-profit tracking
  portfolio_risk  -> correlation filter + concentration + vol sizing
  Alpaca          -> place orders only (paper trading, free)

Signal stack (each contributes +1 / -1 / 0):
  EMA 10/50 crossover  -- trend direction
  RSI 14               -- overbought / oversold
  MACD                 -- momentum shift
  Volume confirmation  -- accumulation / distribution

Entry: score >= +3 (3 of 4 signals agree to buy)

Exit (in priority order):
  1. Trailing stop  -- sell 100% if price drops 7% below its all-time peak
  2. Tranche 1      -- sell 33% at +7% gain (lock in early profit)
  3. Tranche 2      -- sell 50% of remainder at +15% gain
  4. Signal exit    -- sell 100% of remainder if score <= -3
  Remainder after tranches rides the trailing stop until it triggers.
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
from signals import compute_score  # noqa: E402
from scanner import fetch_bars_yf, UNIVERSE  # noqa: E402
from trade_log import (  # noqa: E402
    log_trade,
    circuit_breaker_ok,
    is_on_cooldown,
    add_cooldown,
)
from position_state import (  # noqa: E402
    init_state,
    ensure_initialized,
    update_peak,
    get_tranches,
    mark_tranche,
    clear_state,
    cleanup_closed,
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

POSITION_SIZE_PCT = 0.05  # allocate 5% of portfolio per new position
BUY_THRESHOLD = 3          # need 3+ signals to buy
SELL_THRESHOLD = -3        # signal exit threshold (remainder after tranches)

# Exit parameters
TRAIL_PCT = 0.07           # trailing stop: 7% drop from peak sells everything
TAKE_PROFIT_1 = 7.0        # tranche 1: sell 33% of position at +7%
TAKE_PROFIT_2 = 15.0       # tranche 2: sell 50% of remainder at +15%


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
        portfolio_value,
        float(account.cash),
    )

    # 4. Circuit breaker — check if recent performance is bad
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

    # 6. Check exits on held positions (always runs, even if circuit broken)
    exited = set()
    for sym, pos in held.items():
        pl_pct = float(pos.unrealized_plpc) * 100
        qty = float(pos.qty)
        price = float(pos.current_price)
        entry = float(pos.avg_entry_price)

        # Bootstrap state for pre-existing positions (no-op if already tracked)
        ensure_initialized(sym, price, entry, pl_pct)

        # Update peak price (ratchets up, never down)
        peak = update_peak(sym, price, entry_price=entry)
        trail_stop_price = peak * (1 - TRAIL_PCT)
        tranches = get_tranches(sym)

        # ── Priority 1: Trailing stop ────────────────────────────────────
        # Fires when price drops TRAIL_PCT% below its all-time peak.
        # Sells 100% of remaining position regardless of tranches taken.
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
                add_cooldown(sym)
            exited.add(sym)
            continue

        # ── Priority 2: Tranche 2 — sell 50% of remainder at +15% ───────
        elif pl_pct >= TAKE_PROFIT_2 and tranches < 2:
            sell_qty = max(1, int(qty * 0.50))
            logger.info(
                "TAKE PROFIT 2 | %s | +%.1f%% | selling %d of %d shares",
                sym, pl_pct, sell_qty, qty,
            )
            place_order(sym, sell_qty, side="sell")
            log_trade(sym, "sell", sell_qty, price, "take_profit_2", pl_pct)
            mark_tranche(sym, 2)

        # ── Priority 3: Tranche 1 — sell 33% at +7% ─────────────────────
        elif pl_pct >= TAKE_PROFIT_1 and tranches < 1:
            sell_qty = max(1, int(qty * 0.33))
            logger.info(
                "TAKE PROFIT 1 | %s | +%.1f%% | selling %d of %d shares",
                sym, pl_pct, sell_qty, qty,
            )
            place_order(sym, sell_qty, side="sell")
            log_trade(sym, "sell", sell_qty, price, "take_profit_1", pl_pct)
            mark_tranche(sym, 1)

        # ── Priority 4: Signal exit — sell 100% of remainder ────────────
        elif sym in bars:
            df = bars[sym]
            score = compute_score(df["close"], df["volume"])
            if score <= SELL_THRESHOLD:
                logger.info(
                    "SIGNAL SELL | %s | score=%+d | P&L: %+.1f%%",
                    sym, score, pl_pct,
                )
                place_order(sym, qty, side="sell")
                log_trade(
                    sym, "sell", qty, price,
                    f"signal_exit(score={score})", pl_pct,
                )
                clear_state(sym)
                if pl_pct < 0:
                    add_cooldown(sym)
                exited.add(sym)
            else:
                logger.info(
                    "HOLD        | %s | score=%+d | P&L: %+.1f%% "
                    "| peak $%.2f | trail stop $%.2f",
                    sym, score, pl_pct, peak, trail_stop_price,
                )

    cleanup_closed(set(held.keys()) - exited)

    # 7. Scan for buy signals (skipped if circuit breaker is triggered)
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
        if is_on_cooldown(sym):
            logger.info("COOLDOWN    | %s | skipping", sym)
            continue
        df = bars[sym]
        score = compute_score(df["close"], df["volume"])
        price = float(df["close"].iloc[-1])
        logger.info("SCAN        | %s | score=%+d | $%.2f", sym, score, price)
        if score >= BUY_THRESHOLD:
            candidates.append((sym, score, price))

    candidates.sort(key=lambda x: x[1], reverse=True)

    # Correlation filter: remove candidates that move with held positions
    candidates = correlation_filter(candidates, still_held, bars)
    logger.info("After correlation filter: %d candidates", len(candidates))

    new_buys = 0
    for sym, score, price in candidates:
        if new_buys >= MAX_NEW_POSITIONS:
            logger.info("MAX_NEW_POSITIONS reached — done for today.")
            break

        # Asset class concentration check
        ok, reason = concentration_check(sym, positions, portfolio_value)
        if not ok:
            logger.warning("CONCENTRATION | %s", reason)
            continue
        logger.info("CONCENTRATION | %s | %s", sym, reason)

        # Per-trade safety check
        qty = volatility_adjusted_qty(sym, bars, portfolio_value)
        ok, reason = pre_trade_check(sym, qty, price, portfolio_value)
        if not ok:
            logger.warning("Pre-trade failed | %s: %s", sym, reason)
            continue

        logger.info(
            "BUY         | %s | score=%+d | qty=%d | $%.2f",
            sym, score, qty, price,
        )
        place_order(sym, qty, side="buy")
        log_trade(sym, "buy", qty, price, f"signal_entry(score={score})")
        init_state(sym, price)
        new_buys += 1

    if not candidates:
        logger.info("No buy signals today.")

    # Log Sharpe + drawdown (1 extra API call, still free tier)
    log_portfolio_metrics(trading_client)

    logger.info("-" * 60)
    logger.info("Done | Buys: %d | Exits: %d", new_buys, len(exited))


if __name__ == "__main__":
    run()
