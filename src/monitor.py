"""Portfolio monitor — view-only alerts + automated intraday exit checks.

Two modes:
  start_monitoring()     -- interactive loop with terminal output (menu opt 5)
  run_intraday_check()   -- one-shot execution for GitHub Actions cron

Intraday exit rules (only for held positions):
  Recovery exit: losing position reclaims VWAP or first-hour high -> sell
  Failure exit:  price stays below VWAP AND below opening range -> cut early
"""
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import (  # noqa: E402
    trading_client, CATEGORY_MAP, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
)
from orders import place_order  # noqa: E402
from trade_log import log_trade  # noqa: E402
from position_state import (  # noqa: E402
    get_trade_type, clear_state, update_peak,
)

logger = logging.getLogger(__name__)

alerts_sent = set()


# ── Interactive monitor (unchanged) ──────────────────────────────────────────

def check_and_alert():
    positions = trading_client.get_all_positions()
    account = trading_client.get_account()
    port_val = float(account.portfolio_value)
    total_pl = sum(float(p.unrealized_pl) for p in positions)
    now = datetime.now().strftime("%H:%M:%S")

    print(
        f"\n  -- {now} | Portfolio: ${port_val:,.2f}"
        f" | P&L: ${total_pl:+,.2f} --"
    )

    for p in positions:
        symbol = p.symbol
        pl_pct = float(p.unrealized_plpc) * 100
        pl_usd = float(p.unrealized_pl)
        cat = CATEGORY_MAP.get(symbol, "Other")
        icon = "+" if pl_pct >= 0 else "-"

        print(
            f"    {icon} {symbol:<10} {pl_pct:+.2f}%"
            f"   ${pl_usd:+,.2f}"
        )

        key_sl = f"{symbol}_stoploss"
        if pl_pct <= STOP_LOSS_PCT and key_sl not in alerts_sent:
            print(
                f"\n  STOP LOSS: {symbol} at {pl_pct:.2f}%!"
                f" Consider selling!"
            )
            alerts_sent.add(key_sl)

        key_tp = f"{symbol}_profit"
        if pl_pct >= TAKE_PROFIT_PCT and key_tp not in alerts_sent:
            print(
                f"\n  TAKE PROFIT: {symbol} at +{pl_pct:.2f}%!"
                f" Consider locking in gains!"
            )
            alerts_sent.add(key_tp)


def start_monitoring(interval=60, max_checks=480):
    print("=" * 60)
    print("PORTFOLIO MONITOR STARTED")
    print(
        f"   Checking every {interval}s"
        f" | Stop loss: {STOP_LOSS_PCT}%"
        f" | Take profit: +{TAKE_PROFIT_PCT}%"
    )
    print("   Press Ctrl+C to stop")
    print("=" * 60)

    for i in range(1, max_checks + 1):
        try:
            check_and_alert()
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)


# ── Intraday helpers ─────────────────────────────────────────────────────────

def _fetch_intraday_bars(symbols, interval="15m"):
    """Fetch intraday bars for today via yfinance.

    Returns {symbol: DataFrame} with columns: open, high, low, close, volume.
    Returns empty dict on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed")
        return {}

    if not symbols:
        return {}

    try:
        raw = yf.download(
            tickers=symbols,
            period="1d",
            interval=interval,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error("Intraday download failed: %s", e)
        return {}

    result = {}
    for sym in symbols:
        try:
            df = (
                raw.copy() if len(symbols) == 1
                else raw[sym].copy()
            )
            df = df.dropna(subset=["Close"])
            if len(df) < 2:
                continue
            df = df.rename(columns={
                "Open": "open", "High": "high",
                "Low": "low", "Close": "close",
                "Volume": "volume",
            })
            result[sym] = df[["open", "high", "low", "close", "volume"]]
        except (KeyError, Exception):
            pass

    logger.info(
        "Intraday bars: %d / %d symbols", len(result), len(symbols),
    )
    return result


def _compute_vwap(df):
    """VWAP = sum(typical_price * volume) / sum(volume)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    total_vol = vol.sum()
    if total_vol <= 0:
        return None
    return float((tp * vol).sum() / total_vol)


