"""Intraday check runner — manages exits on held positions.

Runs 2-3 times during market hours via GitHub Actions.
Does NOT open new swing positions — only evaluates intraday exits
(recovery into strength, failure below VWAP/opening range).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from monitor import run_intraday_check  # noqa: E402

if __name__ == "__main__":
    run_intraday_check()
