#!/usr/bin/env python3
"""
scanner_v2.py - Multi-Market Scanner for MarketStateMachine
============================================================

Discovers markets from Gamma API and feeds them into the MarketStateMachine.

Features:
- Query Gamma API for active markets with configurable filters
- Continuous market discovery (periodic scanning)
- Incremental updates (only track new markets)
- Support for crypto (neg_risk) and non-crypto markets
- Automatic removal of expired markets

Usage:
    scanner = MultiMarketScanner(ScannerConfig())
    await scanner.scan_and_track(state_machine)
"""

import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum

from config import GAMMA_API
from core.market_state import Market, MarketStateMachine, MarketState

logger = logging.getLogger(__name__)


class MarketCategory(Enum):
    """Market category filters"""
    CRYPTO = "crypto"
    SPORTS = "sports"
    POLITICS = "politics"
    ECONOMICS = "economics"
    ALL = "all"


@dataclass
class ScannerConfig:
    """Configuration for multi-market scanner"""

    # Time-based filtering
    min_time_to_expiry_seconds: int = 60
    max_time_to_expiry_hours: int = 24

    # Volume and liquidity
    min_volume_usd: float = 100.0

    # Scanning behavior
    scan_interval_seconds: int = 300

    # Categories to include (empty = all)
    categories: List[str] = field(default_factory=list)

    # API limits
    markets_per_request: int = 100
    max_markets_to_track: int = 100

    # Recovery
    max_api_failures: int = 3


