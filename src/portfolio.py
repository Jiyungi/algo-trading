from config import trading_client, CATEGORY_MAP
from datetime import datetime

def show_portfolio():
    account   = trading_client.get_account()
    positions = trading_client.get_all_positions()

    port_val  = float(account.portfolio_value)
    cash      = float(account.cash)
    invested  = port_val - cash
    total_pl  = sum(float(p.unrealized_pl) for p in positions)
    total_pct = (total_pl / (invested - total_pl)) * 100 if invested else 0

    print("=" * 70)
    print(f"  📋 MY PORTFOLIO — {datetime.now().strftime('%B %d, %Y  %H:%M')}")
    print("=" * 70)
    print(f"""
  💼 Total Portfolio Value : ${port_val:>12,.2f}
  📊 Total Invested        : ${invested:>12,.2f}
  💰 Cash Remaining        : ${cash:>12,.2f}
  📈 Total P&L             : ${total_pl:>+12,.2f}  ({total_pct:+.2f}%)
""")

    if not positions:
        print("  No open positions yet.")
        return

    print(f"  {'#':<4} {'Symbol':<10} {'Category':<20} {'Shares':<8} "
          f"{'Avg Cost':<10} {'Price':<10} {'Value':<12} {'P&L $':<12} {'P&L %'}")
    print("  " + "-" * 95)

    for i, p in enumerate(
        sorted(positions, key=lambda p: float(p.market_value), reverse=True), 1
    ):
        cat  = CATEGORY_MAP.get(p.symbol, "📌 Other")
        icon = "🟢" if float(p.unrealized_pl) >= 0 else "🔴"
        print(f"  {i:<4} {p.symbol:<10} {cat:<20} {float(p.qty):<8.2f} "
              f"${float(p.avg_entry_price):<9.2f} ${float(p.current_price):<9.2f} "
              f"${float(p.market_value):<11,.2f} "
              f"{icon} ${float(p.unrealized_pl):<10.2f} "
              f"{float(p.unrealized_plpc)*100:+.2f}%")

    print("  " + "-" * 95)
    best  = max(positions, key=lambda p: float(p.unrealized_plpc))
    worst = min(positions, key=lambda p: float(p.unrealized_plpc))
    print(f"\n  🏆 Best  : {best.symbol} ({float(best.unrealized_plpc)*100:+.2f}%)")
    print(f"  📉 Worst : {worst.symbol} ({float(worst.unrealized_plpc)*100:+.2f}%)")
    print("=" * 70)

if __name__ == "__main__":
    show_portfolio()