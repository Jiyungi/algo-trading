from config import trading_client, CATEGORY_MAP
from alpaca.trading.requests import GetPortfolioHistoryRequest
from datetime import datetime

def weekly_report():
    account   = trading_client.get_account()
    positions = trading_client.get_all_positions()
    port_val  = float(account.portfolio_value)
    cash      = float(account.cash)
    total_pl  = sum(float(p.unrealized_pl) for p in positions)
    winners   = [p for p in positions if float(p.unrealized_pl) > 0]
    losers    = [p for p in positions if float(p.unrealized_pl) < 0]
    best      = max(positions, key=lambda p: float(p.unrealized_plpc), default=None)
    worst     = min(positions, key=lambda p: float(p.unrealized_plpc), default=None)

    print("=" * 65)
    print(f"  📅 WEEKLY REPORT — {datetime.now().strftime('%A, %B %d %Y')}")
    print("=" * 65)
    print(f"""
  💼 Portfolio Value  : ${port_val:>12,.2f}
  💰 Cash Remaining   : ${cash:>12,.2f}
  📈 Total P&L        : ${total_pl:>+12,.2f}
  🟢 Winners          : {len(winners)}
  🔴 Losers           : {len(losers)}
""")

    if best:
        print(f"  🏆 Best  : {best.symbol} {float(best.unrealized_plpc)*100:+.2f}%")
    if worst:
        print(f"  📉 Worst : {worst.symbol} {float(worst.unrealized_plpc)*100:+.2f}%")

    try:
        history = trading_client.get_portfolio_history(
            GetPortfolioHistoryRequest(period="1W", timeframe="1D")
        )
        if history and history.equity and len(history.equity) >= 2:
            start = history.equity[0]
            end   = history.equity[-1]
            chg   = end - start
            pct   = ((end - start) / start) * 100 if start else 0
            icon  = "🟢" if chg >= 0 else "🔴"
            print(f"\n  📆 Weekly Change: {icon} ${chg:+,.2f} ({pct:+.2f}%)")
    except Exception as e:
        print(f"\n  Could not fetch weekly history: {e}")

    print("=" * 65)

if __name__ == "__main__":
    weekly_report()