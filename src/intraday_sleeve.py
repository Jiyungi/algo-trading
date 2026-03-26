"""Intraday trading sleeve — dedicated 15% of capital for gap trades.

Separate from the swing strategy. Trades only highly liquid large-caps/ETFs.
Enters on gap + volume confirmation, uses opening range breakout/failure,
and closes ALL positions by end of day (no overnight holding).

Entry:  gap > 2% + volume > 1.5x average + opening range breakout
Exit:   take-profit +1.5%, stop-loss -1%, time-stop 3:30 PM ET, OR failure
State:  data/intraday_state.json (cleared at start of each trading day)
"""
import json
import logging
import os
import sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(__file__))

from config import trading_client, DRY_RUN  # noqa: E402
from orders import place_order  # noqa: E402
from trade_log import log_trade  # noqa: E402
from safety import market_is_open  # noqa: E402
from monitor import _fetch_intraday_bars  # noqa: E402

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

SLEEVE_PCT = 0.15           # 15% of portfolio for intraday
GAP_THRESHOLD = 0.02        # minimum 2% gap
VOLUME_MULTIPLIER = 1.5     # volume must exceed 1.5x average
MAX_INTRADAY_POSITIONS = 2  # max simultaneous intraday trades

# Exit parameters
TAKE_PROFIT_PCT = 1.5       # +1.5% quick take
STOP_LOSS_PCT = -1.0        # -1% tight stop
EOD_EXIT_HOUR = 15          # 3 PM ET — close all by this hour
EOD_EXIT_MINUTE = 30        # 3:30 PM ET

# Highly liquid names only — tight spreads, deep liquidity
INTRADAY_UNIVERSE = [
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN", "GOOGL",
    "XLE", "XLF", "XLK",
]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "intraday_state.json")


# ── State management ────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        # Clear stale state from a previous day
        if data.get("_date") != date.today().isoformat():
            return {}
        return data
    except Exception:
        return {}