class MultiMarketScanner:
    """
    Discovers and manages markets for the state machine.

    Queries Gamma API for active markets, filters them based on criteria,
    and feeds them into MarketStateMachine for tracking.
    """

    def __init__(self, config: ScannerConfig):
        """
        Initialize scanner.

        Args:
            config: Scanner configuration
        """
        self.config = config
        self.tracked_token_ids: set[str] = set()
        self.api_failure_count = 0
        self.last_scan_time: Optional[datetime] = None

        logger.info(
            f"MultiMarketScanner initialized | "
            f"scan_interval={config.scan_interval_seconds}s | "
            f"min_volume=${config.min_volume_usd:.2f} | "
            f"time_to_expiry={config.min_time_to_expiry_seconds}s-"
            f"{config.max_time_to_expiry_hours}h"
        )

    async def discover_markets(self) -> List[Dict[str, Any]]:
        """
        Query Gamma API for active markets matching criteria.

        Returns:
            List of market dictionaries from Gamma API
        """
        markets = []

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{GAMMA_API}/markets"
                params = {
                    "closed": "false",
                    "active": "true",
                    "_limit": self.config.markets_per_request,
                }

                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        markets = await response.json()
                        logger.info(f"Fetched {len(markets)} markets from Gamma API")
                        self.api_failure_count = 0  # Reset on success
                    else:
                        logger.error(
                            f"Gamma API returned {response.status}"
                        )
                        self.api_failure_count += 1

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            self.api_failure_count += 1

        return markets

    def _parse_end_date(self, market: Dict[str, Any]) -> Optional[datetime]:
        """
        Parse market end date from multiple possible field names.

        Handles ISO format and standard datetime strings.

        Args:
            market: Market dict from API

        Returns:
            Datetime object or None if parsing fails
        """
        end_date_str = market.get("endDate") or market.get("end_date_iso")
        if not end_date_str:
            return None

        try:
            if "T" in end_date_str:
                # ISO format with timezone
                return datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            else:
                # Standard format - treat as UTC
                return datetime.fromisoformat(end_date_str)
        except Exception as e:
            logger.debug(f"Failed to parse date '{end_date_str}': {e}")
            return None

    def _matches_category_filter(self, market: Dict[str, Any]) -> bool:
        """
        Check if market matches configured category filters.

        If no categories configured, all markets pass.

        Args:
            market: Market dict from API

        Returns:
            True if market matches or no filters configured
        """
        if not self.config.categories:
            return True

        question = (market.get("question", "") or "").lower()
        description = (market.get("description", "") or "").lower()
        full_text = f"{question} {description}"

        # Check for category keywords
        category_keywords = {
            "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth", "usd", "xrp", "sol"],
            "sports": ["nfl", "nba", "mlb", "nhl", "super bowl", "world cup"],
            "politics": ["election", "president", "congress", "senate", "vote"],
            "economics": ["inflation", "gdp", "unemployment", "fed", "interest rate"],
        }

        for category in self.config.categories:
            keywords = category_keywords.get(category.lower(), [])
            if any(keyword in full_text for keyword in keywords):
                return True

        return False

    def _extract_market_info(self, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract and validate required market information.

        Handles multiple field naming conventions from Gamma API.

        Args:
            market: Market dict from API

        Returns:
            Extracted info dict or None if missing required fields
        """
        token_id = market.get("token_id") or market.get("tokenId")
        condition_id = market.get("condition_id") or market.get("conditionId")
        question = market.get("question", "Unknown")

        # Parse end date
        end_date = self._parse_end_date(market)
        if not end_date:
            return None

        # Calculate time to expiry
        now = datetime.utcnow()
        time_to_expiry = (end_date - now).total_seconds()

        # Validate time window
        if not (self.config.min_time_to_expiry_seconds <= time_to_expiry <=
                self.config.max_time_to_expiry_hours * 3600):
            return None

        # Get volume data
        volume = market.get("volumeUsd") or market.get("volume_usd", 0)
        try:
            volume = float(volume)
        except (ValueError, TypeError):
            volume = 0.0

        if volume < self.config.min_volume_usd:
            return None

        return {
            "token_id": token_id,
            "condition_id": condition_id,
            "question": question,
            "end_time": end_date,
            "volume_usd": volume,
            "time_to_expiry_seconds": time_to_expiry,
            "is_neg_risk": market.get("negRisk", False) or market.get("neg_risk", False),
            "raw_market": market,  # Keep full market data for reference
        }

    def _filter_markets(
        self, markets: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Filter markets by configured criteria.

        - Time to expiry window
        - Volume threshold
        - Category filters
        - Not already tracked
        - Under max tracking limit

        Args:
            markets: Raw markets from Gamma API

        Returns:
            Filtered list of market info dicts
        """
        filtered = []

        for market in markets:
            # Skip if already tracked
            token_id = market.get("token_id") or market.get("tokenId")
            if token_id in self.tracked_token_ids:
                continue

            # Check category filter
            if not self._matches_category_filter(market):
                continue

            # Extract and validate
            info = self._extract_market_info(market)
            if info is None:
                continue

            filtered.append(info)

        # Sort by time to expiry (soonest first)
        filtered.sort(key=lambda x: x["time_to_expiry_seconds"])

        # Limit to max tracking size
        return filtered[: self.config.max_markets_to_track - len(self.tracked_token_ids)]

    async def scan_and_track(self, state_machine: MarketStateMachine) -> int:
        """
        Scan for new markets and add them to state machine.

        Main entry point. Discovers new markets and feeds them into
        the MarketStateMachine for state tracking and execution.

        Args:
            state_machine: MarketStateMachine instance to feed markets into

        Returns:
            Number of new markets added
        """
        self.last_scan_time = datetime.utcnow()

        # Check if we're in circuit breaker mode (too many API failures)
        if self.api_failure_count >= self.config.max_api_failures:
            logger.warning(
                f"API failure threshold exceeded ({self.api_failure_count}/"
                f"{self.config.max_api_failures}). Skipping scan."
            )
            return 0

        # Discover new markets
        raw_markets = await self.discover_markets()
        if not raw_markets:
            return 0

        # Filter and validate
        new_markets = self._filter_markets(raw_markets)
        if not new_markets:
            logger.debug("No new markets match filter criteria")
            return 0

        # Add to state machine
        added = 0
        for market_info in new_markets:
            try:
                market = Market(
                    token_id=market_info["token_id"],
                    condition_id=market_info["condition_id"],
                    question=market_info["question"],
                    end_time=market_info["end_time"],
                    is_neg_risk=market_info["is_neg_risk"],
                )

                await state_machine.add_market(market)
                self.tracked_token_ids.add(market_info["token_id"])
                added += 1

                logger.info(
                    f"Added market: {market_info['token_id']} | "
                    f"{market_info['question'][:50]}... | "
                    f"Volume: ${market_info['volume_usd']:.2f} | "
                    f"Time to expiry: {market_info['time_to_expiry_seconds']:.0f}s"
                )

            except ValueError as e:
                # Market already exists
                logger.debug(f"Market already tracked: {e}")
            except Exception as e:
                logger.error(
                    f"Failed to add market {market_info['token_id']}: {e}"
                )

        if added > 0:
            logger.info(f"Scan complete: {added} new markets added")

        return added

    async def remove_expired_markets(self, state_machine: MarketStateMachine) -> int:
        """
        Remove markets that have expired from tracking.

        Cleans up markets where end_time has passed.

        Args:
            state_machine: MarketStateMachine instance

        Returns:
            Number of markets removed
        """
        removed = 0
        now = datetime.utcnow()

        for token_id in list(self.tracked_token_ids):
            markets = await state_machine.get_markets_by_state(MarketState.DONE)
            for market in markets:
                if market.token_id == token_id and market.end_time < now:
                    await state_machine.remove_market(token_id)
                    self.tracked_token_ids.discard(token_id)
                    removed += 1
                    logger.info(f"Removed expired market: {token_id}")

        return removed

    async def continuous_scan(
        self, state_machine: MarketStateMachine, duration_seconds: Optional[int] = None
    ) -> None:
        """
        Continuously scan for new markets at configured interval.

        Runs indefinitely (or for specified duration) and periodically
        discovers new markets to feed into the state machine.

        Args:
            state_machine: MarketStateMachine instance
            duration_seconds: How long to scan for (None = indefinite)
        """
        start_time = datetime.utcnow()
        scan_count = 0

        logger.info(
            f"Starting continuous market scanning | "
            f"Interval: {self.config.scan_interval_seconds}s"
        )

        try:
            while True:
                # Check duration limit
                if duration_seconds:
                    elapsed = (datetime.utcnow() - start_time).total_seconds()
                    if elapsed >= duration_seconds:
                        logger.info(
                            f"Scanning duration limit reached ({duration_seconds}s)"
                        )
                        break

                # Perform scan
                added = await self.scan_and_track(state_machine)
                removed = await self.remove_expired_markets(state_machine)
                scan_count += 1

                logger.info(
                    f"Scan #{scan_count}: +{added} markets, -{removed} expired | "
                    f"Total tracked: {len(self.tracked_token_ids)}"
                )

                # Get state stats
                stats = await state_machine.get_stats()
                logger.debug(f"State machine stats: {stats}")

                # Wait for next scan interval
                await asyncio.sleep(self.config.scan_interval_seconds)

        except asyncio.CancelledError:
            logger.info("Continuous scanning cancelled")
        except Exception as e:
            logger.error(f"Continuous scanning error: {e}")
            raise

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get scanner statistics.

        Returns:
            Dictionary with scanner stats
        """
        return {
            "markets_tracked": len(self.tracked_token_ids),
            "api_failures": self.api_failure_count,
            "last_scan": self.last_scan_time,
            "scan_interval_seconds": self.config.scan_interval_seconds,
        }
