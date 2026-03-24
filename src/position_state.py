"""Tracks per-position state for trailing stop and tiered take-profit.

Stored in data/positions_state.json and committed back to the repo by
GitHub Actions after each run, so state persists across daily executions.

State per symbol:
  peak_price     -- highest price seen since entry; trailing stop anchors here
  tranches_taken -- 0, 1, or 2 (how many profit tranches have been sold)
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")


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


def init_state(symbol: str, entry_price: float):
    """Call when a new position is opened."""
    state = _load()
    state[symbol] = {"peak_price": entry_price, "tranches_taken": 0}
    _save(state)
    logger.info("State init: %s | entry $%.2f", symbol, entry_price)


def update_peak(symbol: str, current_price: float,
                entry_price: float = None) -> float:
    """Ratchet peak price upward. Returns the (possibly updated) peak.
    If no state exists yet (position predates this system), bootstraps
    from entry_price so the trailing stop starts at the right level.
    """
    state = _load()
    if symbol not in state:
        initial = entry_price if entry_price else current_price
        state[symbol] = {"peak_price": initial, "tranches_taken": 0}
    else:
        state[symbol]["peak_price"] = max(
            state[symbol].get("peak_price", current_price),
            current_price,
        )
    _save(state)
    return state[symbol]["peak_price"]


def get_tranches(symbol: str) -> int:
    """Return how many profit tranches have been taken (0, 1, or 2)."""
    return _load().get(symbol, {}).get("tranches_taken", 0)


def mark_tranche(symbol: str, n: int):
    """Record that tranche N has been sold."""
    state = _load()
    if symbol not in state:
        state[symbol] = {"peak_price": 0, "tranches_taken": n}
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
