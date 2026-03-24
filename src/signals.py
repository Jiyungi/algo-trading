"""Signal computation — pure math, zero API calls.

Each function returns:  +1 (buy), -1 (sell), 0 (hold)
compute_score() combines all four into a confluence score (-4 to +4).
"""
import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def ma_signal(closes: pd.Series, fast: int = 10, slow: int = 50) -> int:
    """EMA crossover: fast above slow = uptrend (buy), below = downtrend (sell)."""
    if len(closes) < slow:
        return 0
    fast_val = _ema(closes, fast).iloc[-1]
    slow_val = _ema(closes, slow).iloc[-1]
    if fast_val > slow_val:
        return 1
    if fast_val < slow_val:
        return -1
    return 0


def rsi_signal(closes: pd.Series, period: int = 14,
               oversold: int = 35, overbought: int = 65) -> int:
    """RSI: below oversold threshold = buy, above overbought = sell."""
    if len(closes) < period + 1:
        return 0
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    if pd.isna(rsi):
        return 0
    if rsi < oversold:
        return 1
    if rsi > overbought:
        return -1
    return 0


def macd_signal(closes: pd.Series) -> int:
    """MACD line above signal line = bullish momentum (buy), below = bearish (sell)."""
    if len(closes) < 35:
        return 0
    macd_line = _ema(closes, 12) - _ema(closes, 26)
    signal_line = _ema(macd_line, 9)
    if macd_line.iloc[-1] > signal_line.iloc[-1]:
        return 1
    if macd_line.iloc[-1] < signal_line.iloc[-1]:
        return -1
    return 0


def volume_signal(closes: pd.Series, volumes: pd.Series, window: int = 20) -> int:
    """High volume + rising price = accumulation (buy). High volume + falling = distribution (sell).
    Returns 0 if volume is not significantly above average."""
    if len(closes) < window + 1 or len(volumes) < window + 1:
        return 0
    avg_vol = volumes.iloc[-(window + 1):-1].mean()
    if avg_vol == 0:
        return 0
    high_volume = volumes.iloc[-1] > avg_vol * 1.5
    if not high_volume:
        return 0
    return 1 if closes.iloc[-1] > closes.iloc[-2] else -1


def compute_score(closes: pd.Series, volumes: pd.Series) -> int:
    """Confluence score from -4 (strong sell) to +4 (strong buy).
    Combines MA crossover + RSI + MACD + volume confirmation."""
    return (
        ma_signal(closes)
        + rsi_signal(closes)
        + macd_signal(closes)
        + volume_signal(closes, volumes)
    )
