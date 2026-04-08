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
CIRCUIT_BREAKER_MIN_WIN_RATE = 0.25  # lowered from 0.40 for final week

FIELDNAMES = ["date", "symbol", "side", "qty", "price", "reason", "pnl_pct"]


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ── Trade logging ────────────────────────────────────────────────────────────

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


# ── Circuit breaker ──────────────────────────────────────────────────────────

def get_win_rate(n: int = CIRCUIT_BREAKER_N) -> float | None:
    """Win rate of last N closed (sell) trades. None if not enough history."""
    closed = [
        t for t in load_recent_trades(n * 3)
        if t["side"] == "sell" and t["pnl_pct"]
    ][-n:]
    if len(closed) < n:
        return None
    wins = sum(1 for t in closed if float(t["pnl_pct"]) > 0)
    return wins / len(closed)


def _get_payoff(n: int = CIRCUIT_BREAKER_N) -> tuple[float, float]:
    """Return (avg_gain_pct, avg_loss_pct) from last N closed trades."""
    closed = [
        t for t in load_recent_trades(n * 3)
        if t["side"] == "sell" and t["pnl_pct"]
    ][-n:]
    gains = [float(t["pnl_pct"]) for t in closed if float(t["pnl_pct"]) > 0]
    losses = [abs(float(t["pnl_pct"])) for t in closed
              if float(t["pnl_pct"]) < 0]
    avg_gain = sum(gains) / len(gains) if gains else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return avg_gain, avg_loss


def circuit_breaker_ok() -> tuple[bool, str]:
    """Return (ok, reason).

    Halts new buys only when BOTH conditions hold:
      1. Win rate < 40%  (losing more than 60% of trades)
      2. Avg loss > avg gain  (losses outweigh wins in size too)

    Requiring both prevents premature shutdown from small-sample noise
    where a few losses happen to cluster but the payoff ratio is still fine.
    Does NOT block exits — stop-losses and take-profits always fire.
    """
    win_rate = get_win_rate()
    if win_rate is None:
        return True, "not enough trade history yet"

    avg_gain, avg_loss = _get_payoff()
    if win_rate < CIRCUIT_BREAKER_MIN_WIN_RATE and avg_loss > avg_gain:
        return False, (
            f"win rate {win_rate:.0%} < {CIRCUIT_BREAKER_MIN_WIN_RATE:.0%}"
            f" AND avg loss {avg_loss:.1f}% > avg gain {avg_gain:.1f}%"
        )
    return True, (
        f"win rate {win_rate:.0%} | "
        f"avg gain {avg_gain:.1f}% | avg loss {avg_loss:.1f}%"
    )


# ── Cooldowns ────────────────────────────────────────────────────────────────

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


def add_cooldown(symbol: str, days: int = COOLDOWN_DAYS,
                 stop_price: float = None):
    """Block a symbol from being bought again for N days.

    stop_price is recorded so can_override_cooldown() can check
    whether the price has recovered above the stop level.
    """
    cooldowns = _load_cooldowns()
    expiry = (date.today() + timedelta(days=days)).isoformat()
    cooldowns[symbol] = {"expiry": expiry, "stop_price": stop_price}
    _save_cooldowns(cooldowns)
    logger.info("Cooldown: %s blocked until %s", symbol, expiry)


def _cooldown_expiry(entry) -> date:
    """Return expiry date from a cooldown entry (str or dict)."""
    expiry_str = entry if isinstance(entry, str) else entry["expiry"]
    return date.fromisoformat(expiry_str)


def is_on_cooldown(symbol: str) -> bool:
    """Return True if the symbol is still within its cooldown window."""
    cooldowns = _load_cooldowns()
    if symbol not in cooldowns:
        return False
    expiry = _cooldown_expiry(cooldowns[symbol])
    if date.today() >= expiry:
        del cooldowns[symbol]
        _save_cooldowns(cooldowns)
        return False
    return True


def can_override_cooldown(symbol: str, score: int,
                          current_price: float) -> bool:
    """Allow re-entry despite cooldown when score>=4 and price recovered.

    Price is considered recovered when it is above the stop_price that
    was recorded when the cooldown was set (V-shaped recovery).
    Requires score >= 4 (highest conviction only).
    """
    if score < 4:
        return False
    cooldowns = _load_cooldowns()
    if symbol not in cooldowns:
        return False
    entry = cooldowns[symbol]
    if isinstance(entry, str):
        return False  # old format — no stop_price stored
    stop_price = entry.get("stop_price")
    if stop_price is None:
        return False
    return current_price > stop_price
