"""Sector-level exposure control — prevents clustering of correlated bets.

Complements the coarser asset-class limits in portfolio_risk.py by enforcing
finer-grained per-sector position and weight caps.

Sector limits:
  Max 2 positions per sector
  Max ~15% portfolio weight per sector
  Mean-reversion entries: max 1 new per sector per run
  ETFs / bonds / commodities are exempt from position count limits
"""
import logging

logger = logging.getLogger(__name__)

# ── Sector map ───────────────────────────────────────────────────────────────
# Maps every symbol in UNIVERSE to a sector string.

SECTOR_MAP = {
    # Mega-cap tech
    "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "GOOGL": "tech",
    "AMZN": "tech", "META": "tech", "TSLA": "tech", "AVGO": "tech",
    "ORCL": "tech", "CRM": "tech", "ADBE": "tech", "AMD": "tech",
    "QCOM": "tech", "TXN": "tech",
    # Finance
    "JPM": "finance", "BAC": "finance", "WFC": "finance",
    "GS": "finance", "MS": "finance", "BLK": "finance",
    "V": "finance", "MA": "finance", "AXP": "finance",
    # Healthcare
    "JNJ": "healthcare", "UNH": "healthcare", "LLY": "healthcare",
    "ABBV": "healthcare", "MRK": "healthcare", "PFE": "healthcare",
    "TMO": "healthcare", "ABT": "healthcare",
    # Consumer
    "HD": "consumer", "MCD": "consumer", "NKE": "consumer",
    "SBUX": "consumer", "COST": "consumer", "WMT": "consumer",
    "PG": "consumer", "KO": "consumer", "PEP": "consumer",
    # Energy
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "SLB": "energy", "EOG": "energy",
    # Industrial
    "CAT": "industrial", "DE": "industrial", "HON": "industrial",
    "GE": "industrial", "RTX": "industrial", "LMT": "industrial",
    "BA": "industrial",
    # Broad ETFs (exempt from sector position limits)
    "SPY": "etf_broad", "QQQ": "etf_broad",
    "IWM": "etf_broad", "DIA": "etf_broad",
    # Sector ETFs (exempt)
    "XLK": "etf_sector", "XLF": "etf_sector", "XLV": "etf_sector",
    "XLE": "etf_sector", "XLI": "etf_sector",
    "XLY": "etf_sector", "XLP": "etf_sector",
    # Bonds / commodities / international (exempt from count limits)
    "BND": "bond", "TLT": "bond",
    "GLD": "commodity", "SLV": "commodity",
    "EWY": "international", "VEU": "international",
    "EEM": "international",
}

MAX_POSITIONS_PER_SECTOR = 2
MAX_SECTOR_WEIGHT_PCT = 0.15  # 15% of portfolio per sector
MAX_MEAN_REV_PER_SECTOR = 1   # per strategy run

# Sectors exempt from position count limits (still respect weight caps)
EXEMPT_SECTORS = {
    "etf_broad", "etf_sector", "bond", "commodity", "international",
}


def get_sector(symbol: str) -> str:
    """Return sector for a symbol, defaulting to 'other'."""
    return SECTOR_MAP.get(symbol, "other")


def sector_check(
    candidate: str,
    trade_type: str,
    held_positions,
    portfolio_value: float,
    mean_rev_this_run: dict,
) -> tuple[bool, str]:
    """Return (ok, reason). Blocks if sector limits are exceeded.

    Args:
        candidate: symbol to check
        trade_type: "trend", "mean_reversion", or "catalyst"
        held_positions: list of Alpaca position objects
        portfolio_value: current portfolio value
        mean_rev_this_run: {sector: count} of mean-rev buys this run
    """
    sector = get_sector(candidate)

    if sector in EXEMPT_SECTORS or sector == "other":
        return True, f"{sector} (exempt)"

    # Count held positions in same sector
    same_sector = [
        p for p in held_positions
        if get_sector(p.symbol) == sector
    ]
    count = len(same_sector)

    if count >= MAX_POSITIONS_PER_SECTOR:
        return False, (
            f"{sector} already has {count} positions "
            f"(limit {MAX_POSITIONS_PER_SECTOR})"
        )

    # Weight check
    if portfolio_value > 0:
        sector_weight = sum(
            float(p.market_value) for p in same_sector
        ) / portfolio_value
        if sector_weight >= MAX_SECTOR_WEIGHT_PCT:
            return False, (
                f"{sector} at {sector_weight:.0%} "
                f"(limit {MAX_SECTOR_WEIGHT_PCT:.0%})"
            )

    # Mean-reversion: max 1 new entry per sector per run
    if trade_type == "mean_reversion":
        mr_count = mean_rev_this_run.get(sector, 0)
        if mr_count >= MAX_MEAN_REV_PER_SECTOR:
            return False, (
                f"{sector} already has {mr_count} mean-rev "
                f"entry this run"
            )

    return True, f"{sector} {count}/{MAX_POSITIONS_PER_SECTOR}"
