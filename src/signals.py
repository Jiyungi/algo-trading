"""Signal computation — pure math, zero API calls.

Each function returns:  +1 (buy), -1 (sell), 0 (hold)
compute_score() combines signals into a confluence score.

Signals (each ±1):
  EMA 5/20 crossover, RSI(7) regime-aware, MACD, volume, acceleration
Bonuses applied inside compute_score():
  +1 trend alignment (EMA and MACD both bullish)
Bonuses applied in strategy.py:
  +1 catalyst (gap >2% or volume spike)

Trade type classification:
  "trend"          -- EMA + MACD aligned, hold up to 7 days
  "mean_reversion" -- RSI-based oversold bounce, shorter leash (2-4 days)
  "catalyst"       -- gap/volume event-driven, allow same-day exit
"""
import numpy as np
import pandas as pd

REGIME_TREND = "trend"
REGIME_MEAN_REV = "mean_reversion"
REGIME_BEAR = "bear"          # SPY below EMA20 AND EMA20 itself declining


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


# ── Regime detection ─────────────────────────────────────────────────────────

def detect_regime(spy_bars: pd.DataFrame, window: int = 20) -> str:
    """Return REGIME_TREND, REGIME_MEAN_REV, or REGIME_BEAR based on SPY.

    Above EMA20                         → uptrend  → momentum logic.
    Below EMA20 AND EMA20 declining     → sustained downtrend → halt new buys.
    Below EMA20 AND EMA20 flat/rising   → choppy dip → mean-reversion logic.

    Bear is distinguished from mean-reversion by checking whether the EMA20
    itself has turned lower over the past 5 sessions — a proxy for a macro
    downtrend rather than a temporary pullback.
    """
    if spy_bars is None or len(spy_bars) < window + 5:
        return REGIME_TREND
    ema20 = _ema(spy_bars["close"], window)
    current = spy_bars["close"].iloc[-1]
    if current > ema20.iloc[-1]:
        return REGIME_TREND
    # Below EMA20: distinguish sustained downtrend from choppy dip
    if ema20.iloc[-1] < ema20.iloc[-6]:   # EMA20 lower than 5 sessions ago
        return REGIME_BEAR
    return REGIME_MEAN_REV


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

    if regime == REGIME_MEAN_REV:
        if rsi < 30:
            return 1
        if rsi > 70:
            return -1
    else:  # trend or bear — RSI as momentum confirmation, not contrarian
        if rsi > 60:
            return 1
        if rsi < 40:
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


# ── Trade type classification ────────────────────────────────────────────────

TRADE_TYPE_TREND = "trend"
TRADE_TYPE_MEAN_REV = "mean_reversion"
TRADE_TYPE_CATALYST = "catalyst"


def classify_trade_type(
    df, score: int, regime: str, catalyst: bool,
) -> str:
    """Classify an entry into trend, mean_reversion, or catalyst.

    Rules (checked in order):
      1. catalyst  -- gap >3% OR volume >2x 20-day avg
      2. mean_rev  -- regime is mean-reversion AND RSI(7) < 35
      3. trend     -- default for all other entries
    """
    if len(df) < 22:
        return TRADE_TYPE_TREND

    # Strong catalyst: large gap or extreme volume spike
    prev_close = df["close"].iloc[-2]
    today_open = df["open"].iloc[-1]
    if prev_close > 0:
        gap_pct = abs((today_open - prev_close) / prev_close)
        if gap_pct >= 0.03:
            return TRADE_TYPE_CATALYST

    avg_vol = df["volume"].iloc[-21:-1].mean()
    if avg_vol > 0 and df["volume"].iloc[-1] > avg_vol * 2.0:
        return TRADE_TYPE_CATALYST

    # Mean-reversion: regime is mean-rev AND RSI oversold
    if regime == REGIME_MEAN_REV:
        closes = df["close"]
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = (-delta.clip(upper=0)).rolling(7).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = (100 - (100 / (1 + rs))).iloc[-1]
        if not pd.isna(rsi) and rsi < 35:
            return TRADE_TYPE_MEAN_REV

    return TRADE_TYPE_TREND
