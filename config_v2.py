"""
Unified Configuration Module for Polymarket Bot
===============================================

Combines all module configurations into a single coherent system.
Loads from environment variables with sensible defaults and profiles.

Usage:
    from config_v2 import ScalingConfig, load_scaling_config

    config = load_scaling_config()
    errors = validate_config(config)
    if errors:
        for error in errors:
            print(f"Config error: {error}")
    else:
        print(f"Configuration valid: {config.log_level}")

Profiles:
    CONSERVATIVE_PROFILE: Low risk, tight limits
    AGGRESSIVE_PROFILE: Higher throughput, relaxed limits
    PAPER_TRADING_PROFILE: Dry run mode for testing
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# ============================================================================
# Sub-Config Dataclasses (organized by module)
# ============================================================================


@dataclass
class RiskConfig:
    """Kill Switch and Circuit Breaker configuration."""

    # Kill Switch thresholds
    stale_feed_threshold_ms: int = 500  # Halt if feed > 500ms stale
    rpc_lag_threshold_ms: int = 300  # Halt if order ack > 300ms
    max_outstanding_orders: int = 10  # Global limit on outstanding orders
    daily_loss_limit_percent: float = 5.0  # Stop trading if daily loss > 5%

    # Circuit Breaker settings
    circuit_failure_threshold: int = 3  # Trip after N consecutive failures
    circuit_recovery_timeout_seconds: int = 60  # Time before auto-reset
    circuit_half_open_max_requests: int = 1  # Max requests in HALF_OPEN


@dataclass
class CapitalConfig:
    """Capital allocation and exposure limits."""

    # Per-market limits
    max_exposure_per_market_percent: float = 5.0  # 5% of bankroll
    max_exposure_per_market_absolute: float = 50.0  # Hard cap in dollars

    # Total limits
    max_total_exposure_percent: float = 30.0  # 30% across all markets

    # Order sizing
    min_order_size: float = 1.0  # Minimum order in dollars
    order_split_threshold: float = 20.0  # Split orders > $20
    order_split_count: int = 3  # Number of sub-orders

    # Recycling
    recycle_delay_seconds: float = 5.0  # Delay after market resolution


@dataclass
class MetricsConfig:
    """Metrics collection and dashboard configuration."""

    # Collection intervals
    aggregation_interval_seconds: int = 60  # Aggregate every minute
    dashboard_refresh_seconds: int = 5  # Dashboard update frequency

    # Retention
    history_hours: int = 24  # Keep 24h of detailed metrics

    # Alerts
    fill_rate_warning_threshold: float = 0.5  # Warn if fill rate < 50%
    latency_warning_ms: float = 50.0  # Warn if p95 latency > 50ms

    # Performance targets
    target_p95_decision_latency_ms: float = 30.0  # Tick to decision
    target_p95_order_ack_latency_ms: float = 150.0  # Order to ack


@dataclass
class SchedulerConfig:
    """Market state machine and scheduler configuration."""

    # Time-based criteria
    time_to_eligibility_sec: int = 60  # Enter ELIGIBLE when < 60s to expiry
    stale_feed_threshold_ms: int = 500  # Feed stale if no update in 500ms
    max_failures_before_hold: int = 3  # ON_HOLD after 3+ failures

    # Price criteria
    max_buy_price: float = 0.99  # Don't trade above this price
    min_edge_pct: float = 0.01  # Minimum edge percentage (1%)

    # Cleanup
    max_hold_hours: int = 24  # Drop DONE markets after 24 hours

    # Execution windows
    max_watchlist_size: int = 50  # Max markets to watch concurrently
    max_active_executions: int = 5  # Max markets executing simultaneously
    execution_window_seconds: int = 3  # Time window for order execution
    priming_window_seconds: int = 15  # Time to prepare for execution
    eligible_window_seconds: int = 60  # Window to find eligible markets

    # Tick parameters
    tick_interval_ms: int = 10  # How often to check state transitions

    # Spread and liquidity filters
    max_spread_percent: float = 5.0  # Max bid-ask spread
    min_liquidity_usd: float = 100.0  # Minimum liquidity threshold
    min_probability: float = 0.95  # Minimum market probability


@dataclass
class ScannerConfig:
    """Market discovery scanner configuration."""

    # Time-based filtering
    min_time_to_expiry_seconds: int = 60  # Minimum time to market expiry
    max_time_to_expiry_hours: int = 24  # Maximum time to market expiry

    # Volume and liquidity
    min_volume_usd: float = 100.0  # Minimum trading volume

    # Scanning behavior
    scan_interval_seconds: int = 300  # Scan every 5 minutes

    # Categories to include (empty = all)
    categories: List[str] = field(default_factory=list)

    # API limits
    markets_per_request: int = 100  # Markets per API call
    max_markets_to_track: int = 100  # Max concurrent market tracking

    # Recovery
    max_api_failures: int = 3  # Halt scanning after N API failures


@dataclass
class FeedConfig:
    """WebSocket and price feed configuration."""

    # Connection settings
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/"
    reconnect_timeout_seconds: int = 10  # Time before reconnect attempt
    max_reconnect_attempts: int = 5  # Give up after N reconnects

    # Feed quality
    max_price_update_interval_ms: int = 500  # Warn if no update in 500ms
    stale_feed_threshold_ms: int = 500  # Consider feed stale after 500ms

    # Buffer settings
    max_buffered_ticks: int = 1000  # Max ticks to buffer during backpressure
    buffer_flush_interval_ms: int = 100  # Flush buffer every 100ms


@dataclass
class NotificationConfig:
    """Notification and alerting configuration."""

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False

    # Discord
    discord_webhook_url: Optional[str] = None
    discord_enabled: bool = False

    # Alert thresholds
    alert_on_kill_switch: bool = True  # Alert when trading halts
    alert_on_circuit_trip: bool = True  # Alert on circuit breaker trips
    alert_on_daily_loss: bool = True  # Alert when daily loss threshold hit


# ============================================================================
# Master Configuration Dataclass
# ============================================================================


@dataclass
class ScalingConfig:
    """
    Unified configuration for the entire Polymarket bot system.

    Combines configurations from all modules into a single coherent system
    with environment variable loading and validation.
    """

    # Core settings
    dry_run: bool = False
    log_level: str = "INFO"

    # Wallet and credentials
    private_key: str = ""
    wallet_address: str = ""
    clob_api_key: str = ""
    clob_secret: str = ""
    clob_passphrase: str = ""

    # Network configuration
    chain_id: int = 137  # Polygon mainnet
    rpc_url: str = "https://polygon-rpc.com"
    clob_host: str = "https://clob.polymarket.com"

    # API endpoints
    gamma_api: str = "https://gamma-api.polymarket.com"
    data_api: str = "https://data-api.polymarket.com"

    # Sub-configurations
    risk: RiskConfig = field(default_factory=RiskConfig)
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)

    # Rate limits
    public_rate_limit: int = 100  # requests per minute
    trading_rate_limit: int = 60  # orders per minute


# ============================================================================
# Configuration Loading and Validation
# ============================================================================


def load_scaling_config() -> ScalingConfig:
    """
    Load complete configuration from environment variables.

    Returns:
        ScalingConfig: Complete unified configuration

    Raises:
        ValueError: If required environment variables are missing
    """

    # Check required credentials
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        raise ValueError("PRIVATE_KEY is required in .env file")

    wallet_address = os.getenv("WALLET_ADDRESS")
    if not wallet_address:
        raise ValueError("WALLET_ADDRESS is required in .env file")

    return ScalingConfig(
        # Core
        dry_run=os.getenv("DRY_RUN", "True").lower() in ("true", "1", "yes"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        # Credentials
        private_key=private_key,
        wallet_address=wallet_address,
        clob_api_key=os.getenv("CLOB_API_KEY", ""),
        clob_secret=os.getenv("CLOB_SECRET", ""),
        clob_passphrase=os.getenv("CLOB_PASSPHRASE", ""),
        # Network
        chain_id=int(os.getenv("POLYGON_CHAIN_ID", "137")),
        rpc_url=os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),
        clob_host=os.getenv("CLOB_HOST", "https://clob.polymarket.com"),
        # APIs
        gamma_api=os.getenv("GAMMA_API", "https://gamma-api.polymarket.com"),
        data_api=os.getenv("DATA_API", "https://data-api.polymarket.com"),
        # Risk sub-config
        risk=RiskConfig(
            stale_feed_threshold_ms=int(
                os.getenv("RISK_STALE_FEED_MS", "500")
            ),
            rpc_lag_threshold_ms=int(os.getenv("RISK_RPC_LAG_MS", "300")),
            max_outstanding_orders=int(
                os.getenv("RISK_MAX_ORDERS", "10")
            ),
            daily_loss_limit_percent=float(
                os.getenv("RISK_DAILY_LOSS_PCT", "5.0")
            ),
            circuit_failure_threshold=int(
                os.getenv("RISK_CIRCUIT_FAILURES", "3")
            ),
            circuit_recovery_timeout_seconds=int(
                os.getenv("RISK_CIRCUIT_RECOVERY_SEC", "60")
            ),
        ),
        # Capital sub-config
        capital=CapitalConfig(
            max_exposure_per_market_percent=float(
                os.getenv("CAPITAL_MAX_EXPOSURE_PCT", "5.0")
            ),
            max_exposure_per_market_absolute=float(
                os.getenv("CAPITAL_MAX_EXPOSURE_ABS", "50.0")
            ),
            max_total_exposure_percent=float(
                os.getenv("CAPITAL_MAX_TOTAL_PCT", "30.0")
            ),
            min_order_size=float(os.getenv("CAPITAL_MIN_ORDER_SIZE", "1.0")),
            order_split_threshold=float(
                os.getenv("CAPITAL_SPLIT_THRESHOLD", "20.0")
            ),
            order_split_count=int(
                os.getenv("CAPITAL_SPLIT_COUNT", "3")
            ),
            recycle_delay_seconds=float(
                os.getenv("CAPITAL_RECYCLE_DELAY_SEC", "5.0")
            ),
        ),
        # Metrics sub-config
        metrics=MetricsConfig(
            aggregation_interval_seconds=int(
                os.getenv("METRICS_AGGREGATION_SEC", "60")
            ),
            dashboard_refresh_seconds=int(
                os.getenv("METRICS_REFRESH_SEC", "5")
            ),
            history_hours=int(os.getenv("METRICS_HISTORY_HOURS", "24")),
            fill_rate_warning_threshold=float(
                os.getenv("METRICS_FILL_RATE_WARNING", "0.5")
            ),
            latency_warning_ms=float(
                os.getenv("METRICS_LATENCY_WARNING_MS", "50.0")
            ),
        ),
        # Scheduler sub-config
        scheduler=SchedulerConfig(
            time_to_eligibility_sec=int(
                os.getenv("SCHEDULER_ELIGIBILITY_SEC", "60")
            ),
            max_failures_before_hold=int(
                os.getenv("SCHEDULER_MAX_FAILURES", "3")
            ),
            max_buy_price=float(os.getenv("SCHEDULER_MAX_BUY_PRICE", "0.99")),
            max_watchlist_size=int(
                os.getenv("SCHEDULER_MAX_WATCHLIST", "50")
            ),
            max_active_executions=int(
                os.getenv("SCHEDULER_MAX_EXECUTIONS", "5")
            ),
            execution_window_seconds=int(
                os.getenv("SCHEDULER_EXECUTION_WINDOW_SEC", "3")
            ),
            priming_window_seconds=int(
                os.getenv("SCHEDULER_PRIMING_WINDOW_SEC", "15")
            ),
            eligible_window_seconds=int(
                os.getenv("SCHEDULER_ELIGIBLE_WINDOW_SEC", "60")
            ),
            tick_interval_ms=int(os.getenv("SCHEDULER_TICK_INTERVAL_MS", "10")),
            max_spread_percent=float(
                os.getenv("SCHEDULER_MAX_SPREAD_PCT", "5.0")
            ),
            min_liquidity_usd=float(
                os.getenv("SCHEDULER_MIN_LIQUIDITY_USD", "100.0")
            ),
            min_probability=float(
                os.getenv("SCHEDULER_MIN_PROBABILITY", "0.95")
            ),
        ),
        # Scanner sub-config
        scanner=ScannerConfig(
            min_time_to_expiry_seconds=int(
                os.getenv("SCANNER_MIN_EXPIRY_SEC", "60")
            ),
            max_time_to_expiry_hours=int(
                os.getenv("SCANNER_MAX_EXPIRY_HOURS", "24")
            ),
            min_volume_usd=float(os.getenv("SCANNER_MIN_VOLUME", "100.0")),
            scan_interval_seconds=int(
                os.getenv("SCANNER_INTERVAL_SEC", "300")
            ),
            categories=os.getenv("SCANNER_CATEGORIES", "").split(",")
            if os.getenv("SCANNER_CATEGORIES")
            else [],
            markets_per_request=int(
                os.getenv("SCANNER_MARKETS_PER_REQUEST", "100")
            ),
            max_markets_to_track=int(
                os.getenv("SCANNER_MAX_MARKETS", "100")
            ),
            max_api_failures=int(os.getenv("SCANNER_MAX_API_FAILURES", "3")),
        ),
        # Feed sub-config
        feed=FeedConfig(
            ws_url=os.getenv(
                "FEED_WS_URL",
                "wss://ws-subscriptions-clob.polymarket.com/ws/",
            ),
            reconnect_timeout_seconds=int(
                os.getenv("FEED_RECONNECT_TIMEOUT_SEC", "10")
            ),
            max_reconnect_attempts=int(
                os.getenv("FEED_MAX_RECONNECTS", "5")
            ),
            max_price_update_interval_ms=int(
                os.getenv("FEED_MAX_UPDATE_INTERVAL_MS", "500")
            ),
            max_buffered_ticks=int(
                os.getenv("FEED_MAX_BUFFERED_TICKS", "1000")
            ),
            buffer_flush_interval_ms=int(
                os.getenv("FEED_BUFFER_FLUSH_INTERVAL_MS", "100")
            ),
        ),
        # Notifications sub-config
        notifications=NotificationConfig(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            telegram_enabled=os.getenv("TELEGRAM_ENABLED", "false").lower()
            == "true",
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            discord_enabled=os.getenv("DISCORD_ENABLED", "false").lower()
            == "true",
            alert_on_kill_switch=os.getenv(
                "ALERT_KILL_SWITCH", "true"
            ).lower()
            == "true",
            alert_on_circuit_trip=os.getenv(
                "ALERT_CIRCUIT_TRIP", "true"
            ).lower()
            == "true",
            alert_on_daily_loss=os.getenv(
                "ALERT_DAILY_LOSS", "true"
            ).lower()
            == "true",
        ),
        # Rate limits
        public_rate_limit=int(os.getenv("PUBLIC_RATE_LIMIT", "100")),
        trading_rate_limit=int(os.getenv("TRADING_RATE_LIMIT", "60")),
    )


def validate_config(config: ScalingConfig) -> List[str]:
    """
    Validate configuration against constraints.

    Args:
        config: ScalingConfig to validate

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    # Credentials
    if not config.private_key:
        errors.append("private_key is required")
    if not config.wallet_address:
        errors.append("wallet_address is required")

    # Risk constraints
    if config.risk.stale_feed_threshold_ms < 0:
        errors.append("risk.stale_feed_threshold_ms must be >= 0")
    if config.risk.daily_loss_limit_percent <= 0 or config.risk.daily_loss_limit_percent > 100:
        errors.append("risk.daily_loss_limit_percent must be between 0 and 100")
    if config.risk.circuit_failure_threshold < 1:
        errors.append("risk.circuit_failure_threshold must be >= 1")

    # Capital constraints
    if config.capital.max_exposure_per_market_percent <= 0 or config.capital.max_exposure_per_market_percent > 100:
        errors.append("capital.max_exposure_per_market_percent must be between 0 and 100")
    if config.capital.max_total_exposure_percent <= 0 or config.capital.max_total_exposure_percent > 100:
        errors.append("capital.max_total_exposure_percent must be between 0 and 100")
    if config.capital.min_order_size <= 0:
        errors.append("capital.min_order_size must be > 0")
    if config.capital.order_split_count < 1:
        errors.append("capital.order_split_count must be >= 1")

    # Scheduler constraints
    if config.scheduler.time_to_eligibility_sec < 0:
        errors.append("scheduler.time_to_eligibility_sec must be >= 0")
    if config.scheduler.max_buy_price < 0 or config.scheduler.max_buy_price > 1:
        errors.append("scheduler.max_buy_price must be between 0 and 1")
    if config.scheduler.max_watchlist_size < 1:
        errors.append("scheduler.max_watchlist_size must be >= 1")
    if config.scheduler.max_active_executions < 1:
        errors.append("scheduler.max_active_executions must be >= 1")
    if config.scheduler.max_active_executions > config.scheduler.max_watchlist_size:
        errors.append("scheduler.max_active_executions cannot exceed max_watchlist_size")

    # Scanner constraints
    if config.scanner.min_time_to_expiry_seconds < 0:
        errors.append("scanner.min_time_to_expiry_seconds must be >= 0")
    if config.scanner.max_time_to_expiry_hours < 1:
        errors.append("scanner.max_time_to_expiry_hours must be >= 1")
    if config.scanner.min_volume_usd < 0:
        errors.append("scanner.min_volume_usd must be >= 0")
    if config.scanner.max_markets_to_track < 1:
        errors.append("scanner.max_markets_to_track must be >= 1")

    # Feed constraints
    if config.feed.reconnect_timeout_seconds < 1:
        errors.append("feed.reconnect_timeout_seconds must be >= 1")
    if config.feed.max_reconnect_attempts < 0:
        errors.append("feed.max_reconnect_attempts must be >= 0")

    return errors


