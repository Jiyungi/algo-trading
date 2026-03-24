"""Portfolio-level risk checks — applied after signal scoring, before buying.

Layers:
  1. Correlation filter  -- skip candidates that move with held positions
  2. Concentration check -- enforce asset class diversification limits
  3. Volatility sizing   -- size positions to equalise risk contribution
  4. Metrics logging     -- Sharpe ratio + max drawdown each run
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# ── Asset class map ──────────────────────────────────────────────────────────
ASSET_CLASS = {
    # Individual equities
    "AAPL": "equity", "MSFT": "equity", "NVDA": "equity", "GOOGL": "equity",
    "AMZN": "equity", "META": "equity", "TSLA": "equity", "AVGO": "equity",
    "ORCL": "equity", "CRM": "equity", "ADBE": "equity", "AMD": "equity",
    "QCOM": "equity", "TXN": "equity",
    "JPM": "equity", "BAC": "equity", "WFC": "equity", "GS": "equity",
    "MS": "equity", "BLK": "equity", "V": "equity", "MA": "equity",
    "AXP": "equity",
    "JNJ": "equity", "UNH": "equity", "LLY": "equity", "ABBV": "equity",
    "MRK": "equity", "PFE": "equity", "TMO": "equity", "ABT": "equity",
    "HD": "equity", "MCD": "equity", "NKE": "equity", "SBUX": "equity",
    "COST": "equity", "WMT": "equity", "PG": "equity",
    "KO": "equity", "PEP": "equity",
    "XOM": "equity", "CVX": "equity", "COP": "equity",
    "SLB": "equity", "EOG": "equity",
    "CAT": "equity", "DE": "equity", "HON": "equity", "GE": "equity",
    "RTX": "equity", "LMT": "equity", "BA": "equity",
    # Broad market ETFs
    "SPY": "etf_broad", "QQQ": "etf_broad",
    "IWM": "etf_broad", "DIA": "etf_broad",
    # Sector ETFs
    "XLK": "etf_sector", "XLF": "etf_sector", "XLV": "etf_sector",
    "XLE": "etf_sector", "XLI": "etf_sector",
    "XLY": "etf_sector", "XLP": "etf_sector",
    # Fixed income
    "BND": "bond", "TLT": "bond",
    # Commodities
    "GLD": "commodity", "SLV": "commodity",
    # International
    "EWY": "international", "VEU": "international", "EEM": "international",
}

MAX_CLASS_WEIGHT = {
    "equity":        0.60,
    "etf_broad":     0.30,
    "etf_sector":    0.20,
    "bond":          0.40,
    "commodity":     0.20,
    "international": 0.30,
}

CORRELATION_THRESHOLD = 0.87  # relaxed from 0.80 for short-term strategies
TARGET_VOL = 0.20             # annualised vol benchmark (~large-cap equity)


def _asset_class(symbol: str) -> str:
    return ASSET_CLASS.get(symbol, "equity")


# ── 1. Correlation filter ────────────────────────────────────────────────────

def correlation_filter(
    candidates: list,
    held_symbols: set,
    bars: dict,
    threshold: float = CORRELATION_THRESHOLD,
) -> list:
    """Remove candidates correlated too strongly with held positions.

    Returns filtered list preserving score-sorted order.
    Candidates with no bar history overlap pass through unchecked.
    """
    if not held_symbols or not candidates:
        return candidates

    held_returns = {
        sym: bars[sym]["close"].pct_change().dropna()
        for sym in held_symbols
        if sym in bars and len(bars[sym]) >= 20
    }
    if not held_returns:
        return candidates

    filtered = []
    for sym, score, price in candidates:
        if sym not in bars:
            filtered.append((sym, score, price))
            continue

        candidate_ret = bars[sym]["close"].pct_change().dropna()
        max_corr, most_correlated = 0.0, None

        for held_sym, held_ret in held_returns.items():
            aligned = pd.concat(
                [candidate_ret.rename("c"), held_ret.rename("h")], axis=1
            ).dropna()
            if len(aligned) < 20:
                continue
            corr = abs(aligned["c"].corr(aligned["h"]))
            if corr > max_corr:
                max_corr, most_correlated = corr, held_sym

        if max_corr >= threshold:
            logger.info(
                "CORR SKIP   | %s | corr=%.2f with %s (limit %.2f)",
                sym, max_corr, most_correlated, threshold,
            )
        else:
            filtered.append((sym, score, price))

    return filtered


# ── 2. Asset class concentration check ───────────────────────────────────────

def concentration_check(
    candidate: str,
    held_positions,
    portfolio_value: float,
) -> tuple[bool, str]:
    """Return (ok, reason). Blocks if asset class is at its weight limit."""
    if portfolio_value <= 0:
        return True, "OK"

    asset_cls = _asset_class(candidate)
    limit = MAX_CLASS_WEIGHT.get(asset_cls, 1.0)

    current_weight = sum(
        float(p.market_value)
        for p in held_positions
        if _asset_class(p.symbol) == asset_cls
    ) / portfolio_value

    if current_weight >= limit:
        return False, (
            f"{asset_cls} already at {current_weight:.0%} "
            f"(limit {limit:.0%}) — skipping {candidate}"
        )
    return True, f"{asset_cls} {current_weight:.0%} / {limit:.0%}"


# ── 3. Volatility-adjusted position sizing ───────────────────────────────────

def volatility_adjusted_qty(
    symbol: str,
    bars: dict,
    portfolio_value: float,
    base_pct: float = 0.035,
    score: int = 3,
) -> int:
    """Scale position size by volatility AND signal conviction.

    Base allocation = 3.5% (reduced from 5% to support up to 4 positions).
    Volatility scalar: low-vol assets get more, high-vol get less.
    Conviction scalar: score=4 gets 30% more than score=3.
    Hard cap: 10% of portfolio per position.
    """
    if symbol not in bars:
        return 0
    price = float(bars[symbol]["close"].iloc[-1])
    if price <= 0:
        return 0

    base_alloc = portfolio_value * base_pct
    daily_vol = bars[symbol]["close"].pct_change().dropna().std()
    annual_vol = daily_vol * (252 ** 0.5)

    vol_scalar = min(TARGET_VOL / annual_vol, 2.0) if annual_vol > 0 else 1.0
    conviction_scalar = 0.8 + (score - 3) * 0.70  # 3→0.8x, 4→1.5x
    alloc = min(
        base_alloc * vol_scalar * conviction_scalar,
        portfolio_value * 0.10,
    )
    return max(1, int(alloc / price))


# ── 4. Portfolio metrics logging ─────────────────────────────────────────────

def log_portfolio_metrics(trading_client) -> None:
    """Log Sharpe ratio and max drawdown from 3M portfolio history.
    One extra Alpaca API call — still within free tier limits.
    """
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        history = trading_client.get_portfolio_history(
            GetPortfolioHistoryRequest(period="3M", timeframe="1D")
        )
        equity = [e for e in (history.equity or []) if e is not None]
        if len(equity) < 20:
            logger.info("Metrics: not enough history yet (need 20+ days)")
            return

        s = pd.Series(equity, dtype=float)
        returns = s.pct_change().dropna()
        rf_daily = 0.05 / 252
        sharpe = (
            (returns.mean() - rf_daily) / returns.std() * (252 ** 0.5)
            if returns.std() > 0 else 0.0
        )
        max_dd = ((s - s.cummax()) / s.cummax()).min() * 100

        logger.info(
            "Metrics (3M) | Sharpe: %.2f | Max Drawdown: %.1f%%",
            sharpe, max_dd,
        )
        if sharpe < 0:
            logger.warning(
                "Sharpe negative — underperforming risk-free rate"
            )
        if max_dd < -20:
            logger.warning(
                "Max drawdown >20%% — consider reviewing position sizing"
            )

    except Exception as e:
        logger.warning("Could not compute portfolio metrics: %s", e)
