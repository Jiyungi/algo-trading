import os
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient

load_dotenv()

# API Keys
API_KEY    = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_SECRET")
DRY_RUN    = os.getenv("DRY_RUN", "0") in ("1", "true", "True")

if not API_KEY or not SECRET_KEY:
    raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")

# Shared clients
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=True)
stock_data     = StockHistoricalDataClient(API_KEY, SECRET_KEY)

# Category map
CATEGORY_MAP = {
    "AAPL":    "🇺🇸 US Stock",
    "MSFT":    "🇺🇸 US Stock",
    "GOOGL":   "🇺🇸 US Stock",
    "JNJ":     "🏥 Healthcare",
    "XOM":     "⚡ Energy",
    "JPM":     "🏦 Finance",
    "SPY":     "📦 Broad ETF",
    "QQQ":     "💻 Tech ETF",
    "VEU":     "🌍 Intl ETF",
    "VNQ":     "🏘️ Real Estate",
    "GLD":     "🥇 Gold",
    "BND":     "🛡️ Bonds",
    "BTCUSD":  "₿ Crypto",
    "ETHUSD":  "Ξ Crypto",
    "EWY":     "🇰🇷 Korean ETF"
}

# Alert thresholds
STOP_LOSS_PCT   = -7.0
TAKE_PROFIT_PCT = +10.0
DAILY_DROP_PCT  = -3.0