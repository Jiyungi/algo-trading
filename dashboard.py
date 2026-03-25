"""
Portfolio Dashboard — Flask web server.

Run from the project root:
    python dashboard.py

Then open http://localhost:5000 in your browser.
"""
import sys
import os

# Allow importing modules from src/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import csv
import json
import glob
from datetime import datetime, date

from flask import Flask, jsonify, render_template

app = Flask(__name__, template_folder="templates")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")


# ── Data helpers ───────────────────────────────────────────────────────────────

def _get_alpaca_data():
    """Fetch live portfolio data from Alpaca. Returns dict with 'error' key on failure."""
    try:
        from config import trading_client, CATEGORY_MAP
        account = trading_client.get_account()
        positions = trading_client.get_all_positions()

        port_val = float(account.portfolio_value)
        cash = float(account.cash)
        invested = port_val - cash
        total_pl = sum(float(p.unrealized_pl) for p in positions)
        cost_basis = invested - total_pl
        total_pct = (total_pl / cost_basis * 100) if cost_basis else 0

        positions_data = []
        for p in sorted(positions, key=lambda x: float(x.market_value), reverse=True):
            positions_data.append({
                "symbol": p.symbol,
                "category": CATEGORY_MAP.get(p.symbol, "Other"),
                "qty": float(p.qty),
                "avg_cost": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
            })

        return {
            "portfolio_value": port_val,
            "cash": cash,
            "invested": invested,
            "total_pl": total_pl,
            "total_pl_pct": total_pct,
            "positions": positions_data,
        }
    except Exception as e:
        return {"error": str(e), "positions": []}


def _get_position_state():
    path = os.path.join(DATA_DIR, "positions_state.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_cooldowns():
    path = os.path.join(DATA_DIR, "cooldowns.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _get_trade_log(n=100):
    path = os.path.join(DATA_DIR, "trade_log.csv")
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        return rows[-n:][::-1]  # newest first
    except Exception:
        return []


def _get_portfolio_history():
    """Combine all portfolio_history_*.csv files into a deduplicated, sorted list."""
    files = sorted(glob.glob(os.path.join(DATA_DIR, "portfolio_history_*.csv")))
    rows_by_date = {}
    for filepath in files:
        try:
            with open(filepath, newline="") as f:
                for row in csv.DictReader(f):
                    ts = row.get("timestamp") or row.get("date") or ""
                    day = ts[:10]
                    if day:
                        rows_by_date[day] = {
                            "date": day,
                            "equity": float(row.get("equity", 0)),
                            "pl": float(row.get("profit_loss", 0)),
                            "pl_pct": float(row.get("profit_loss_pct", 0)),
                        }
        except Exception:
            continue
    return sorted(rows_by_date.values(), key=lambda r: r["date"])


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def api_portfolio():
    data = _get_alpaca_data()
    pos_state = _get_position_state()
    for p in data.get("positions", []):
        state = pos_state.get(p["symbol"], {})
        p["peak_price"] = state.get("peak_price")
        p["tranches_taken"] = state.get("tranches_taken", 0)
    return jsonify(data)


@app.route("/api/history")
def api_history():
    return jsonify(_get_portfolio_history())


@app.route("/api/trades")
def api_trades():
    return jsonify(_get_trade_log(100))


@app.route("/api/status")
def api_status():
    try:
        from trade_log import circuit_breaker_ok, get_win_rate
        ok, reason = circuit_breaker_ok()
        win_rate = get_win_rate()
    except Exception as e:
        ok, reason, win_rate = True, str(e), None

    cooldowns = _get_cooldowns()
    today = date.today().isoformat()
    active_cooldowns = {}
    for sym, val in cooldowns.items():
        expiry = None
        if isinstance(val, str):
            expiry = val
        elif isinstance(val, dict):
            expiry = val.get("expiry") or val.get("exp")
        if expiry and expiry > today:
            active_cooldowns[sym] = val

    return jsonify({
        "circuit_breaker_ok": ok,
        "circuit_breaker_reason": reason,
        "win_rate": win_rate,
        "cooldowns": active_cooldowns,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Dashboard running at http://localhost:{port}\n")
    app.run(debug=True, port=port)
