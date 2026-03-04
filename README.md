Algo Trading
===========

Small algorithmic trading utilities using the Alpaca API.

**Prerequisites**
- **Python:** 3.10+ recommended.
- **Git:** for version control and pushing to GitHub.

**Setup**
- Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Copy the example environment file and fill in your keys (do not commit this file):

```bash
cp .env.example .env
# edit .env and set ALPACA_API_KEY and ALPACA_SECRET_KEY
```

- The project reads credentials from environment variables in [src/config.py](src/config.py).

**Running**
- From the project root you can run the scripts directly. Examples:

```bash
# run the main trading script
python src/main.py

# generate the weekly report (example run from src)
python src/weekly_report.py
```

**Git & Secrets**
- `.env` is included in [.gitignore](.gitignore) to avoid committing secrets. Keep real API keys out of git.
- If you need CI secrets (GitHub Actions), add them in the repository Settings → Secrets → Actions as `ALPACA_KEY` and `ALPACA_SECRET`.
- If credentials were accidentally committed, rotate them immediately and consider purging history (git-filter-repo or BFG).

**Files to check**
- Configuration and client setup: [src/config.py](src/config.py)
- Example environment variables: [.env.example](.env.example)
