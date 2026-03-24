"""Tracks per-position state for trailing stop, tiered take-profit, and
time-based exits.

Stored in data/positions_state.json and committed back to the repo by
GitHub Actions after each run, so state persists across daily executions.

State per symbol:
  peak_price     -- highest price seen since entry; trailing stop anchors here
  tranches_taken -- 0 or 1 (how many profit tranches have been sold)
  entry_date     -- ISO date string; used for max holding period exit
"""
import json
import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")

MAX_HOLD_DAYS = 7   # force exit after this many trading days


def _load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _trading_days_since(entry_date_str: str) -> int:
    """Approximate trading days elapsed since entry_date (Mon-Fri only)."""
    try:
        entry = date.fromisoformat(entry_date_str)
    except (ValueError, TypeError):
        return 0
    total = 0
    cursor = entry
    today = date.today()
    while cursor < today:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Mon-Fri
            total += 1
    return total


def init_state(symbol: str, entry_price: float):
    """Call when a new position is opened."""
    state = _load()
    state[symbol] = {
        "peak_price": entry_price,
        "tranches_taken": 0,
        "entry_date": date.today().isoformat(),
    }
    _save(state)
    logger.info("State init: %s | entry $%.2f", symbol, entry_price)


def ensure_initialized(
    symbol: str, current_price: float,
    entry_price: float, pl_pct: float,
):
    """Bootstrap state for positions that predate this strategy.

    Called at the start of the exit loop for every held position.
    If state already exists, does nothing.

    Sets tranches based on current P&L so the strategy doesn't immediately
    fire partial sells on gains that existed before it started managing:
      pl >= +18% -> tranches = 1  (tranche already 'taken', ride trailing stop)
      otherwise  -> tranches = 0  (manage from scratch)

    Entry date is set to today, giving the position a fresh MAX_HOLD_DAYS
    window rather than immediately triggering a time-based exit.
    """
    state = _load()
    if symbol in state:
        return

    tranches = 1 if pl_pct >= 18.0 else 0
    state[symbol] = {
        "peak_price": current_price,
        "tranches_taken": tranches,
        "entry_date": date.today().isoformat(),
    }
    _save(state)
    logger.info(
        "Bootstrap: %s | pl=%.1f%% | tranches=%d | peak=$%.2f",
        symbol, pl_pct, tranches, current_price,
    )


def update_peak(symbol: str, current_price: float,
                entry_price: float = None) -> float:
    """Ratchet peak price upward. Returns the (possibly updated) peak."""
    state = _load()
    if symbol not in state:
        initial = entry_price if entry_price else current_price
        state[symbol] = {
            "peak_price": initial,
            "tranches_taken": 0,
            "entry_date": date.today().isoformat(),
        }
    else:
        state[symbol]["peak_price"] = max(
            state[symbol].get("peak_price", current_price),
            current_price,
        )
    _save(state)
    return state[symbol]["peak_price"]


def get_tranches(symbol: str) -> int:
    """Return how many profit tranches have been taken (0 or 1)."""
    return _load().get(symbol, {}).get("tranches_taken", 0)


def get_days_held(symbol: str) -> int:
    """Return approximate trading days held since entry."""
    entry_date = _load().get(symbol, {}).get("entry_date")
    return _trading_days_since(entry_date) if entry_date else 0


def mark_tranche(symbol: str, n: int):
    """Record that tranche N has been sold."""
    state = _load()
    if symbol not in state:
        state[symbol] = {
            "peak_price": 0,
            "tranches_taken": n,
            "entry_date": date.today().isoformat(),
        }
    else:
        state[symbol]["tranches_taken"] = n
    _save(state)


def clear_state(symbol: str):
    """Remove a symbol's state when the position is fully closed."""
    state = _load()
    state.pop(symbol, None)
    _save(state)


def cleanup_closed(held_symbols: set):
    """Remove stale state for symbols no longer in the portfolio."""
    state = _load()
    stale = [s for s in state if s not in held_symbols]
    for s in stale:
        del state[s]
    if stale:
        _save(state)
        logger.info("Removed stale state for: %s", ", ".join(stale))
