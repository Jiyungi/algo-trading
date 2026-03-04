from config import trading_client, CATEGORY_MAP, STOP_LOSS_PCT, TAKE_PROFIT_PCT
from datetime import datetime
import time

alerts_sent = set()

def check_and_alert():
    positions = trading_client.get_all_positions()
    account   = trading_client.get_account()
    port_val  = float(account.portfolio_value)
    total_pl  = sum(float(p.unrealized_pl) for p in positions)
    now       = datetime.now().strftime("%H:%M:%S")

    print(f"\n  ── {now} | Portfolio: ${port_val:,.2f} | P&L: ${total_pl:+,.2f} ──")

    for p in positions:
        symbol = p.symbol
        pl_pct = float(p.unrealized_plpc) * 100
        pl_usd = float(p.unrealized_pl)
        cat    = CATEGORY_MAP.get(symbol, "📌 Other")
        icon   = "🟢" if pl_pct >= 0 else "🔴"

        print(f"    {icon} {symbol:<10} {pl_pct:+.2f}%   ${pl_usd:+,.2f}")

        if pl_pct <= STOP_LOSS_PCT and f"{symbol}_stoploss" not in alerts_sent:
            print(f"\n  🚨 STOP LOSS: {symbol} is at {pl_pct:.2f}%! Consider selling!")
            alerts_sent.add(f"{symbol}_stoploss")

        if pl_pct >= TAKE_PROFIT_PCT and f"{symbol}_profit" not in alerts_sent:
            print(f"\n  💰 TAKE PROFIT: {symbol} is at +{pl_pct:.2f}%! Consider locking in gains!")
            alerts_sent.add(f"{symbol}_profit")

def start_monitoring(interval=60, max_checks=480):
    print("=" * 60)
    print("🔔 PORTFOLIO MONITOR STARTED")
    print(f"   Checking every {interval}s | Stop loss: {STOP_LOSS_PCT}% | Take profit: +{TAKE_PROFIT_PCT}%")
    print("   Press Ctrl+C to stop")
    print("=" * 60)

    for i in range(1, max_checks + 1):
        try:
            check_and_alert()
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n🛑 Monitor stopped.")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    start_monitoring()