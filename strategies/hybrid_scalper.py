"""
Directional Scalper Strategy v4
===============================

Based on distinct-baguette's CURRENT trading pattern (200 recent trades):
- Buys at EXTREMES: LOW (10-35¢) or HIGH (85-95¢)
- Often holds to expiry (24% of trades)
- Accepts big losses (-30¢ avg) for big wins (+35¢ avg)
- 27% win rate but larger win size compensates

Strategy:
1. Buy LOW (10-35¢) - betting on price going up
2. Buy HIGH (85-95¢) - betting on staying high/going to 100¢
3. Wide profit target (+20¢) or hold to expiry
4. Wide stop loss (-15¢) or hold through
"""

import asyncio
import time
import logging
import json
import os
import aiohttp
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

from strategies.base_strategy import BaseStrategy
from config import GAMMA_API, CLOB_HOST
from executor import OrderRequest

# RAG learning system imports
try:
    from rag.knowledge_store import KnowledgeStore
    from rag.learning_capture import LearningCapture
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False

# Notifications
try:
    from utils.notifications import Notifier
    NOTIFIER_AVAILABLE = True
except ImportError:
    NOTIFIER_AVAILABLE = False

logger = logging.getLogger(__name__)

# Trade journal directory
JOURNAL_DIR = Path("data/trade_journal")
JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class HybridScalperConfig:
    """Configuration for directional scalper v4"""

    # V4: Two entry zones at EXTREMES
    # LOW zone (10-35¢) - buy expecting price to rise
    low_zone_min: float = 0.10
    low_zone_max: float = 0.35

    # HIGH zone (85-95¢) - buy expecting to stay high or hit 100¢
    high_zone_min: float = 0.85
    high_zone_max: float = 0.95

    # V4: Wider targets (distinct-baguette accepts big swings)
    profit_target_cents: float = 0.20   # Exit at +20¢ profit
    stop_loss_cents: float = 0.15       # Wide stop at -15¢ (or 0 = hold through)

    # Hold to expiry mode (like distinct-baguette does 24% of time)
    hold_to_expiry: bool = False        # If True, no early exits

    # Position sizing
    position_size_usd: float = 1.0      # $1 per trade (user requirement)
    max_positions: int = 20

    # Timing
    timeout_seconds: int = 600          # 10 min max hold (longer than v3)
    scan_interval_sec: float = 1.0
    min_time_to_expiry_sec: int = 120

    # Assets
    supported_assets: List[str] = field(
        default_factory=lambda: ["btc", "eth", "sol", "xrp"]
    )

    # Market type
    market_window_minutes: int = 15  # 15-minute markets


@dataclass
class MarketPair:
    """Represents a crypto Up/Down market pair"""

    slug: str
    asset: str
    expiry_timestamp: int

    # Token IDs
    up_token_id: str
    down_token_id: str

    # Current prices
    up_price: float = 0.0
    down_price: float = 0.0

    # Metadata
    condition_id: str = ""
    question: str = ""

    @property
    def time_to_expiry(self) -> float:
        """Seconds until expiry"""
        return self.expiry_timestamp - time.time()

    @property
    def is_active(self) -> bool:
        """Check if market is active"""
        return self.time_to_expiry > 0


@dataclass
class ScalperPosition:
    """Tracks a position for scalping"""

    token_id: str
    side: str  # "Up" or "Down"
    entry_price: float
    shares: float
    entry_time: float
    market_slug: str
    asset: str

    @property
    def hold_time(self) -> float:
        """Seconds since entry"""
        return time.time() - self.entry_time


@dataclass
class TradeOutcome:
    """Detailed trade outcome for RAG learning"""

    # Trade identification
    trade_id: str
    timestamp: str
    asset: str
    side: str  # "Up" or "Down"
    market_slug: str

    # Entry details
    entry_price: float
    entry_time: str
    shares: float
    cost_usd: float

    # Exit details
    exit_type: str  # "scalp", "stop_loss", "expiry_win", "expiry_loss", "hold"
    exit_price: float
    exit_time: str
    hold_duration_sec: float

    # Results
    profit_usd: float
    profit_pct: float
    outcome: str  # "win", "loss", "break_even"

    # Market context at entry
    market_up_price_at_entry: float
    market_down_price_at_entry: float
    time_to_expiry_at_entry: float

    # Market context at exit
    market_up_price_at_exit: float
    market_down_price_at_exit: float

    # Strategy parameters used
    scalp_target_cents: float
    stop_loss_cents: float

    # Learning tags
    tags: List[str] = field(default_factory=list)
    notes: str = ""


