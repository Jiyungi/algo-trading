"""Trade log — records every order, tracks cooldowns and win rate.

Files written to data/ (committed back to repo by the GitHub Actions workflow):
  data/trade_log.csv   — full history of every buy and sell
  data/cooldowns.json  — symbols blocked from re-entry after a stop-loss

Circuit breaker: if the last 10 closed trades have a win rate below 40%,
the strategy skips new buys until performance recovers.

Cooldown: any symbol that exits via stop-loss is blocked for 5 days.
This prevents the strategy from immediately re-buying a falling stock.
"""
import csv
import json
import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
TRADE_LOG_PATH = os.path.join(DATA_DIR, "trade_log.csv")
COOLDOWN_PATH = os.path.join(DATA_DIR, "cooldowns.json")

COOLDOWN_DAYS = 5               # days to block a symbol after stop-loss exit
CIRCUIT_BREAKER_N = 10          # number of recent closed trades to evaluate
CIRCUIT_BREAKER_MIN_WIN_RATE = 0.40  # halt new buys if win rate drops below this

FIELDNAMES = ["date", "symbol", "side", "qty", "price", "reason", "pnl_pct"]


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ── Trade logging ─────────────────────────────────────────────────────────────

def log_trade(symbol: str, side: str, qty: float, price: float,
              reason: str, pnl_pct: float = None):
    """Append one trade record to trade_log.csv."""
    _ensure_data_dir()
    file_exists = os.path.exists(TRADE_LOG_PATH)
    with open(TRADE_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "date":    datetime.now().strftime("%Y-%m-%d %H:%M"),
            "symbol":  symbol,
            "side":    side,
            "qty":     qty,
            "price":   f"{price:.2f}",
            "reason":  reason,
            "pnl_pct": f"{pnl_pct:.2f}" if pnl_pct is not None else "",
        })


def load_recent_trades(n: int = 20) -> list:
    """Return the last N rows from trade_log.csv as a list of dicts."""
    if not os.path.exists(TRADE_LOG_PATH):
        return []
    with open(TRADE_LOG_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]


# ── Circuit breaker ───────────────────────────────────────────────────────────

def get_win_rate(n: int = CIRCUIT_BREAKER_N) -> float | None:
    """Win rate of the last N closed (sell) trades. None if not enough history."""
    closed = [
        t for t in load_recent_trades(n * 3)
        if t["side"] == "sell" and t["pnl_pct"]
    ][-n:]
    if len(closed) < n:
        return None
    wins = sum(1 for t in closed if float(t["pnl_pct"]) > 0)
    return wins / len(closed)


def circuit_breaker_ok() -> tuple[bool, str]:
    """Return (ok, reason).
    Halts new buys when the recent win rate falls below the minimum threshold.
    Does NOT block exits — stop-losses and take-profits still fire.
    """
    win_rate = get_win_rate()
    if win_rate is None:
        return True, "not enough trade history yet"
    if win_rate < CIRCUIT_BREAKER_MIN_WIN_RATE:
        return False, (
            f"win rate {win_rate:.0%} is below "
            f"{CIRCUIT_BREAKER_MIN_WIN_RATE:.0%} — pausing new buys"
        )
    return True, f"win rate {win_rate:.0%} OK"


# ── Cooldowns ─────────────────────────────────────────────────────────────────

def _load_cooldowns() -> dict:
    if not os.path.exists(COOLDOWN_PATH):
        return {}
    try:
        with open(COOLDOWN_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cooldowns(cooldowns: dict):
    _ensure_data_dir()
    with open(COOLDOWN_PATH, "w") as f:
        json.dump(cooldowns, f, indent=2)


def add_cooldown(symbol: str, days: int = COOLDOWN_DAYS):
    """Block a symbol from being bought again for N days."""
    cooldowns = _load_cooldowns()
    expiry = (date.today() + timedelta(days=days)).isoformat()
    cooldowns[symbol] = expiry
    _save_cooldowns(cooldowns)
    logger.info("Cooldown: %s blocked until %s", symbol, expiry)


def is_on_cooldown(symbol: str) -> bool:
    """Return True if the symbol is still within its cooldown window."""
    cooldowns = _load_cooldowns()
    if symbol not in cooldowns:
        return False
    expiry = date.fromisoformat(cooldowns[symbol])
    if date.today() >= expiry:
        # Expired — remove it
        del cooldowns[symbol]
        _save_cooldowns(cooldowns)
        return False
    return True
