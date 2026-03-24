"""Signal computation — pure math, zero API calls.

Each function returns:  +1 (buy), -1 (sell), 0 (hold)
compute_score() combines signals into a confluence score.

Signals (each ±1):
  EMA 5/20 crossover, RSI(7) regime-aware, MACD, volume, acceleration
Bonuses applied inside compute_score():
  +1 trend alignment (EMA and MACD both bullish)
Bonuses applied in strategy.py:
  +1 catalyst (gap >2% or volume spike)
"""
import numpy as np
import pandas as pd

REGIME_TREND = "trend"
REGIME_MEAN_REV = "mean_reversion"


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ── Regime detection ─────────────────────────────────────────────────────────

def detect_regime(spy_bars: pd.DataFrame, window: int = 20) -> str:
    """Return REGIME_TREND or REGIME_MEAN_REV based on SPY vs its EMA.

    Above 20-day EMA → market is in an uptrend → use momentum logic.
    Below 20-day EMA → market is choppy/falling → use mean-reversion logic.
    Defaults to trend if not enough data.
    """
    if spy_bars is None or len(spy_bars) < window:
        return REGIME_TREND
    ema20 = _ema(spy_bars["close"], window).iloc[-1]
    current = spy_bars["close"].iloc[-1]
    return REGIME_TREND if current > ema20 else REGIME_MEAN_REV


# ── Individual signals ───────────────────────────────────────────────────────

def ma_signal(closes: pd.Series, fast: int = 5, slow: int = 20) -> int:
    """EMA 5/20 crossover.
    Faster than the original 10/50 — earlier entries, suits 1-month horizon.
    """
    if len(closes) < slow:
        return 0
    fast_val = _ema(closes, fast).iloc[-1]
    slow_val = _ema(closes, slow).iloc[-1]
    if fast_val > slow_val:
        return 1
    if fast_val < slow_val:
        return -1
    return 0


def rsi_signal(closes: pd.Series, period: int = 7,
               regime: str = REGIME_TREND) -> int:
    """Regime-aware RSI signal (period=7 for faster reaction).

    Trend regime:
      RSI > 60 = bullish confirmation (+1)  -- strong trends stay overbought
      RSI < 40 = bearish confirmation (-1)

    Mean-reversion regime:
      RSI < 30 = oversold, expect bounce (+1)
      RSI > 70 = overbought, expect pullback (-1)

    Removes the contradiction where RSI penalised strong uptrends.
    """
    if len(closes) < period + 1:
        return 0
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    if pd.isna(rsi):
        return 0

    if regime == REGIME_TREND:
        if rsi > 60:
            return 1
        if rsi < 40:
            return -1
    else:  # mean_reversion
        if rsi < 30:
            return 1
        if rsi > 70:
            return -1
    return 0


def macd_signal(closes: pd.Series) -> int:
    """MACD line above signal line = bullish (buy), below = bearish (sell)."""
    if len(closes) < 35:
        return 0
    macd_line = _ema(closes, 12) - _ema(closes, 26)
    signal_line = _ema(macd_line, 9)
    if macd_line.iloc[-1] > signal_line.iloc[-1]:
        return 1
    if macd_line.iloc[-1] < signal_line.iloc[-1]:
        return -1
    return 0


def volume_signal(closes: pd.Series, volumes: pd.Series,
                  window: int = 20) -> int:
    """High volume + rising price = accumulation (+1).
    High volume + falling price = distribution (-1).
    Returns 0 if volume is not significantly above average.
    """
    if len(closes) < window + 1 or len(volumes) < window + 1:
        return 0
    avg_vol = volumes.iloc[-(window + 1):-1].mean()
    if avg_vol == 0:
        return 0
    if volumes.iloc[-1] <= avg_vol * 1.5:
        return 0
    return 1 if closes.iloc[-1] > closes.iloc[-2] else -1


def acceleration_signal(closes: pd.Series, lookback: int = 2,
                        threshold: float = 0.03) -> int:
    """2-day return acceleration: >+3% → +1 (momentum), <-3% → -1 (fade).

    Captures short-term continuation moves earlier than slower signals.
    """
    if len(closes) < lookback + 1:
        return 0
    past = closes.iloc[-1 - lookback]
    if past <= 0:
        return 0
    ret = (closes.iloc[-1] - past) / past
    if ret > threshold:
        return 1
    if ret < -threshold:
        return -1
    return 0


def ema(closes: pd.Series, span: int) -> float:
    """Return the latest EMA value for the given span."""
    return _ema(closes, span).iloc[-1]


def has_catalyst(df: pd.DataFrame, gap_threshold: float = 0.02) -> bool:
    """Return True if today has a meaningful catalyst.

    Catalyst = price gap >2% at open vs previous close,
               OR volume spike (>1.5x 20-day average).

    Rationale: news + unusual volume = short-term momentum evidence.
    Used as an additional filter so we only buy when something is
    actually moving, not just scoring well on lagging indicators.
    """
    if len(df) < 22:
        return False
    prev_close = df["close"].iloc[-2]
    today_open = df["open"].iloc[-1]
    if prev_close > 0:
        gap_pct = abs((today_open - prev_close) / prev_close)
        if gap_pct >= gap_threshold:
            return True
    avg_vol = df["volume"].iloc[-21:-1].mean()
    if avg_vol > 0 and df["volume"].iloc[-1] > avg_vol * 1.5:
        return True
    return False


# ── Momentum continuation ────────────────────────────────────────────────────

def momentum_continuation(closes: pd.Series,
                           fast_span: int = 5,
                           gain_threshold: float = 0.02,
                           lookback: int = 3) -> bool:
    """Return True if price is above EMA(5) and gained ≥2% over last 3 days.

    Secondary entry path for short-term continuation moves that may not yet
    score ≥3 on the main signal stack but show clear price momentum.
    """
    if len(closes) < fast_span + lookback:
        return False
    ema5 = _ema(closes, fast_span).iloc[-1]
    current = closes.iloc[-1]
    past = closes.iloc[-1 - lookback]
    if past <= 0:
        return False
    return current > ema5 and (current - past) / past >= gain_threshold


# ── Composite score ───────────────────────────────────────────────────────────

def compute_score(closes: pd.Series, volumes: pd.Series,
                  regime: str = REGIME_TREND) -> int:
    """Confluence score combining all signals.

    Base signals (each ±1): EMA 5/20, RSI(7), MACD, volume, acceleration.
    Trend alignment bonus: +1 when both EMA and MACD are bullish.
    Catalyst (+1 bonus) is applied in strategy.py before threshold check.
    """
    ma = ma_signal(closes)
    mc = macd_signal(closes)
    score = (
        ma
        + rsi_signal(closes, regime=regime)
        + mc
        + volume_signal(closes, volumes)
        + acceleration_signal(closes)
    )
    if ma == 1 and mc == 1:
        score += 1  # trend alignment bonus
    return score
