"""
Polymarket Bot Configuration
Loads settings from environment variables
"""
import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional

# Load environment variables from .env file
load_dotenv()


@dataclass
class Config:
    """Bot configuration loaded from environment"""

    # Wallet
    private_key: str
    wallet_address: str

    # API Credentials
    clob_api_key: str
    clob_secret: str
    clob_passphrase: str

    # Network
    chain_id: int
    rpc_url: str

    # Trading Parameters
    position_size_pct: float
    max_position_pct: float
    daily_loss_limit_pct: float
    min_price_threshold: float
    max_buy_price: float
    starting_bankroll: float

    # Safety
    dry_run: bool

    # Notifications (optional)
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]
    discord_webhook_url: Optional[str]


def load_config() -> Config:
    """Load configuration from environment variables"""

    # Required fields - will raise if missing
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        raise ValueError("PRIVATE_KEY is required in .env file")

    wallet_address = os.getenv("WALLET_ADDRESS")
    if not wallet_address:
        raise ValueError("WALLET_ADDRESS is required in .env file")

    return Config(
        # Wallet
        private_key=private_key,
        wallet_address=wallet_address,

        # API Credentials
        clob_api_key=os.getenv("CLOB_API_KEY", ""),
        clob_secret=os.getenv("CLOB_SECRET", ""),
        clob_passphrase=os.getenv("CLOB_PASSPHRASE", ""),

        # Network
        chain_id=int(os.getenv("POLYGON_CHAIN_ID", "137")),
        rpc_url=os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),

        # Trading Parameters
        position_size_pct=float(os.getenv("POSITION_SIZE_PCT", "0.005")),
        max_position_pct=float(os.getenv("MAX_POSITION_PCT", "0.05")),
        daily_loss_limit_pct=float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.05")),
        min_price_threshold=float(os.getenv("MIN_PRICE_THRESHOLD", "0.99")),
        max_buy_price=float(os.getenv("MAX_BUY_PRICE", "0.99")),
        starting_bankroll=float(os.getenv("STARTING_BANKROLL", "1000.0")),

        # Safety
        dry_run=os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes"),

        # Notifications
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
    )


# API Endpoints (configurable via environment variables)
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
CLOB_WS = os.getenv("CLOB_WS", "wss://ws-subscriptions-clob.polymarket.com/ws/")
GAMMA_API = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com")
DATA_API = os.getenv("DATA_API", "https://data-api.polymarket.com")

# Contract Addresses (Polygon Mainnet)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Rate Limits
PUBLIC_RATE_LIMIT = 100  # requests per minute
TRADING_RATE_LIMIT = 60  # orders per minute

# Logging
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Multi-Market Scheduler Configuration
# Load from environment variables with sensible defaults
SCHEDULER_MAX_WATCHLIST_SIZE = int(os.getenv("SCHEDULER_MAX_WATCHLIST_SIZE", "50"))
SCHEDULER_MAX_ACTIVE_EXECUTIONS = int(
    os.getenv("SCHEDULER_MAX_ACTIVE_EXECUTIONS", "5")
)
SCHEDULER_ELIGIBLE_WINDOW_SECONDS = int(
    os.getenv("SCHEDULER_ELIGIBLE_WINDOW_SECONDS", "60")
)
SCHEDULER_EXECUTION_WINDOW_SECONDS = int(
    os.getenv("SCHEDULER_EXECUTION_WINDOW_SECONDS", "3")
)
SCHEDULER_PRIMING_WINDOW_SECONDS = int(
    os.getenv("SCHEDULER_PRIMING_WINDOW_SECONDS", "15")
)
SCHEDULER_MAX_SPREAD_PERCENT = float(os.getenv("SCHEDULER_MAX_SPREAD_PERCENT", "5.0"))
SCHEDULER_MIN_LIQUIDITY_USD = float(
    os.getenv("SCHEDULER_MIN_LIQUIDITY_USD", "100.0")
)
SCHEDULER_MAX_PRICE_THRESHOLD = float(
    os.getenv("SCHEDULER_MAX_PRICE_THRESHOLD", "0.99")
)
SCHEDULER_MIN_PROBABILITY = float(os.getenv("SCHEDULER_MIN_PROBABILITY", "0.95"))
SCHEDULER_STALE_FEED_THRESHOLD_MS = int(
    os.getenv("SCHEDULER_STALE_FEED_THRESHOLD_MS", "500")
)
SCHEDULER_MAX_FAILURE_COUNT = int(os.getenv("SCHEDULER_MAX_FAILURE_COUNT", "3"))
SCHEDULER_TICK_INTERVAL_MS = int(os.getenv("SCHEDULER_TICK_INTERVAL_MS", "10"))


