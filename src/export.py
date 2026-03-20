import csv
import os
from datetime import datetime
from config import trading_client
from alpaca.trading.requests import (
    GetPortfolioHistoryRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import QueryOrderStatus

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _ts():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_portfolio_history(period="1M", timeframe="1D"):
    """Save portfolio history CSV: timestamp, equity, profitloss,
    profitloss_pct, base_value."""
    _ensure_data_dir()
    history = trading_client.get_portfolio_history(
        GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
    )

    timestamps = history.timestamp or []
    equities = history.equity or []
    profit_losses = history.profit_loss or []
    pl_pcts = history.profit_loss_pct or []
    base_value = history.base_value

    filename = os.path.join(DATA_DIR, f"portfolio_history_{_ts()}.csv")
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "equity", "profitloss",
            "profitloss_pct", "base_value",
        ])
        for i, ts in enumerate(timestamps):
            dt = (
                datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                if ts else ""
            )
            eq = equities[i] if i < len(equities) else ""
            pl = profit_losses[i] if i < len(profit_losses) else ""
            plp = pl_pcts[i] if i < len(pl_pcts) else ""
            bv = (
                base_value[i]
                if isinstance(base_value, list) and i < len(base_value)
                else base_value
            )
            writer.writerow([dt, eq, pl, plp, bv])

    print(f"  ✅ Portfolio history saved ({len(timestamps)} rows) → {filename}")
    return filename


def export_activity():
    """Save trade (fill) activity CSV: activity_type, transaction_time,
    symbol, side, qty, order_id, cum_qty, leaves_qty, order_status."""
    _ensure_data_dir()
    activities = trading_client.get("/account/activities/FILL")

    filename = os.path.join(DATA_DIR, f"activity_{_ts()}.csv")
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "activity_type", "transaction_time", "symbol", "side",
            "qty", "order_id", "cum_qty", "leaves_qty", "order_status",
        ])
        for a in activities:
            writer.writerow([
                a.get("activity_type", ""),
                a.get("transaction_time", ""),
                a.get("symbol", ""),
                a.get("side", ""),
                a.get("qty", ""),
                a.get("order_id", ""),
                a.get("cum_qty", ""),
                a.get("leaves_qty", ""),
                a.get("order_status", ""),
            ])

    print(f"  ✅ Activity saved ({len(activities)} rows) → {filename}")
    return filename


def export_orders(status=QueryOrderStatus.ALL):
    """Save orders CSV: order_id, symbol, side, type, status, qty, notional,
    filled_qty, filled_avg_price, time_in_force, submitted_at, filled_at,
    canceled_at, created_at."""
    _ensure_data_dir()
    orders = trading_client.get_orders(
        GetOrdersRequest(status=status, limit=500)
    )

    filename = os.path.join(DATA_DIR, f"orders_{_ts()}.csv")
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "order_id", "symbol", "side", "type", "status", "qty",
            "notional", "filled_qty", "filled_avg_price", "time_in_force",
            "submitted_at", "filled_at", "canceled_at", "created_at",
        ])
        for o in orders:
            writer.writerow([
                o.id,
                o.symbol,
                o.side.value if o.side else "",
                o.order_type.value if o.order_type else "",
                o.status.value if o.status else "",
                o.qty,
                o.notional,
                o.filled_qty,
                o.filled_avg_price,
                o.time_in_force.value if o.time_in_force else "",
                o.submitted_at,
                o.filled_at,
                o.canceled_at,
                o.created_at,
            ])

    print(f"  ✅ Orders saved ({len(orders)} rows) → {filename}")
    return filename


def export_all():
    """Export portfolio history, activity, and orders CSVs to data/."""
    print("\n  📁 Exporting CSVs to data/ folder...")
    try:
        export_portfolio_history()
    except Exception as e:
        print(f"  ❌ Portfolio history failed: {e}")
    try:
        export_activity()
    except Exception as e:
        print(f"  ❌ Activity failed: {e}")
    try:
        export_orders()
    except Exception as e:
        print(f"  ❌ Orders failed: {e}")
    print()


if __name__ == "__main__":
    export_all()
