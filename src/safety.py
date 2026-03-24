"""Pre-trade safety gates — checked before every order is placed.

portfolio_health_check() → must pass before ANY trading runs
pre_trade_check()        → must pass for each individual order
market_is_open()         → guard against off-hours execution
"""
import logging
from config import trading_client, STOP_LOSS_PCT, DAILY_DROP_PCT

logger = logging.getLogger(__name__)

MAX_POSITION_PCT = 0.10   # no single position > 10% of portfolio value
MAX_NEW_POSITIONS = 2     # max new buys per strategy run
MIN_CASH_PCT = 0.05       # halt if cash < 5% of portfolio


def portfolio_health_check(account, positions) -> tuple[bool, str]:
    """Return (ok, reason). If ok is False, skip all trading today."""
    if getattr(account, "trading_blocked", False):
        return False, "account trading is blocked"
    if getattr(account, "account_blocked", False):
        return False, "account is blocked"

    port_val = float(account.portfolio_value)
    cash = float(account.cash)

    if port_val <= 0:
        return False, "portfolio value is zero"

    if cash / port_val < MIN_CASH_PCT:
        return False, f"cash too low ({cash / port_val:.1%} of portfolio)"

    # Check if portfolio dropped too much today (uses DAILY_DROP_PCT from config.py)
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        history = trading_client.get_portfolio_history(
            GetPortfolioHistoryRequest(period="1D", timeframe="1H")
        )
        if history and history.equity and len(history.equity) >= 2:
            start = history.equity[0]
            end = history.equity[-1]
            if start and start > 0:
                daily_pct = ((end - start) / start) * 100
                if daily_pct <= DAILY_DROP_PCT:
                    return False, f"portfolio down {daily_pct:.1f}% today (limit: {DAILY_DROP_PCT}%)"
    except Exception as e:
        logger.warning("Could not check daily drawdown: %s", e)

    return True, "OK"


def pre_trade_check(symbol: str, qty: float, price: float,
                    portfolio_value: float) -> tuple[bool, str]:
    """Verify an individual order won't over-concentrate the portfolio."""
    if price <= 0 or qty <= 0:
        return False, f"invalid qty={qty} or price={price}"
    trade_value = qty * price
    if portfolio_value > 0 and trade_value / portfolio_value > MAX_POSITION_PCT:
        return False, (
            f"{symbol} trade (${trade_value:.0f}) exceeds "
            f"{MAX_POSITION_PCT:.0%} position limit"
        )
    return True, "OK"


def market_is_open() -> bool:
    """Return True if the US stock market is currently open."""
    try:
        return trading_client.get_clock().is_open
    except Exception as e:
        logger.warning("Could not check market clock: %s", e)
        return False
