from typing import Any, Dict, Optional
from config import trading_client, DRY_RUN
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import logging
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

logger = logging.getLogger(__name__)


def place_order(
    symbol: str,
    qty: int,
    side: str = "buy",
    type: str = "market",
    time_in_force: str = "day",
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Place an order via the Alpaca client or return a dry-run stub.

    The function is intentionally minimal so it can be extended by the
    caller. When DRY_RUN is enabled in the environment this will not
    send a live order.
    """
    payload = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": type,
        "time_in_force": time_in_force
    }

    if DRY_RUN:
        logger.info("DRY_RUN enabled — not placing order: %s", payload)
        return {"status": "dry_run", "order": payload}

    try:
        is_crypto = "/" in symbol
        order = trading_client.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.GTC if is_crypto else TimeInForce.DAY
            )
        )
        logger.info("Order placed: %s", order)
        return {"status": "placed", "order": order}

    except Exception as e:
        logger.error("Failed to place order: %s", e)
        return {"status": "error", "error": str(e)}


def get_account() -> Dict[str, Any]:
    """Return current account details."""
    try:
        account = trading_client.get_account()
        return {
            "portfolio_value": float(account.portfolio_value),
            "cash":            float(account.cash),
            "buying_power":    float(account.buying_power),
            "status":          account.status,
        }
    except Exception as e:
        logger.error("Failed to get account: %s", e)
        return {"status": "error", "error": str(e)}