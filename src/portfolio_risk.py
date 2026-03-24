"""Portfolio-level risk checks — applied after signal scoring, before buying.

Three layers added on top of the existing per-symbol signal strategy:

  1. Correlation filter     -- skip candidates that move with held positions
  2. Concentration check    -- enforce asset class diversification limits
  3. Volatility-adjusted    -- size positions to equalise risk contribution
     position sizing
  4. Metrics logging        -- Sharpe ratio + max drawdown each run

Why each matters:
  Correlation: buying MSFT when you hold AAPL doubles tech exposure without
  adding real diversification. Assets that move together offer no risk
  reduction regardless of how many you own.

  Concentration: true diversification comes from mixing asset classes
  (equities, bonds, commodities) not just adding more stocks. A portfolio
  of 10 tech stocks is not diversified.

  Volatility sizing: a flat 5% in TSLA (annual vol ~60%) vs BND (vol ~5%)
  creates wildly different risk contributions per position. Scaling by vol
  means each position adds roughly the same amount of risk to the portfolio.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# ── Asset class map ───────────────────────────────────────────────────────────
# Used for concentration limits. Unknown symbols default to "equity".
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
    # Broad market ETFs (behave like equity but are already diversified)
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

# Maximum portfolio weight per asset class (market_value / portfolio_value).
# These are soft limits — the strategy will skip buys that would breach them.
MAX_CLASS_WEIGHT = {
    "equity":        0.60,
    "etf_broad":     0.30,
    "etf_sector":    0.20,
    "bond":          0.40,
    "commodity":     0.20,
    "international": 0.30,
}

CORRELATION_THRESHOLD = 0.75   # skip candidate if |corr| > this with any held symbol
TARGET_VOL = 0.20              # annualised vol benchmark for position sizing (~large-cap)


def _asset_class(symbol: str) -> str:
    return ASSET_CLASS.get(symbol, "equity")


# ── 1. Correlation filter ─────────────────────────────────────────────────────

def correlation_filter(
    candidates: list,
    held_symbols: set,
    bars: dict,
    threshold: float = CORRELATION_THRESHOLD,
) -> list:
    """Remove candidates whose returns correlate too strongly with held positions.

    Returns filtered list preserving original order (score-sorted).
    Candidates with no overlap in bar history pass through unchecked.
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
    """Return (ok, reason).

    Blocks a buy if the candidate's asset class is already at its weight limit.
    Rationale: prevents the portfolio from becoming 90% tech stocks even if
    tech signals are all firing at once.
    """
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


# ── 3. Volatility-adjusted position sizing ────────────────────────────────────

def volatility_adjusted_qty(
    symbol: str,
    bars: dict,
    portfolio_value: float,
    base_pct: float = 0.05,
) -> int:
    """Scale position size inversely to the asset's annualised volatility.

    Base allocation = 5% of portfolio.
    If the asset's vol is below TARGET_VOL → allocate more (up to 10%).
    If the asset's vol is above TARGET_VOL → allocate less.

    Examples at $100k portfolio, base = $5,000:
      BND  (vol ~4%)  → scalar 5.0x capped → $10,000 allocation
      SPY  (vol ~15%) → scalar 1.3x        →  $6,667
      TSLA (vol ~55%) → scalar 0.36x       →  $1,818
    """
    if symbol not in bars:
        return 0
    price = float(bars[symbol]["close"].iloc[-1])
    if price <= 0:
        return 0

    base_alloc = portfolio_value * base_pct
    daily_vol = bars[symbol]["close"].pct_change().dropna().std()
    annual_vol = daily_vol * (252 ** 0.5)

    if annual_vol > 0:
        scalar = min(TARGET_VOL / annual_vol, 2.0)   # cap at 2x base
        alloc = min(base_alloc * scalar, portfolio_value * 0.10)  # hard cap 10%
    else:
        alloc = base_alloc

    return max(1, int(alloc / price))


# ── 4. Portfolio metrics logging ──────────────────────────────────────────────

def log_portfolio_metrics(trading_client) -> None:
    """Compute and log Sharpe ratio and max drawdown from 3M portfolio history.
    Costs one extra Alpaca API call (still free tier, well within limits).
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
        peak = s.cummax()
        max_dd = ((s - peak) / peak).min() * 100

        logger.info(
            "Portfolio metrics (3M) | Sharpe: %.2f | Max Drawdown: %.1f%%",
            sharpe, max_dd,
        )
        if sharpe < 0:
            logger.warning("Sharpe is negative — strategy is underperforming risk-free rate")
        if max_dd < -20:
            logger.warning("Max drawdown exceeds 20%% — consider reviewing position sizing")

    except Exception as e:
        logger.warning("Could not compute portfolio metrics: %s", e)
