"""Simple CLI entrypoint to perform a sample trade or dry-run.

Usage: python src/trade.py --symbol AAPL --qty 1 --side buy
"""
import argparse
import logging
import os
import sys

from .orders import place_order
from .config import get_config


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a minimal trade example")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--dry-run", action="store_true", help="Do not place a live order")

    args = parser.parse_args(argv)

    if args.dry_run:
        os.environ["DRY_RUN"] = "1"

    cfg = get_config()
    logger.info("Running trade with config DRY_RUN=%s", cfg.get("DRY_RUN"))

    try:
        result = place_order(symbol=args.symbol, qty=args.qty, side=args.side)
    except Exception as exc:
        logger.exception("Failed to place order: %s", exc)
        sys.exit(2)

    logger.info("Result: %s", result)


if __name__ == "__main__":
    main()