class HybridScalper(BaseStrategy):
    """
    Directional Scalper Strategy v4 - Trades at price extremes.

    Based on distinct-baguette's CURRENT trading pattern:
    1. Buy LOW (10-35¢) - betting on price rising
    2. Buy HIGH (85-95¢) - betting on staying high / hitting 100¢
    3. Wide profit target (+20¢) or hold to expiry
    4. Wide stop loss (-15¢) or hold through losses
    """

    def __init__(
        self,
        config,
        executor,
        scalper_config: Optional[HybridScalperConfig] = None,
    ):
        super().__init__(config, name="HybridScalper")

        self.executor = executor
        self.scalper_config = scalper_config or HybridScalperConfig()

        # Position tracking
        self.positions: Dict[str, ScalperPosition] = {}  # token_id -> position

        # Market cache
        self.active_markets: Dict[str, MarketPair] = {}
        self.last_discovery: float = 0
        self.discovery_interval: float = 30.0

        # Price history for momentum detection
        self.price_history: Dict[str, List[tuple]] = {}  # token_id -> [(time, price)]

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Metrics
        self.trades_executed = 0
        self.scalps_executed = 0
        self.expiry_wins = 0
        self.expiry_losses = 0
        self.total_profit = 0.0

        # Trade journal for RAG learning
        self.trade_journal: List[TradeOutcome] = []
        self.trade_counter = 0
        self.window_outcomes: Dict[str, Dict] = {}  # slug -> outcome data
        self.expired_windows: List[str] = []  # Track windows we've logged

        # RAG learning system (initialized async in run())
        self.knowledge_store: Optional[Any] = None
        self.learning_capture: Optional[Any] = None
        self.winning_patterns: Dict[str, float] = {}  # tag combo -> confidence score
        self.veto_rules: Dict[str, Dict] = {}  # tag combo -> veto rule data
        self._rag_initialized = False

        # Notifications for veto alerts
        self.notifier: Optional[Any] = None
        self._veto_alert_count = 0  # Track vetoes this session

        hold_mode = "HOLD TO EXPIRY" if self.scalper_config.hold_to_expiry else f"+{self.scalper_config.profit_target_cents*100:.0f}¢/-{self.scalper_config.stop_loss_cents*100:.0f}¢"
        logger.info(
            f"HybridScalper v4 initialized | "
            f"LOW zone: {self.scalper_config.low_zone_min:.2f}-{self.scalper_config.low_zone_max:.2f} | "
            f"HIGH zone: {self.scalper_config.high_zone_min:.2f}-{self.scalper_config.high_zone_max:.2f} | "
            f"Exit: {hold_mode} | "
            f"Position: ${self.scalper_config.position_size_usd}"
        )

    async def run(self):
        """Main strategy loop"""
        await self.validate_config()

        # Initialize RAG learning system
        await self._initialize_rag()

        # Initialize notifier for veto alerts
        if NOTIFIER_AVAILABLE:
            try:
                from config import config
                self.notifier = Notifier(
                    telegram_token=getattr(config, 'telegram_token', None),
                    telegram_chat=getattr(config, 'telegram_chat_id', None),
                    discord_webhook=getattr(config, 'discord_webhook_url', None),
                )
            except Exception as e:
                logger.debug(f"Notifier not configured: {e}")

        headers = {
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        }
        self._session = aiohttp.ClientSession(headers=headers)

        try:
            while self.running:
                try:
                    # 1. Discover active markets
                    await self._discover_markets()

                    # 2. Update prices from order books
                    await self._update_prices()

                    # 3. Look for entry opportunities
                    await self._check_entries()

                    # 4. Manage existing positions (scalp exits)
                    await self._manage_positions()

                    # 5. Check for expired windows and log outcomes
                    await self._check_expired_windows()

                    # 6. Log status periodically
                    await self._log_status()

                    # 7. Send veto alerts if needed
                    await self._send_veto_alert_if_needed()

                    await asyncio.sleep(self.scalper_config.scan_interval_sec)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"HybridScalper loop error: {e}", exc_info=True)
                    await asyncio.sleep(5)

        finally:
            if self._session:
                await self._session.close()

    async def cleanup(self):
        """Cleanup resources and generate RAG summary"""
        # Generate RAG learning summary before cleanup
        if self.trade_journal:
            summary = self.generate_rag_summary()
            logger.info(
                f"📊 Session Summary | Trades: {summary.get('total_trades', 0)} | "
                f"Win Rate: {summary.get('win_rate', 0)*100:.1f}% | "
                f"P&L: ${summary.get('total_pnl', 0):.2f}"
            )
            for learning in summary.get('learnings', []):
                logger.info(f"   📝 {learning}")

        if self._session:
            await self._session.close()

        # Close RAG knowledge store
        if self.knowledge_store:
            await self.knowledge_store.close()

        logger.info("HybridScalper cleaned up")

    async def _initialize_rag(self):
        """Initialize RAG learning system at startup"""
        if not RAG_AVAILABLE:
            logger.warning("RAG system not available - continuing without learning")
            return

        try:
            # Initialize knowledge store
            self.knowledge_store = KnowledgeStore(
                persist_directory="data/rag",
                collection_name="trading_learnings",
            )
            await self.knowledge_store.initialize()

            # Initialize learning capture
            self.learning_capture = LearningCapture(self.knowledge_store)

            # Load winning patterns from history
            await self._load_winning_patterns()

            # Load veto rules from error patterns
            await self._load_veto_rules()

            self._rag_initialized = True

            stats = await self.knowledge_store.get_stats()
            logger.info(
                f"RAG learning system initialized | "
                f"Backend: {stats.get('backend', 'unknown')} | "
                f"Learnings: {stats.get('total_learnings', 0)} | "
                f"Winning patterns: {len(self.winning_patterns)} | "
                f"Veto rules: {len(self.veto_rules)}"
            )

        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e} - continuing without learning")
            self._rag_initialized = False

    async def _load_winning_patterns(self):
        """Load high-confidence winning patterns from knowledge store for reinforcement"""
        if not self.knowledge_store:
            return

        try:
            # Search for successful patterns with high confidence
            results = await self.knowledge_store.search_learnings(
                query="successful trade pattern profit",
                learning_type="successful_pattern",
                n_results=50,
            )

            for result in results:
                metadata = result.get("metadata", {})
                profit_pct = metadata.get("profit_pct", 0)
                tags_str = metadata.get("tags", "")

                # Only include patterns with 60%+ implied win rate (profit_pct > threshold)
                if profit_pct >= 0.02:  # 2% profit threshold for "good" pattern
                    # Use tags as pattern key
                    if tags_str:
                        self.winning_patterns[tags_str] = max(
                            self.winning_patterns.get(tags_str, 0),
                            min(1.0, profit_pct * 10)  # Scale to 0-1 confidence
                        )

            logger.info(f"Loaded {len(self.winning_patterns)} high-confidence patterns")

        except Exception as e:
            logger.debug(f"Failed to load winning patterns: {e}")

    async def _load_veto_rules(self):
        """Load error patterns from knowledge store for entry veto (startup only)"""
        if not self.knowledge_store:
            return

        try:
            # Search for error patterns with multiple occurrences
            results = await self.knowledge_store.search_learnings(
                query="loss pattern error stop_loss",
                learning_type="error_pattern",
                n_results=50,
            )

            # Count occurrences by tag combination
            pattern_counts: Dict[str, Dict] = {}
            for result in results:
                metadata = result.get("metadata", {})
                tags_str = metadata.get("tags", "")
                profit_pct = metadata.get("profit_pct", 0)

                if tags_str:
                    if tags_str not in pattern_counts:
                        pattern_counts[tags_str] = {"count": 0, "total_loss": 0, "losses": 0}

                    pattern_counts[tags_str]["count"] += 1
                    if profit_pct < 0:
                        pattern_counts[tags_str]["losses"] += 1
                        pattern_counts[tags_str]["total_loss"] += abs(profit_pct)

            # Create veto rules for patterns with 5+ occurrences and 60%+ loss rate
            for tags_str, stats in pattern_counts.items():
                if stats["count"] >= 5:
                    loss_rate = stats["losses"] / stats["count"]
                    if loss_rate >= 0.60:
                        self.veto_rules[tags_str] = {
                            "count": stats["count"],
                            "loss_rate": loss_rate,
                            "avg_loss": stats["total_loss"] / stats["losses"] if stats["losses"] > 0 else 0,
                        }

            if self.veto_rules:
                logger.warning(f"Loaded {len(self.veto_rules)} veto rules - will block matching entries")

        except Exception as e:
            logger.debug(f"Failed to load veto rules: {e}")

    def _generate_market_slugs(self) -> List[str]:
        """Generate slugs for active 15-min crypto markets"""
        now = int(time.time())
        window_sec = self.scalper_config.market_window_minutes * 60

        # Current window end
        current_end = ((now // window_sec) + 1) * window_sec
        # Next window end
        next_end = current_end + window_sec

        slugs = []
        for asset in self.scalper_config.supported_assets:
            slugs.append(f"{asset}-updown-{self.scalper_config.market_window_minutes}m-{current_end}")
            slugs.append(f"{asset}-updown-{self.scalper_config.market_window_minutes}m-{next_end}")

        return slugs

    async def _discover_markets(self):
        """Discover active crypto markets"""
        if time.time() - self.last_discovery < self.discovery_interval:
            return

        self.last_discovery = time.time()
        slugs = self._generate_market_slugs()

        for slug in slugs:
            if slug in self.active_markets:
                continue

            try:
                market = await self._fetch_market(slug)
                if market and market.is_active:
                    self.active_markets[slug] = market
                    logger.info(
                        f"Discovered: {market.asset} | "
                        f"Up: {market.up_price:.2f} | Down: {market.down_price:.2f} | "
                        f"Expires: {market.time_to_expiry:.0f}s"
                    )
            except Exception as e:
                logger.debug(f"Failed to fetch {slug}: {e}")

        # Remove expired markets
        expired = [s for s, m in self.active_markets.items() if m.time_to_expiry <= 0]
        for slug in expired:
            del self.active_markets[slug]
            logger.debug(f"Removed expired: {slug}")

    async def _fetch_market(self, slug: str) -> Optional[MarketPair]:
        """Fetch market data from Gamma API"""
        url = f"{GAMMA_API}/events?slug={slug}"

        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                if not data:
                    return None

                event = data[0]
                markets = event.get("markets", [])

                if not markets:
                    return None

                market = markets[0]

                # Parse JSON string fields
                outcomes_raw = market.get("outcomes", [])
                if isinstance(outcomes_raw, str):
                    outcomes = json.loads(outcomes_raw)
                else:
                    outcomes = outcomes_raw

                tokens_raw = market.get("clobTokenIds", [])
                if isinstance(tokens_raw, str):
                    token_ids = json.loads(tokens_raw)
                else:
                    token_ids = tokens_raw

                prices_raw = market.get("outcomePrices", [])
                if isinstance(prices_raw, str):
                    prices = json.loads(prices_raw)
                else:
                    prices = prices_raw

                if len(outcomes) < 2 or len(token_ids) < 2:
                    return None

                # Find Up and Down indices
                up_idx = None
                down_idx = None
                for i, outcome in enumerate(outcomes):
                    if outcome.lower() == "up":
                        up_idx = i
                    elif outcome.lower() == "down":
                        down_idx = i

                if up_idx is None or down_idx is None:
                    return None

                parts = slug.split("-")
                expiry_ts = int(parts[-1])
                asset = parts[0].upper()

                up_price = float(prices[up_idx]) if len(prices) > up_idx else 0.0
                down_price = float(prices[down_idx]) if len(prices) > down_idx else 0.0

                return MarketPair(
                    slug=slug,
                    asset=asset,
                    expiry_timestamp=expiry_ts,
                    up_token_id=token_ids[up_idx],
                    down_token_id=token_ids[down_idx],
                    condition_id=market.get("conditionId", ""),
                    question=event.get("title", ""),
                    up_price=up_price,
                    down_price=down_price,
                )

        except Exception as e:
            logger.debug(f"Error fetching {slug}: {e}")
            return None

    async def _update_prices(self):
        """Update prices from CLOB order books.

        Only updates prices if the spread is reasonable (< 50%).
        Wide spreads indicate thin liquidity - keep Gamma API prices.
        """
        MAX_SPREAD = 0.50  # Don't trust order book if spread > 50%

        for slug, market in self.active_markets.items():
            try:
                # Fetch best prices from order book
                up_book = await self._fetch_order_book(market.up_token_id)
                down_book = await self._fetch_order_book(market.down_token_id)

                # Only update Up price if spread is reasonable
                if up_book and up_book.get("best_bid") and up_book.get("best_ask"):
                    bid = float(up_book["best_bid"])
                    ask = float(up_book["best_ask"])
                    spread = ask - bid
                    if spread < MAX_SPREAD:
                        # Use mid-price for decision making
                        market.up_price = (bid + ask) / 2
                    # else: keep Gamma API price

                # Only update Down price if spread is reasonable
                if down_book and down_book.get("best_bid") and down_book.get("best_ask"):
                    bid = float(down_book["best_bid"])
                    ask = float(down_book["best_ask"])
                    spread = ask - bid
                    if spread < MAX_SPREAD:
                        market.down_price = (bid + ask) / 2
                    # else: keep Gamma API price

                # Track price history
                now = time.time()
                if market.up_token_id not in self.price_history:
                    self.price_history[market.up_token_id] = []
                self.price_history[market.up_token_id].append((now, market.up_price))

                if market.down_token_id not in self.price_history:
                    self.price_history[market.down_token_id] = []
                self.price_history[market.down_token_id].append((now, market.down_price))

                # Keep only last 60 seconds of history
                for token_id in [market.up_token_id, market.down_token_id]:
                    self.price_history[token_id] = [
                        (t, p) for t, p in self.price_history[token_id]
                        if now - t < 60
                    ]

            except Exception as e:
                logger.debug(f"Error updating prices for {slug}: {e}")

    async def _fetch_order_book(self, token_id: str) -> Optional[Dict]:
        """Fetch order book from CLOB API"""
        if not token_id:
            return None

        url = f"{CLOB_HOST}/book"
        params = {"token_id": token_id}

        try:
            async with self._session.get(url, params=params, timeout=5) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()

                best_bid = None
                best_ask = None

                if data.get("bids"):
                    best_bid = float(data["bids"][0]["price"])
                if data.get("asks"):
                    best_ask = float(data["asks"][0]["price"])

                return {"best_bid": best_bid, "best_ask": best_ask}

        except Exception as e:
            logger.debug(f"Error fetching order book: {e}")
            return None

    async def _check_entries(self):
        """Check for entry opportunities - copy distinct-baguette's pattern"""
        if len(self.positions) >= self.scalper_config.max_positions:
            return

        for slug, market in self.active_markets.items():
            # Skip if too close to expiry
            if market.time_to_expiry < self.scalper_config.min_time_to_expiry_sec:
                continue

            # Check for Up entry opportunity
            if self._should_buy_up(market):
                await self._enter_up(market)

            # Check for Down lottery ticket opportunity
            if self._should_buy_down(market):
                await self._enter_down(market)

    def _get_entry_tags(self, price: float, market: MarketPair, side: str) -> List[str]:
        """Generate potential tags for an entry to check against patterns"""
        tags = []

        # Entry zone tag
        if price <= self.scalper_config.low_zone_max:
            tags.append("low_zone_entry")
        elif price >= self.scalper_config.high_zone_min:
            tags.append("high_zone_entry")

        # Time-based tag
        if market.time_to_expiry < 300:
            tags.append("near_expiry")
        elif market.time_to_expiry > 600:
            tags.append("early_window")

        return tags

    def _get_entry_confidence(self, market: MarketPair, side: str) -> float:
        """
        Calculate confidence score for entry (0.0-1.0).
        Uses winning patterns to boost confidence for historically successful entries.
        """
        price = market.up_price if side == "Up" else market.down_price
        tags = self._get_entry_tags(price, market, side)
        tags_str = ",".join(sorted(tags))

        # Base confidence of 0.5 (neutral)
        confidence = 0.5

        # Boost if matching winning pattern
        if tags_str in self.winning_patterns:
            confidence += 0.1 * self.winning_patterns[tags_str]

        # Boost for early_window entries (more time for position to work)
        if "early_window" in tags and any("early_window" in k for k in self.winning_patterns):
            confidence += 0.05

        # Cap at 1.0
        return min(1.0, confidence)

    def _is_vetoed_entry(self, market: MarketPair, side: str) -> bool:
        """
        Check if entry matches a veto rule.
        Returns True if entry should be blocked.
        """
        if not self.veto_rules:
            return False

        price = market.up_price if side == "Up" else market.down_price
        tags = self._get_entry_tags(price, market, side)
        tags_str = ",".join(sorted(tags))

        if tags_str in self.veto_rules:
            rule = self.veto_rules[tags_str]
            self._veto_alert_count += 1
            logger.warning(
                f"🚫 VETO: {market.asset} {side} blocked | "
                f"Pattern: {tags_str} | Loss rate: {rule['loss_rate']*100:.0f}% "
                f"({rule['count']} occurrences) | Session vetoes: {self._veto_alert_count}"
            )
            return True

        return False

    async def _send_veto_alert_if_needed(self):
        """Send alert if significant number of vetoes occurred (called periodically)"""
        if not self.notifier or self._veto_alert_count == 0:
            return

        # Alert every 10 vetoes
        if self._veto_alert_count % 10 == 0:
            await self.notifier.notify(
                f"🚫 Veto Summary: {self._veto_alert_count} entries blocked this session\n"
                f"Active veto rules: {len(self.veto_rules)}",
                title="RAG Veto Alert"
            )

    def _should_buy_up(self, market: MarketPair) -> bool:
        """
        V4: Enter Up in two scenarios:
        1. HIGH zone (85-95¢) - momentum play, expecting to stay high or hit 100¢
        2. LOW zone (10-35¢) - value play, expecting reversal upward

        With RAG learning:
        - Check veto rules first
        - In live mode, require minimum confidence
        """
        if market.up_token_id in self.positions:
            return False

        price = market.up_price
        if price <= 0:
            return False

        # V4: Two entry zones at extremes
        in_low_zone = self.scalper_config.low_zone_min <= price <= self.scalper_config.low_zone_max
        in_high_zone = self.scalper_config.high_zone_min <= price <= self.scalper_config.high_zone_max

        if not (in_low_zone or in_high_zone):
            return False

        # Check veto rules
        if self._is_vetoed_entry(market, "Up"):
            return False

        # In live mode with RAG enabled, require minimum confidence
        if self._rag_initialized and not self.config.dry_run:
            confidence = self._get_entry_confidence(market, "Up")
            if confidence < 0.5:
                logger.debug(
                    f"Low confidence ({confidence:.2f}) for {market.asset} Up - skipping"
                )
                return False

        return True

    def _should_buy_down(self, market: MarketPair) -> bool:
        """
        V4: Enter Down in two scenarios:
        1. HIGH zone (85-95¢) - momentum play, expecting to stay high or hit 100¢
        2. LOW zone (10-35¢) - value play, expecting reversal upward

        With RAG learning:
        - Check veto rules first
        - In live mode, require minimum confidence
        """
        if market.down_token_id in self.positions:
            return False

        price = market.down_price
        if price <= 0:
            return False

        # V4: Two entry zones at extremes
        in_low_zone = self.scalper_config.low_zone_min <= price <= self.scalper_config.low_zone_max
        in_high_zone = self.scalper_config.high_zone_min <= price <= self.scalper_config.high_zone_max

        if not (in_low_zone or in_high_zone):
            return False

        # Check veto rules
        if self._is_vetoed_entry(market, "Down"):
            return False

        # In live mode with RAG enabled, require minimum confidence
        if self._rag_initialized and not self.config.dry_run:
            confidence = self._get_entry_confidence(market, "Down")
            if confidence < 0.5:
                logger.debug(
                    f"Low confidence ({confidence:.2f}) for {market.asset} Down - skipping"
                )
                return False

        return True


    async def _enter_up(self, market: MarketPair):
        """Enter Up position"""
        size_usd = self.scalper_config.position_size_usd
        shares = size_usd / market.up_price

        logger.info(
            f"🔼 BUY {market.asset} Up @ {market.up_price:.2f}¢ | "
            f"{shares:.1f} shares | ${size_usd:.2f}"
        )

        if self.config.dry_run:
            # Simulate the trade
            self.positions[market.up_token_id] = ScalperPosition(
                token_id=market.up_token_id,
                side="Up",
                entry_price=market.up_price,
                shares=shares,
                entry_time=time.time(),
                market_slug=market.slug,
                asset=market.asset,
            )
            self.trades_executed += 1
        else:
            # Execute real trade via executor
            if self.executor:
                order = OrderRequest(
                    token_id=market.up_token_id,
                    side="YES",  # Up = YES outcome
                    action="BUY",
                    size=shares,
                    strategy="hybrid_scalper_v3",
                    metadata={"asset": market.asset, "market_slug": market.slug}
                )
                success = await self.executor.execute_order(order)
                if success:
                    self.positions[market.up_token_id] = ScalperPosition(
                        token_id=market.up_token_id,
                        side="Up",
                        entry_price=market.up_price,
                        shares=shares,
                        entry_time=time.time(),
                        market_slug=market.slug,
                        asset=market.asset,
                    )
                    self.trades_executed += 1

    async def _enter_down(self, market: MarketPair):
        """Enter Down position"""
        size_usd = self.scalper_config.position_size_usd
        shares = size_usd / market.down_price

        logger.info(
            f"🔽 BUY {market.asset} Down @ {market.down_price:.2f}¢ | "
            f"{shares:.1f} shares | ${size_usd:.2f}"
        )

        if self.config.dry_run:
            self.positions[market.down_token_id] = ScalperPosition(
                token_id=market.down_token_id,
                side="Down",
                entry_price=market.down_price,
                shares=shares,
                entry_time=time.time(),
                market_slug=market.slug,
                asset=market.asset,
            )
            self.trades_executed += 1
        else:
            if self.executor:
                order = OrderRequest(
                    token_id=market.down_token_id,
                    side="YES",  # Down token = YES outcome for that token
                    action="BUY",
                    size=shares,
                    strategy="hybrid_scalper_v3",
                    metadata={"asset": market.asset, "market_slug": market.slug}
                )
                success = await self.executor.execute_order(order)
                if success:
                    self.positions[market.down_token_id] = ScalperPosition(
                        token_id=market.down_token_id,
                        side="Down",
                        entry_price=market.down_price,
                        shares=shares,
                        entry_time=time.time(),
                        market_slug=market.slug,
                        asset=market.asset,
                    )
                    self.trades_executed += 1

    async def _manage_positions(self):
        """
        V4: Manage positions with flexible exit strategy.
        - If hold_to_expiry=True: Only exit at market resolution
        - Otherwise: Exit on profit target (+20¢), stop loss (-15¢), or timeout
        """
        to_close: List[tuple] = []

        for token_id, position in self.positions.items():
            current_price = self._get_current_price(token_id)
            if current_price is None:
                continue

            market = self._get_market_for_token(token_id)
            profit_cents = current_price - position.entry_price

            # HOLD TO EXPIRY MODE: Skip all early exits
            if self.scalper_config.hold_to_expiry:
                # Only exit handled by _check_expired_windows
                continue

            # SCALP: Exit if profit target hit (+20¢ in v4)
            if profit_cents >= self.scalper_config.profit_target_cents:
                profit_usd = profit_cents * position.shares
                profit_pct = profit_cents / position.entry_price if position.entry_price > 0 else 0
                logger.info(
                    f"💰 SCALP {position.asset} {position.side} | "
                    f"Entry: {position.entry_price:.2f} → Exit: {current_price:.2f} | "
                    f"Profit: ${profit_usd:.2f} (+{profit_cents*100:.0f}¢)"
                )
                to_close.append((token_id, "scalp", current_price, profit_usd, profit_pct, market))
                self.scalps_executed += 1
                self.total_profit += profit_usd
                continue

            # STOP LOSS: Exit if down too much (-15¢ in v4, or skip if 0)
            if self.scalper_config.stop_loss_cents > 0 and profit_cents <= -self.scalper_config.stop_loss_cents:
                loss_usd = abs(profit_cents) * position.shares
                profit_pct = profit_cents / position.entry_price if position.entry_price > 0 else 0
                logger.warning(
                    f"🛑 STOP LOSS {position.asset} {position.side} | "
                    f"Entry: {position.entry_price:.2f} → Exit: {current_price:.2f} | "
                    f"Loss: ${loss_usd:.2f} (-{abs(profit_cents)*100:.0f}¢)"
                )
                to_close.append((token_id, "stop_loss", current_price, -loss_usd, profit_pct, market))
                self.total_profit -= loss_usd
                continue

            # TIMEOUT: Exit if held too long (10 min in v4)
            if position.hold_time > self.scalper_config.timeout_seconds:
                profit_usd = profit_cents * position.shares
                profit_pct = profit_cents / position.entry_price if position.entry_price > 0 else 0
                logger.info(
                    f"⏰ TIMEOUT {position.asset} {position.side} | "
                    f"Entry: {position.entry_price:.2f} → Exit: {current_price:.2f} | "
                    f"P&L: ${profit_usd:.2f}"
                )
                to_close.append((token_id, "timeout", current_price, profit_usd, profit_pct, market))
                self.total_profit += profit_usd
                continue

        # Close positions and log outcomes
        for token_id, exit_type, exit_price, profit_usd, profit_pct, market in to_close:
            position = self.positions[token_id]

            # Log trade outcome for RAG
            await self._log_trade_outcome(
                position=position,
                exit_type=exit_type,
                exit_price=exit_price,
                profit_usd=profit_usd,
                profit_pct=profit_pct,
                market=market,
            )

            # Execute sell if live
            if not self.config.dry_run and self.executor:
                order = OrderRequest(
                    token_id=token_id,
                    side="YES",  # We're selling the token we hold
                    action="SELL",
                    size=position.shares,
                    strategy="hybrid_scalper",
                    metadata={"exit_type": exit_type, "asset": position.asset}
                )
                await self.executor.execute_order(order)
            del self.positions[token_id]

    def _get_market_for_token(self, token_id: str) -> Optional[MarketPair]:
        """Get market pair for a token"""
        for market in self.active_markets.values():
            if market.up_token_id == token_id or market.down_token_id == token_id:
                return market
        return None

    def _get_current_price(self, token_id: str) -> Optional[float]:
        """Get current price for a token"""
        for market in self.active_markets.values():
            if market.up_token_id == token_id:
                return market.up_price
            if market.down_token_id == token_id:
                return market.down_price
        return None

    async def _log_status(self):
        """Log periodic status update"""
        if not hasattr(self, '_last_status_log'):
            self._last_status_log = 0

        if time.time() - self._last_status_log < 30:
            return

        self._last_status_log = time.time()

        # Build status message
        positions_str = f"{len(self.positions)}/{self.scalper_config.max_positions}"

        logger.info(
            f"📊 Status | Positions: {positions_str} | "
            f"Trades: {self.trades_executed} | Scalps: {self.scalps_executed} | "
            f"P&L: ${self.total_profit:.2f}"
        )

        # Log current market prices
        for slug, market in list(self.active_markets.items())[:4]:
            if market.time_to_expiry > 0:
                logger.info(
                    f"   {market.asset}: Up={market.up_price:.2f} Down={market.down_price:.2f} "
                    f"({market.time_to_expiry:.0f}s)"
                )

    def get_metrics(self) -> Dict[str, Any]:
        """Get strategy metrics"""
        return {
            "runtime_seconds": (datetime.utcnow() - self.start_time).total_seconds() if self.start_time else 0,
            "trades_executed": self.trades_executed,
            "scalps_executed": self.scalps_executed,
            "active_positions": len(self.positions),
            "total_profit": self.total_profit,
            "opportunities_found": self.trades_executed,
        }

    # ========================================
    # Trade Journal for RAG Learning
    # ========================================

    async def _log_trade_outcome(
        self,
        position: ScalperPosition,
        exit_type: str,
        exit_price: float,
        profit_usd: float,
        profit_pct: float,
        market: Optional[MarketPair] = None,
    ):
        """Log detailed trade outcome for RAG learning (async for RAG integration)"""
        self.trade_counter += 1
        trade_id = f"T{self.trade_counter:04d}_{position.asset}_{position.side}_{int(time.time())}"

        # Determine outcome
        if profit_usd > 0.01:
            outcome = "win"
        elif profit_usd < -0.01:
            outcome = "loss"
        else:
            outcome = "break_even"

        # Generate learning tags based on trade characteristics
        tags = []

        # Exit type tags
        if exit_type == "scalp":
            tags.append("successful_scalp")
            if profit_pct > 0.10:
                tags.append("large_scalp_10pct_plus")
        elif exit_type == "stop_loss":
            tags.append("stop_loss_triggered")
        elif exit_type == "timeout":
            tags.append("timeout_exit")
        elif exit_type == "expiry_win":
            tags.append("held_to_resolution")
            tags.append("resolution_win")
        elif exit_type == "expiry_loss":
            tags.append("held_to_resolution")
            tags.append("resolution_loss")

        # Entry zone tags
        if position.entry_price <= self.scalper_config.low_zone_max:
            tags.append("low_zone_entry")
        elif position.entry_price >= self.scalper_config.high_zone_min:
            tags.append("high_zone_entry")

        # Time-based tags (at entry)
        hold_duration = time.time() - position.entry_time
        time_to_expiry_at_entry = (market.time_to_expiry + hold_duration) if market else 0
        if time_to_expiry_at_entry > 0:
            if time_to_expiry_at_entry < 300:  # < 5 min
                tags.append("near_expiry")
            elif time_to_expiry_at_entry > 600:  # > 10 min (early in 15-min window)
                tags.append("early_window")

        # Outcome speed tags
        if hold_duration < 60:
            if outcome == "win":
                tags.append("quick_win")
            elif outcome == "loss":
                tags.append("quick_loss")
            tags.append("quick_exit_under_1min")
        elif hold_duration > 300:
            tags.append("timeout_exit_5min_plus")

        # Get market context
        up_at_exit = market.up_price if market else 0.0
        down_at_exit = market.down_price if market else 0.0

        trade_outcome = TradeOutcome(
            trade_id=trade_id,
            timestamp=datetime.utcnow().isoformat(),
            asset=position.asset,
            side=position.side,
            market_slug=position.market_slug,
            entry_price=position.entry_price,
            entry_time=datetime.fromtimestamp(position.entry_time).isoformat(),
            shares=position.shares,
            cost_usd=position.entry_price * position.shares,
            exit_type=exit_type,
            exit_price=exit_price,
            exit_time=datetime.utcnow().isoformat(),
            hold_duration_sec=hold_duration,
            profit_usd=profit_usd,
            profit_pct=profit_pct,
            outcome=outcome,
            market_up_price_at_entry=position.entry_price if position.side == "Up" else 0.0,
            market_down_price_at_entry=position.entry_price if position.side == "Down" else 0.0,
            time_to_expiry_at_entry=market.time_to_expiry + hold_duration if market else 0.0,
            market_up_price_at_exit=up_at_exit,
            market_down_price_at_exit=down_at_exit,
            scalp_target_cents=self.scalper_config.profit_target_cents,
            stop_loss_cents=self.scalper_config.stop_loss_cents,
            tags=tags,
            notes=f"{exit_type.upper()}: {position.asset} {position.side} @ {exit_price:.3f}",
        )

        self.trade_journal.append(trade_outcome)

        # Write to journal file immediately
        self._write_trade_to_journal(trade_outcome)

        # Capture to RAG learning system
        if self._rag_initialized and self.learning_capture:
            try:
                # Determine entry zone for metadata
                entry_zone = "low_zone" if position.entry_price <= self.scalper_config.low_zone_max else "high_zone"

                await self.learning_capture.capture_trade_outcome(
                    strategy="hybrid_scalper_v4",
                    token_id=position.token_id,
                    action=f"BUY_{position.side.upper()}",
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    size=position.shares * position.entry_price,  # cost in USD
                    profit=profit_usd,
                    exit_reason=exit_type,
                    metadata={
                        "asset": position.asset,
                        "side": position.side,
                        "entry_zone": entry_zone,
                        "time_to_expiry_at_entry": time_to_expiry_at_entry,
                        "hold_duration_sec": hold_duration,
                        "tags": ",".join(tags),  # Store as comma-separated for ChromaDB
                    },
                )
            except Exception as e:
                logger.debug(f"Failed to capture trade to RAG: {e}")

        # Log detailed outcome
        logger.info(
            f"📝 TRADE LOGGED [{trade_id}] | {position.asset} {position.side} | "
            f"{exit_type.upper()} | P&L: ${profit_usd:.2f} ({profit_pct*100:.1f}%) | "
            f"Tags: {', '.join(tags[:3])}"
        )

    def _write_trade_to_journal(self, trade: TradeOutcome):
        """Write trade to JSON journal file"""
        journal_file = JOURNAL_DIR / f"trades_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"

        try:
            with open(journal_file, "a") as f:
                f.write(json.dumps(asdict(trade)) + "\n")
        except Exception as e:
            logger.error(f"Failed to write trade journal: {e}")

    async def _log_window_outcome(self, slug: str, market: MarketPair, resolution: str):
        """Log market window outcome for RAG learning"""
        window_data = {
            "window_id": slug,
            "timestamp": datetime.utcnow().isoformat(),
            "asset": market.asset,
            "expiry_timestamp": market.expiry_timestamp,
            "resolution": resolution,  # "Up" or "Down"
            "final_up_price": market.up_price,
            "final_down_price": market.down_price,
            "positions_held": [],
            "total_pnl": 0.0,
        }

        # Find positions in this market
        for token_id, position in list(self.positions.items()):
            if position.market_slug == slug:
                # Calculate P&L based on resolution
                if position.side == resolution:
                    # Won - shares are worth $1 each
                    pnl = (1.0 - position.entry_price) * position.shares
                    outcome = "WIN"
                else:
                    # Lost - shares are worth $0
                    pnl = -position.entry_price * position.shares
                    outcome = "LOSS"

                window_data["positions_held"].append({
                    "side": position.side,
                    "entry_price": position.entry_price,
                    "shares": position.shares,
                    "pnl": pnl,
                    "outcome": outcome,
                })
                window_data["total_pnl"] += pnl

                # Log the trade outcome
                await self._log_trade_outcome(
                    position=position,
                    exit_type=f"expiry_{outcome.lower()}",
                    exit_price=1.0 if position.side == resolution else 0.0,
                    profit_usd=pnl,
                    profit_pct=pnl / (position.entry_price * position.shares) if position.entry_price > 0 else 0,
                    market=market,
                )

                # Remove position
                del self.positions[token_id]
                self.total_profit += pnl

        self.window_outcomes[slug] = window_data

        # Write window outcome to file
        self._write_window_outcome(window_data)

        logger.info(
            f"🏁 WINDOW RESOLVED [{slug}] | Resolution: {resolution} | "
            f"Positions: {len(window_data['positions_held'])} | "
            f"P&L: ${window_data['total_pnl']:.2f}"
        )

    def _write_window_outcome(self, window_data: Dict):
        """Write window outcome to JSON file"""
        window_file = JOURNAL_DIR / f"windows_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"

        try:
            with open(window_file, "a") as f:
                f.write(json.dumps(window_data) + "\n")
        except Exception as e:
            logger.error(f"Failed to write window outcome: {e}")

    async def _check_expired_windows(self):
        """Check for expired windows and log their outcomes"""
        now = time.time()

        for slug, market in list(self.active_markets.items()):
            # Window has expired (past expiry time)
            if market.time_to_expiry < -60 and slug not in self.expired_windows:
                # Fetch resolution from API
                resolution = await self._fetch_resolution(slug)
                if resolution:
                    await self._log_window_outcome(slug, market, resolution)
                    self.expired_windows.append(slug)

                    # Clean up old market
                    del self.active_markets[slug]

    async def _fetch_resolution(self, slug: str) -> Optional[str]:
        """Fetch market resolution from API"""
        url = f"{GAMMA_API}/events"
        params = {"slug": slug}

        try:
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json()
                if not data:
                    return None

                event = data[0]
                markets = event.get("markets", [])
                if not markets:
                    return None

                market = markets[0]

                # Check if resolved
                if market.get("closed"):
                    # Parse outcome prices - winning side = 1.0
                    prices_raw = market.get("outcomePrices", [])
                    if isinstance(prices_raw, str):
                        prices = json.loads(prices_raw)
                    else:
                        prices = prices_raw

                    outcomes_raw = market.get("outcomes", [])
                    if isinstance(outcomes_raw, str):
                        outcomes = json.loads(outcomes_raw)
                    else:
                        outcomes = outcomes_raw

                    for i, price in enumerate(prices):
                        if float(price) >= 0.99:  # Winner
                            return outcomes[i]

                return None

        except Exception as e:
            logger.debug(f"Error fetching resolution for {slug}: {e}")
            return None

    def generate_rag_summary(self) -> Dict:
        """Generate summary for RAG knowledge base"""
        if not self.trade_journal:
            return {}

        wins = [t for t in self.trade_journal if t.outcome == "win"]
        losses = [t for t in self.trade_journal if t.outcome == "loss"]

        summary = {
            "session_id": f"session_{int(self.start_time.timestamp()) if self.start_time else 0}",
            "timestamp": datetime.utcnow().isoformat(),
            "strategy": "hybrid_scalper_v3",
            "total_trades": len(self.trade_journal),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(self.trade_journal) if self.trade_journal else 0,
            "total_pnl": sum(t.profit_usd for t in self.trade_journal),
            "avg_win": sum(t.profit_usd for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t.profit_usd for t in losses) / len(losses) if losses else 0,
            "scalps": len([t for t in self.trade_journal if t.exit_type == "scalp"]),
            "stop_losses": len([t for t in self.trade_journal if t.exit_type == "stop_loss"]),
            "timeouts": len([t for t in self.trade_journal if t.exit_type == "timeout"]),
            "expiry_wins": len([t for t in self.trade_journal if t.exit_type == "expiry_win"]),
            "expiry_losses": len([t for t in self.trade_journal if t.exit_type == "expiry_loss"]),
            "assets_traded": list(set(t.asset for t in self.trade_journal)),
            "common_tags": self._get_common_tags(),
            "learnings": self._extract_learnings(),
        }

        # Write summary to file
        summary_file = JOURNAL_DIR / f"summary_{int(self.start_time.timestamp()) if self.start_time else 0}.json"
        try:
            with open(summary_file, "w") as f:
                json.dump(summary, f, indent=2)
            logger.info(f"📊 RAG summary written to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to write RAG summary: {e}")

        return summary

    def _get_common_tags(self) -> List[str]:
        """Get most common tags from trades"""
        tag_counts: Dict[str, int] = {}
        for trade in self.trade_journal:
            for tag in trade.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        return [tag for tag, count in sorted_tags[:10]]

    def _extract_learnings(self) -> List[str]:
        """Extract actionable learnings from trade outcomes"""
        learnings = []

        if not self.trade_journal:
            return learnings

        # Analyze scalp performance
        scalps = [t for t in self.trade_journal if t.exit_type == "scalp"]
        if scalps:
            avg_scalp_pct = sum(t.profit_pct for t in scalps) / len(scalps)
            learnings.append(
                f"Scalp trades averaged {avg_scalp_pct*100:.1f}% profit over {len(scalps)} trades"
            )

        # Analyze asset performance
        asset_pnl: Dict[str, float] = {}
        for t in self.trade_journal:
            asset_pnl[t.asset] = asset_pnl.get(t.asset, 0) + t.profit_usd

        best_asset = max(asset_pnl.items(), key=lambda x: x[1]) if asset_pnl else None
        worst_asset = min(asset_pnl.items(), key=lambda x: x[1]) if asset_pnl else None

        if best_asset:
            learnings.append(f"Best performing asset: {best_asset[0]} (${best_asset[1]:.2f})")
        if worst_asset and worst_asset[1] < 0:
            learnings.append(f"Worst performing asset: {worst_asset[0]} (${worst_asset[1]:.2f})")

        # Analyze lottery tickets
        lottery = [t for t in self.trade_journal if "cheap_lottery_ticket" in t.tags]
        if lottery:
            lottery_wins = [t for t in lottery if t.outcome == "win"]
            learnings.append(
                f"Lottery tickets: {len(lottery_wins)}/{len(lottery)} won "
                f"(${sum(t.profit_usd for t in lottery):.2f} total)"
            )

        # Analyze hold duration
        quick_exits = [t for t in self.trade_journal if t.hold_duration_sec < 60]
        if quick_exits:
            quick_win_rate = len([t for t in quick_exits if t.outcome == "win"]) / len(quick_exits)
            learnings.append(
                f"Quick exits (<1min): {quick_win_rate*100:.0f}% win rate over {len(quick_exits)} trades"
            )

        return learnings