def _save_state(state: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    state["_date"] = date.today().isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _clear_state():
    _save_state({})


# ── Gap scanner ──────────────────────────────────────────────────────────────

def _scan_gaps(daily_bars, intraday_bars):
    """Find gap candidates: |gap| >= 2% and volume > 1.5x average.

    Returns list of (symbol, gap_pct, direction) sorted by |gap| desc.
    Only considers gap-up for long entries (no shorting).
    """
    candidates = []
    for sym in INTRADAY_UNIVERSE:
        if sym not in daily_bars or sym not in intraday_bars:
            continue

        daily = daily_bars[sym]
        intra = intraday_bars[sym]

        if len(daily) < 21 or len(intra) < 2:
            continue

        prev_close = float(daily["close"].iloc[-1])
        today_open = float(intra["open"].iloc[0])

        if prev_close <= 0:
            continue

        gap_pct = (today_open - prev_close) / prev_close

        # Only gap-up (long only)
        if gap_pct < GAP_THRESHOLD:
            continue

        # Volume check: first bar volume vs average daily volume
        avg_vol = daily["volume"].iloc[-20:].mean()
        if avg_vol > 0:
            first_bar_vol = float(intra["volume"].iloc[0])
            # Scale: 15-min bar is ~1/26 of daily volume normally
            scaled_vol = first_bar_vol * 26
            if scaled_vol < avg_vol * VOLUME_MULTIPLIER:
                continue

        candidates.append((sym, gap_pct, "long"))

    candidates.sort(key=lambda x: abs(x[1]), reverse=True)
    return candidates


# ── Opening range breakout signal ────────────────────────────────────────────

def _check_breakout(intraday_df, or_high):
    """Return True if current price is above opening range high."""
    if len(intraday_df) < 3:
        return False
    current = float(intraday_df["close"].iloc[-1])
    return current > or_high


# ── Main entry point ────────────────────────────────────────────────────────

def run_intraday_sleeve():
    """Run the intraday gap-trading sleeve.

    Called at specific times during the trading day:
      10:00 AM ET — scan gaps, enter positions (OR just formed)
      12:00 PM ET — check exits
       2:30 PM ET — close all remaining positions
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    logger.info("=" * 60)
    logger.info(
        "INTRADAY SLEEVE | %s",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    logger.info("=" * 60)

    if not market_is_open():
        logger.warning("Market is closed — skipping intraday sleeve.")
        return

    account = trading_client.get_account()
    portfolio_value = float(account.portfolio_value)
    sleeve_capital = portfolio_value * SLEEVE_PCT
    positions = trading_client.get_all_positions()
    held_syms = {p.symbol for p in positions}

    state = _load_state()
    now = datetime.now()
    is_eod = (
        now.hour > EOD_EXIT_HOUR
        or (now.hour == EOD_EXIT_HOUR and now.minute >= EOD_EXIT_MINUTE)
    )

    logger.info(
        "Sleeve capital: $%,.2f | Portfolio: $%,.2f",
        sleeve_capital, portfolio_value,
    )

    # ── EOD: Close all intraday positions ────────────────────────
    if is_eod:
        logger.info("EOD — closing all intraday sleeve positions.")
        closed = 0
        for sym, info in list(state.items()):
            if sym.startswith("_"):
                continue
            if sym not in held_syms:
                continue
            pos = next(
                (p for p in positions if p.symbol == sym), None,
            )
            if pos is None:
                continue
            qty = float(pos.qty)
            price = float(pos.current_price)
            entry_price = info.get("entry_price", price)
            pl_pct = ((price - entry_price) / entry_price * 100
                      if entry_price > 0 else 0)
            logger.info(
                "EOD CLOSE   | %s | P&L: %+.1f%% | $%.2f",
                sym, pl_pct, price,
            )
            place_order(sym, qty, side="sell")
            log_trade(sym, "sell", qty, price,
                      "intraday_eod_close", pl_pct)
            closed += 1
        _clear_state()
        logger.info("EOD complete | Closed: %d", closed)
        return

    # ── Check exits on existing intraday positions ────────────────
    for sym, info in list(state.items()):
        if sym.startswith("_"):
            continue
        if sym not in held_syms:
            continue

        pos = next((p for p in positions if p.symbol == sym), None)
        if pos is None:
            continue

        price = float(pos.current_price)
        entry_price = info.get("entry_price", price)
        if entry_price <= 0:
            continue
        pl_pct = (price - entry_price) / entry_price * 100

        # Take profit
        if pl_pct >= TAKE_PROFIT_PCT:
            logger.info(
                "INTRA TP    | %s | +%.1f%% | $%.2f", sym, pl_pct, price,
            )
            place_order(sym, float(pos.qty), side="sell")
            log_trade(sym, "sell", float(pos.qty), price,
                      "intraday_take_profit", pl_pct)
            del state[sym]
            _save_state(state)
            continue

        # Stop loss
        if pl_pct <= STOP_LOSS_PCT:
            logger.info(
                "INTRA SL    | %s | %.1f%% | $%.2f", sym, pl_pct, price,
            )
            place_order(sym, float(pos.qty), side="sell")
            log_trade(sym, "sell", float(pos.qty), price,
                      "intraday_stop_loss", pl_pct)
            del state[sym]
            _save_state(state)
            continue

        # Opening range failure: price below OR low
        or_low = info.get("or_low")
        if or_low and price < or_low and pl_pct < 0:
            logger.info(
                "INTRA OR FAIL | %s | %.1f%% | below OR $%.2f",
                sym, pl_pct, or_low,
            )
            place_order(sym, float(pos.qty), side="sell")
            log_trade(sym, "sell", float(pos.qty), price,
                      "intraday_or_failure", pl_pct)
            del state[sym]
            _save_state(state)
            continue

        logger.info(
            "INTRA HOLD  | %s | %+.1f%% | $%.2f", sym, pl_pct, price,
        )

    # ── Scan for new entries (morning only, before 11:30 AM) ─────
    active_count = sum(
        1 for k in state if not k.startswith("_")
    )
    if now.hour >= 12 or (now.hour == 11 and now.minute >= 30):
        logger.info("Past entry window — no new scans.")
        return
    if active_count >= MAX_INTRADAY_POSITIONS:
        logger.info(
            "Max intraday positions (%d) — no new scans.",
            MAX_INTRADAY_POSITIONS,
        )
        return

    # Fetch daily bars for gap detection + intraday bars
    try:
        import yfinance as yf
        raw = yf.download(
            tickers=INTRADAY_UNIVERSE,
            period="30d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        daily_bars = {}
        for sym in INTRADAY_UNIVERSE:
            try:
                df = raw[sym].copy().dropna(subset=["Close"])
                df = df.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low", "Close": "close",
                    "Volume": "volume",
                })
                if len(df) >= 20:
                    daily_bars[sym] = df
            except (KeyError, Exception):
                pass
    except Exception as e:
        logger.error("Failed to fetch daily bars: %s", e)
        return

    intraday_bars = _fetch_intraday_bars(INTRADAY_UNIVERSE)

    gap_candidates = _scan_gaps(daily_bars, intraday_bars)
    if not gap_candidates:
        logger.info("No gap candidates found.")
        return

    new_entries = 0
    per_position = sleeve_capital / MAX_INTRADAY_POSITIONS
    max_per_position = portfolio_value * 0.10  # hard cap 10%
    alloc = min(per_position, max_per_position)

    for sym, gap_pct, direction in gap_candidates:
        if active_count + new_entries >= MAX_INTRADAY_POSITIONS:
            break
        if sym in state:
            continue
        # Skip if already held as a swing position
        if sym in held_syms and sym not in state:
            continue

        intra = intraday_bars[sym]

        # Compute opening range (first 2 bars of 15-min = 30 min)
        or_bars = min(2, len(intra))
        first = intra.iloc[:or_bars]
        or_high = float(first["high"].max())
        or_low = float(first["low"].min())

        # Must have broken out above opening range
        if not _check_breakout(intra, or_high):
            logger.info(
                "INTRA WAIT  | %s | gap=+%.1f%% | no breakout yet",
                sym, gap_pct * 100,
            )
            continue

        price = float(intra["close"].iloc[-1])
        if price <= 0:
            continue
        qty = max(1, int(alloc / price))

        logger.info(
            "INTRA BUY   | %s | gap=+%.1f%% | qty=%d | $%.2f "
            "| OR: $%.2f-$%.2f",
            sym, gap_pct * 100, qty, price, or_low, or_high,
        )
        place_order(sym, qty, side="buy")
        log_trade(sym, "buy", qty, price,
                  f"intraday_gap(gap={gap_pct*100:.1f}%)")

        state[sym] = {
            "entry_price": price,
            "entry_time": now.isoformat(),
            "qty": qty,
            "gap_pct": gap_pct,
            "or_high": or_high,
            "or_low": or_low,
        }
        _save_state(state)
        new_entries += 1

    logger.info(
        "Intraday sleeve complete | New: %d | Active: %d",
        new_entries, active_count + new_entries,
    )


if __name__ == "__main__":
    run_intraday_sleeve()
