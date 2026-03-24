"""Universe scanner using yfinance — free, no API key required.

Scans ~100 liquid US stocks and ETFs daily.
Returns the same {symbol: DataFrame} format used by signals.py.

Why yfinance instead of Alpaca for this step:
- No rate limits for reasonable use
- No cost regardless of number of symbols
- Alpaca is reserved only for placing orders
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 90  # need ≥50 days for the slow EMA signal

# ~100 liquid US stocks + ETFs covering broad market opportunity.
# Stable list — these are all large-cap or high-volume names.
UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AVGO", "ORCL", "CRM", "ADBE", "AMD", "QCOM", "TXN",
    # Finance
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "V", "MA", "AXP",
    # Healthcare
    "JNJ", "UNH", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT",
    # Consumer
    "HD", "MCD", "NKE", "SBUX", "COST", "WMT", "PG", "KO", "PEP",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG",
    # Industrial
    "CAT", "DE", "HON", "GE", "RTX", "LMT", "BA",
    # Broad market ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP",
    # Defensive / alternative
    "GLD", "SLV", "BND", "TLT",
    # International
    "EWY", "VEU", "EEM",
]

# Deduplicate while preserving order
UNIVERSE = list(dict.fromkeys(UNIVERSE))


def fetch_bars_yf(symbols: list, lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Fetch daily OHLCV bars for all symbols in one yfinance call.

    Returns {symbol: DataFrame} with lowercase columns:
        open, high, low, close, volume
    Symbols with fewer than 30 bars are dropped (not enough for signals).
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — run: pip install yfinance")
        return {}

    logger.info("Scanning %d symbols via yfinance (%d days)...", len(symbols), lookback_days)

    try:
        raw = yf.download(
            tickers=symbols,
            period=f"{lookback_days}d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error("yfinance download failed: %s", e)
        return {}

    result = {}
    for sym in symbols:
        try:
            # yfinance returns a flat DataFrame when only one symbol is given
            df = raw.copy() if len(symbols) == 1 else raw[sym].copy()
            df = df.dropna(subset=["Close"])
            if len(df) < 30:
                continue
            df = df.rename(columns={
                "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            result[sym] = df[["open", "high", "low", "close", "volume"]]
        except (KeyError, Exception):
            pass

    logger.info("Received data for %d / %d symbols", len(result), len(symbols))
    return result
