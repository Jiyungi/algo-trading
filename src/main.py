from portfolio import show_portfolio
from orders import place_order
from monitor import start_monitoring
from weekly_report import weekly_report
from export import export_all
from config import trading_client, stock_data, DRY_RUN
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.data.requests import StockLatestQuoteRequest
import re
import json
import time

print(f"""
╔══════════════════════════════════════╗
║     🏦 ALPACA PORTFOLIO MANAGER      ║
║     Mode: {'🧪 DRY RUN' if DRY_RUN else '💰 LIVE PAPER'}              ║
╠══════════════════════════════════════╣
║  1 → View Portfolio                 ║
║  2 → Buy a Stock                    ║
║  3 → Sell a Stock                   ║
║  4 → Check a Price                  ║
║  5 → Start Monitor (alarm system)   ║
║  6 → Weekly Report                  ║
║  7 → Check My Orders                ║
║  8 → Export CSVs                    ║
║  q → Quit                           ║
╚══════════════════════════════════════╝
""")

def is_crypto_symbol(sym):
    crypto_quote_currencies = ("USD", "USDT", "BTC", "ETH")
    return "/" in sym or any(sym.endswith(c) for c in crypto_quote_currencies)


def symbol_candidates(sym):
    normalized = re.sub(r"[^A-Z0-9]", "", sym.upper())
    candidates = [sym.upper().strip()]
    if normalized and normalized not in candidates:
        candidates.append(normalized)
    return candidates


def find_open_position(sym):
    """Try original and normalized symbol forms, then scan all positions."""
    last_err = None
    for candidate in symbol_candidates(sym):
        try:
            position = trading_client.get_open_position(candidate)
            return position, candidate
        except Exception as e:
            last_err = e

    try:
        positions = trading_client.get_all_positions()
        target_set = set(symbol_candidates(sym))
        for p in positions:
            pos_candidates = set(symbol_candidates(p.symbol))
            if target_set & pos_candidates:
                return p, p.symbol
    except Exception:
        pass

    if last_err:
        raise last_err
    raise RuntimeError(f"No open position found for symbol: {sym}")


def parse_error_payload(exc):
    raw = str(exc).strip()
    try:
        if raw.startswith("{") and raw.endswith("}"):
            return json.loads(raw)
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start:end + 1])
    except Exception:
        return None
    return None


while True:
    choice = input("  Enter choice: ").strip().lower()

    if choice == "1":
        show_portfolio()

    elif choice == "2":
        sym = input("  Symbol to buy (e.g. AAPL): ").upper().strip()
        qty = float(input("  How many shares: "))
        if DRY_RUN:
            print(f"  🧪 DRY RUN — would buy {qty} shares of {sym}")
        else:
            try:
                tif = TimeInForce.GTC if is_crypto_symbol(sym) else TimeInForce.DAY
                order = trading_client.submit_order(
                    MarketOrderRequest(
                        symbol=sym,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=tif
                    )
                )
                print(f"  ✅ Bought {qty} {sym} | Status: {order.status}")
            except Exception as e:
                print(f"  ❌ Failed: {e}")

    elif choice == "3":
        sym = input("  Symbol to sell (e.g. JPM): ").upper().strip()
        if DRY_RUN:
            print(f"  🧪 DRY RUN — would sell all shares of {sym}")
        else:
            try:
                position, resolved_symbol = find_open_position(sym)
                tif = TimeInForce.GTC if is_crypto_symbol(resolved_symbol) else TimeInForce.DAY
                order = trading_client.submit_order(
                    MarketOrderRequest(
                        symbol=resolved_symbol,
                        qty=float(position.qty),
                        side=OrderSide.SELL,
                        time_in_force=tif
                    )
                )
                print(f"  ✅ Sold {position.qty} {resolved_symbol} | Status: {order.status}")
            except Exception as e:
                print(f"  ❌ Failed to sell {sym}: {e}")
                payload = parse_error_payload(e)
                if payload and payload.get("code") == 40310000:
                    held = payload.get("held_for_orders", "0")
                    related_orders = payload.get("related_orders") or []
                    print(f"  ℹ️ Shares are reserved by open order(s). held_for_orders={held}")
                    if related_orders:
                        print(f"  ℹ️ Related order id(s): {', '.join(related_orders)}")
                        answer = input("  Cancel related order(s) and retry sell? [y/N]: ").strip().lower()
                        if answer == "y":
                            for oid in related_orders:
                                try:
                                    trading_client.cancel_order_by_id(oid)
                                    print(f"  ✅ Canceled order {oid}")
                                except Exception as cancel_err:
                                    print(f"  ❌ Failed to cancel {oid}: {cancel_err}")
                            time.sleep(1.0)
                            try:
                                position, resolved_symbol = find_open_position(sym)
                                tif = TimeInForce.GTC if is_crypto_symbol(resolved_symbol) else TimeInForce.DAY
                                order = trading_client.submit_order(
                                    MarketOrderRequest(
                                        symbol=resolved_symbol,
                                        qty=float(position.qty),
                                        side=OrderSide.SELL,
                                        time_in_force=tif
                                    )
                                )
                                print(f"  ✅ Sold {position.qty} {resolved_symbol} | Status: {order.status}")
                            except Exception as retry_err:
                                print(f"  ❌ Retry failed: {retry_err}")
                try:
                    positions = trading_client.get_all_positions()
                    if positions:
                        print("  ℹ️ Open position symbols:", ", ".join(p.symbol for p in positions))
                    else:
                        print("  ℹ️ You currently have no open positions to sell.")
                except Exception:
                    pass

    elif choice == "4":
        sym = input("  Symbol to check (e.g. EWY): ").upper().strip()
        try:
            quote = stock_data.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=sym)
            )
            ask = float(quote[sym].ask_price)
            bid = float(quote[sym].bid_price)
            print(f"  💲 {sym}: Bid ${bid:.2f} | Ask ${ask:.2f}")
        except Exception as e:
            print(f"  ❌ Failed: {e}")

    elif choice == "5":
        start_monitoring()

    elif choice == "6":
        weekly_report()

    elif choice == "7":
        print("\n  Filter by status:")
        print("  [1] All  [2] Open  [3] Closed")
        status_choice = input("  Choose: ").strip()

        status_map = {
            "1": QueryOrderStatus.ALL,
            "2": QueryOrderStatus.OPEN,
            "3": QueryOrderStatus.CLOSED,
        }
        status = status_map.get(status_choice, QueryOrderStatus.ALL)

        try:
            orders = trading_client.get_orders(
                GetOrdersRequest(status=status, limit=20)
            )
            if not orders:
                print("  📭 No orders found.")
            else:
                print(f"\n  {'#':<4} {'Symbol':<8} {'Side':<6} {'Qty':<8} {'Type':<8} {'Status':<12} {'Submitted'}")
                print("  " + "─" * 70)
                for i, o in enumerate(orders, 1):
                    submitted = o.submitted_at.strftime("%Y-%m-%d %H:%M") if o.submitted_at else "—"
                    print(f"  {i:<4} {o.symbol:<8} {o.side.value:<6} {float(o.qty):<8.2f} {o.order_type.value:<8} {o.status.value:<12} {submitted}")
                print()
        except Exception as e:
            print(f"  ❌ Failed to fetch orders: {e}")

    elif choice == "8":
        export_all()

    elif choice == "q":
        print("  👋 Goodbye!")
        break

    else:
        print("  ❌ Invalid choice, try again.")
