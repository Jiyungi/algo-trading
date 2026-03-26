"""Lightweight sentiment filter using yfinance news headlines.

Used as a filter/modifier on entry signals, NOT a standalone buy signal.

Decision matrix:
  trade_type       | sentiment < -0.3 | neutral | sentiment > 0.3
  mean_reversion   |     BLOCK        | neutral |    neutral
  trend            |     neutral      | neutral |    BOOST (+1)
  catalyst         |     neutral      | neutral |    BOOST (+1)

Graceful degradation: returns ("neutral", 0.0) on any failure.
Zero external API keys required — uses yfinance .news property.
"""
import logging

logger = logging.getLogger(__name__)

# Keyword lists for simple bag-of-words sentiment scoring
_POSITIVE = {
    "beat", "beats", "exceeds", "exceeded", "upgrade", "upgraded",
    "raises", "raised", "strong", "growth", "record", "outperform",
    "surge", "surges", "rally", "rallies", "profit", "gains",
    "bullish", "optimistic", "positive", "expands", "expansion",
    "buy", "overweight", "upside", "breakthrough", "soars",
    "approves", "approval", "launches", "partnership",
}

_NEGATIVE = {
    "miss", "misses", "missed", "downgrade", "downgraded",
    "cut", "cuts", "weak", "weakness", "layoff", "layoffs",
    "recall", "recalls", "lawsuit", "fraud", "investigation",
    "decline", "declines", "loss", "losses", "bearish",
    "pessimistic", "negative", "warns", "warning", "crash",
    "sell", "underweight", "underperform", "plunge", "plunges",
    "default", "bankruptcy", "slump", "fails", "failure",
}

# Per-run cache to avoid duplicate yfinance calls
_cache = {}

# Thresholds for sentiment decisions
_NEGATIVE_THRESHOLD = -0.3
_POSITIVE_THRESHOLD = 0.3


def _fetch_headlines(symbol, max_headlines=10):
    """Fetch recent news headlines via yfinance (free, no API key).

    Returns list of headline strings. Empty list on failure.
    """
    if symbol in _cache:
        return _cache[symbol]

    headlines = []
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        for item in news[:max_headlines]:
            title = item.get("title", "")
            if title:
                headlines.append(title)
    except Exception as e:
        logger.debug("Sentiment fetch failed for %s: %s", symbol, e)

    _cache[symbol] = headlines
    return headlines


def score_sentiment(headlines):
    """Score headlines from -1.0 (strongly negative) to +1.0 (positive).

    Uses simple keyword matching — no ML, no external dependencies.
    Returns 0.0 if no headlines or no signal words found.
    """
    if not headlines:
        return 0.0

    scores = []
    for headline in headlines:
        words = set(headline.lower().split())
        pos = len(words & _POSITIVE)
        neg = len(words & _NEGATIVE)
        total = pos + neg
        if total > 0:
            scores.append((pos - neg) / total)

    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def get_sentiment_filter(symbol, trade_type):
    """Return (action, score) for a buy candidate.

    action: "block", "boost", or "neutral"
    score: raw sentiment score for logging

    Never raises — returns ("neutral", 0.0) on any failure.
    """
    try:
        headlines = _fetch_headlines(symbol)
        score = score_sentiment(headlines)

        if trade_type == "mean_reversion":
            if score < _NEGATIVE_THRESHOLD:
                return "block", score
        elif trade_type in ("trend", "catalyst"):
            if score > _POSITIVE_THRESHOLD:
                return "boost", score

        return "neutral", score

    except Exception as e:
        logger.debug("Sentiment filter error for %s: %s", symbol, e)
        return "neutral", 0.0


def clear_cache():
    """Clear the per-run headline cache."""
    _cache.clear()