# ==============================================================================
# Spread Capture Strategy Configuration
# ==============================================================================
# Inspired by distinct-baguette's near-zero-loss trading approach:
# Buy at bid, sell at ask, or arbitrage both sides when combined < $1

# Entry criteria
SPREAD_MIN_SPREAD_PCT = float(os.getenv("SPREAD_MIN_SPREAD_PCT", "2.0"))  # Minimum spread to trade
SPREAD_MAX_SPREAD_PCT = float(os.getenv("SPREAD_MAX_SPREAD_PCT", "15.0"))  # Max spread (avoid illiquid)
SPREAD_MIN_LIQUIDITY_USD = float(os.getenv("SPREAD_MIN_LIQUIDITY_USD", "100.0"))  # Min depth

# Exit targets
SPREAD_EXIT_TARGET_PCT = float(os.getenv("SPREAD_EXIT_TARGET_PCT", "2.0"))  # Target profit per trade
SPREAD_STOP_LOSS_PCT = float(os.getenv("SPREAD_STOP_LOSS_PCT", "5.0"))  # Max loss per trade
SPREAD_MAX_HOLD_SEC = int(os.getenv("SPREAD_MAX_HOLD_SEC", "600"))  # 10 min max hold

# Pre-expiry safety (CRITICAL: exit before resolution to avoid directional risk)
SPREAD_EXIT_BEFORE_EXPIRY_SEC = int(os.getenv("SPREAD_EXIT_BEFORE_EXPIRY_SEC", "60"))  # Exit 60s before
SPREAD_NO_ENTRY_BEFORE_EXPIRY_SEC = int(os.getenv("SPREAD_NO_ENTRY_BEFORE_EXPIRY_SEC", "120"))  # No entry in final 2 min

# Position sizing (percentage-based for compounding)
SPREAD_POSITION_SIZE_PCT = float(os.getenv("SPREAD_POSITION_SIZE_PCT", "0.25"))  # 25% of bankroll per position
SPREAD_MAX_EXPOSURE_PCT = float(os.getenv("SPREAD_MAX_EXPOSURE_PCT", "0.75"))  # 75% max total exposure
SPREAD_MAX_CONCURRENT_POSITIONS = int(os.getenv("SPREAD_MAX_CONCURRENT_POSITIONS", "5"))  # Max positions
# Fixed limits as safety caps (optional, 0 = no cap)
SPREAD_MAX_POSITION_USD = float(os.getenv("SPREAD_MAX_POSITION_USD", "0"))  # Hard cap per position (0=unlimited)
SPREAD_MAX_TOTAL_EXPOSURE_USD = float(os.getenv("SPREAD_MAX_TOTAL_EXPOSURE_USD", "0"))  # Hard cap total (0=unlimited)

# Arbitrage mode (buy both YES and NO when combined < $1)
SPREAD_ENABLE_ARBITRAGE = os.getenv("SPREAD_ENABLE_ARBITRAGE", "True").lower() in ("true", "1", "yes")
SPREAD_MAX_ARBITRAGE_COST = float(os.getenv("SPREAD_MAX_ARBITRAGE_COST", "0.98"))  # Max combined cost

# Scanning
SPREAD_SCAN_INTERVAL_SEC = float(os.getenv("SPREAD_SCAN_INTERVAL_SEC", "5.0"))  # How often to scan