# ============================================================================
# Preset Profiles
# ============================================================================

CONSERVATIVE_PROFILE = ScalingConfig(
    log_level="INFO",
    dry_run=False,
    risk=RiskConfig(
        daily_loss_limit_percent=2.0,
        max_outstanding_orders=5,
        stale_feed_threshold_ms=300,
    ),
    capital=CapitalConfig(
        max_exposure_per_market_percent=2.0,
        max_exposure_per_market_absolute=20.0,
        max_total_exposure_percent=10.0,
    ),
    scheduler=SchedulerConfig(
        max_active_executions=2,
        max_watchlist_size=25,
        max_buy_price=0.95,
    ),
    scanner=ScannerConfig(
        min_volume_usd=500.0,
        max_markets_to_track=50,
    ),
)

AGGRESSIVE_PROFILE = ScalingConfig(
    log_level="INFO",
    dry_run=False,
    risk=RiskConfig(
        daily_loss_limit_percent=10.0,
        max_outstanding_orders=20,
        stale_feed_threshold_ms=1000,
    ),
    capital=CapitalConfig(
        max_exposure_per_market_percent=10.0,
        max_exposure_per_market_absolute=100.0,
        max_total_exposure_percent=50.0,
    ),
    scheduler=SchedulerConfig(
        max_active_executions=10,
        max_watchlist_size=100,
        max_buy_price=0.99,
    ),
    scanner=ScannerConfig(
        min_volume_usd=10.0,
        max_markets_to_track=100,
    ),
)

PAPER_TRADING_PROFILE = ScalingConfig(
    log_level="DEBUG",
    dry_run=True,
    risk=RiskConfig(
        daily_loss_limit_percent=100.0,  # No limit for paper trading
        max_outstanding_orders=100,
    ),
    capital=CapitalConfig(
        max_total_exposure_percent=100.0,  # No limit for testing
    ),
    scheduler=SchedulerConfig(
        max_active_executions=20,
        max_watchlist_size=100,
    ),
    scanner=ScannerConfig(
        min_volume_usd=0.0,  # Accept any volume
        max_markets_to_track=100,
    ),
)
