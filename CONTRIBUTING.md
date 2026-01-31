# Contributing to PredictiveEdge

## Development Setup

```bash
# Clone
git clone https://github.com/ChudiNnorukam/predictive-edge-bot.git
cd predictive-edge-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with test credentials
```

---

## Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready code |
| `feature/arag-prototype` | RAG/ML experiments |
| `feature/*` | New features |
| `fix/*` | Bug fixes |

### Workflow

1. Create feature branch from `main`
2. Make changes with clear commits
3. Test locally with `DRY_RUN=True`
4. Create pull request to `main`
5. Review and merge

---

## Code Style

### Python
- Python 3.11+
- Type hints for function signatures
- Docstrings for public functions
- Max line length: 100 characters

### Commits
```
<type>: <description>

Types:
- feat: New feature
- fix: Bug fix
- docs: Documentation
- refactor: Code restructure
- test: Testing
```

Example:
```
feat: Add compounding position sizing

- Read POSITION_SIZE_PCT from environment
- Cap maximum position at $5
- Update trade logger with position details
```

---

## Testing

### Dry Run (Always First)
```bash
# Set in .env
DRY_RUN=True

# Run scanner
python scanner.py --direct

# Run sniper
python sniper.py --token-id <TOKEN>
```

### Live Testing
- Start with $1 trades
- Monitor logs closely
- Only after 10+ successful dry runs

---

## Logging

Use the trade logger for all trading events:

```python
from utils.trade_logger import get_trade_logger

logger = get_trade_logger()
logger.log_opportunity(...)
logger.log_execution(...)
```

Logs go to: `logs/trades/trades_YYYY-MM-DD.jsonl`

---

## Key Files

| File | Purpose | Modify With Care |
|------|---------|------------------|
| `sniper.py` | Core trading logic | High |
| `config.py` | Configuration | Medium |
| `utils/trade_logger.py` | Logging system | Medium |
| `scanner.py` | Market discovery | Low |

---

## Security

### Never Commit
- `.env` files
- Private keys
- API credentials
- Trade logs with sensitive data

### Always Use
- Environment variables for secrets
- `.gitignore` for sensitive files
- `DRY_RUN=True` for testing

---

## Questions?

Open an issue or contact the PredictiveEdge team.
