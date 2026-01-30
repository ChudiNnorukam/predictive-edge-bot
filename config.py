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