def _compute_opening_range(df, bars_count=4):
    """Return (high, low) of the first N bars (~first hour for 15m).

    Returns (None, None) if insufficient data.
    """
    if len(df) < bars_count:
        bars_count = len(df)
    if bars_count < 1:
        return None, None
    first = df.iloc[:bars_count]
    return float(first["high"].max()), float(first["low"].min())


# ── Automated intraday check ────────────────────────────────────────────────

def run_intraday_check():
    """One-shot intraday exit check for held positions.

    Called by GitHub Actions 2-3x per day between market open and close.
    Evaluates VWAP and opening-range-based exits.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    logger.info("=" * 60)
    logger.info("INTRADAY CHECK | %s",
                datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    # Check market hours
    try:
        if not trading_client.get_clock().is_open:
            logger.warning("Market is closed — skipping intraday check.")
            return
    except Exception as e:
        logger.warning("Could not check market clock: %s", e)
        return

    positions = trading_client.get_all_positions()
    if not positions:
        logger.info("No positions held — nothing to check.")
        return

    symbols = [p.symbol for p in positions]
    intra_bars = _fetch_intraday_bars(symbols)
    if not intra_bars:
        logger.warning("No intraday data — skipping.")
        return

    exits = 0
    for pos in positions:
        sym = pos.symbol
        if sym not in intra_bars:
            continue

        df = intra_bars[sym]
        price = float(pos.current_price)
        pl_pct = float(pos.unrealized_plpc) * 100
        qty = float(pos.qty)
        entry = float(pos.avg_entry_price)
        ttype = get_trade_type(sym)

        vwap = _compute_vwap(df)
        or_high, or_low = _compute_opening_range(df)

        if vwap is None or or_high is None:
            logger.info(
                "INTRA SKIP  | %s | insufficient intraday data", sym,
            )
            continue

        # ── Recovery exit: sell into strength ────────────────────────
        # If position is losing but price has reclaimed VWAP or
        # broken above first-hour high → exit into the recovery.
        if pl_pct < -2.0 and (price > vwap or price > or_high):
            logger.info(
                "RECOVERY EXIT | %s | P&L: %+.1f%% | price=$%.2f"
                " | VWAP=$%.2f | OR_high=$%.2f | type=%s",
                sym, pl_pct, price, vwap, or_high, ttype,
            )
            place_order(sym, qty, side="sell")
            log_trade(
                sym, "sell", qty, price,
                f"intraday_recovery(vwap={vwap:.2f})", pl_pct,
            )
            clear_state(sym)
            exits += 1
            continue

        # ── Failure exit: cut weak positions early ───────────────────
        # Price below VWAP AND below opening range low → momentum
        # has failed, exit before daily stop triggers.
        # More aggressive for mean-reversion (just below VWAP enough)
        if ttype == "mean_reversion":
            failure = price < vwap and price < or_low
        elif ttype == "catalyst":
            # Catalyst: gap fully retraced (below opening range)
            failure = price < or_low
        else:
            # Trend: need meaningful failure (>1.5% below VWAP)
            failure = (
                price < vwap * 0.985
                and price < or_low
            )

        if failure and pl_pct < 0:
            logger.info(
                "FAILURE EXIT | %s | P&L: %+.1f%% | price=$%.2f"
                " | VWAP=$%.2f | OR_low=$%.2f | type=%s",
                sym, pl_pct, price, vwap, or_low, ttype,
            )
            place_order(sym, qty, side="sell")
            log_trade(
                sym, "sell", qty, price,
                f"intraday_failure(vwap={vwap:.2f})", pl_pct,
            )
            clear_state(sym)
            exits += 1
            continue

        # No intraday action
        logger.info(
            "INTRA HOLD  | %s | P&L: %+.1f%% | price=$%.2f"
            " | VWAP=$%.2f | type=%s",
            sym, pl_pct, price, vwap, ttype,
        )

    logger.info(
        "Intraday check complete | Exits: %d / %d positions",
        exits, len(positions),
    )


if __name__ == "__main__":
    start_monitoring()
