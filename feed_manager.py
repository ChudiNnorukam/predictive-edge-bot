"""
WebSocket Feed Manager for Polymarket Latency-Arbitrage Bot
============================================================

Manages multi-market WebSocket subscriptions to Polymarket CLOB.
Receives price updates and passes them to MarketStateMachine.

Handles:
- Multiple concurrent WebSocket subscriptions
- Connection recovery with exponential backoff
- Health monitoring (stale feed detection)
- Automatic reconnection on disconnect
- Price update parsing and forwarding
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Callable, Any
from datetime import datetime

import websockets
from websockets.client import WebSocketClientProtocol

from config import CLOB_WS

logger = logging.getLogger(__name__)


@dataclass
class FeedConfig:
    """Configuration for the feed manager"""

    ws_url: str = f"{CLOB_WS}market"
    reconnect_delay_seconds: float = 1.0
    max_reconnect_attempts: int = 10
    stale_threshold_ms: int = 500
    heartbeat_interval_seconds: float = 30.0


class FeedManager:
    """
    WebSocket feed manager for multi-market price subscriptions.

    Subscribes to Polymarket CLOB WebSocket and forwards price updates
    to the MarketStateMachine. Handles reconnection and stale feed detection.
    """

    def __init__(self, state_machine, config: Optional[FeedConfig] = None):
        """
        Initialize the feed manager.

        Args:
            state_machine: MarketStateMachine instance to update on price changes
            config: FeedConfig with connection parameters
        """
        self.state_machine = state_machine
        self.config = config or FeedConfig()

        # Subscription management
        self.subscribed_markets: Dict[str, float] = {}  # token_id -> last_update_time
        self._lock = asyncio.Lock()

        # Connection state
        self.ws: Optional[WebSocketClientProtocol] = None
        self.running = False
        self.connected = False

        # Reconnection tracking
        self.reconnect_attempts = 0
        self.last_connection_error: Optional[str] = None

        # Health monitoring
        self.last_data_received = time.time()
        self.data_timeout_seconds = 60  # Reconnect if no data for 60s

        # Callbacks
        self.stale_feed_callback: Optional[Callable[[str], None]] = None

        logger.info(
            f"FeedManager initialized | URL: {self.config.ws_url} | "
            f"Stale threshold: {self.config.stale_threshold_ms}ms"
        )

    async def subscribe(self, token_id: str) -> bool:
        """
        Subscribe to price updates for a market.

        Args:
            token_id: Market token ID to subscribe to

        Returns:
            True if subscription succeeded, False otherwise
        """
        async with self._lock:
            if token_id in self.subscribed_markets:
                logger.debug(f"Already subscribed to {token_id}")
                return True

            self.subscribed_markets[token_id] = time.time()

        # Send subscription message if connected
        if self.connected and self.ws:
            try:
                msg = {
                    "type": "subscribe",
                    "channel": "market",
                    "assets_ids": [token_id],
                }
                await self.ws.send(json.dumps(msg))
                logger.info(f"Subscribed to market: {token_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to subscribe to {token_id}: {e}")
                async with self._lock:
                    del self.subscribed_markets[token_id]
                return False
        else:
            logger.debug(f"Queued subscription for {token_id} (not yet connected)")
            return True

    async def unsubscribe(self, token_id: str) -> bool:
        """
        Unsubscribe from price updates for a market.

        Args:
            token_id: Market token ID to unsubscribe from

        Returns:
            True if unsubscription succeeded, False otherwise
        """
        async with self._lock:
            if token_id not in self.subscribed_markets:
                logger.debug(f"Not subscribed to {token_id}")
                return True

            del self.subscribed_markets[token_id]

        # Send unsubscribe message if connected
        if self.connected and self.ws:
            try:
                msg = {
                    "type": "unsubscribe",
                    "channel": "market",
                    "assets_ids": [token_id],
                }
                await self.ws.send(json.dumps(msg))
                logger.info(f"Unsubscribed from market: {token_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to unsubscribe from {token_id}: {e}")
                return False

        return True

    async def get_subscribed_markets(self) -> List[str]:
        """
        Get list of currently subscribed markets.

        Returns:
            List of subscribed token IDs
        """
        async with self._lock:
            return list(self.subscribed_markets.keys())

    async def on_price_update(self, token_id: str, bid: float, ask: float) -> None:
        """
        Handle price update from WebSocket.

        Updates MarketStateMachine and tracks feed freshness.

        Args:
            token_id: Market token ID
            bid: Current bid price (0-1)
            ask: Current ask price (0-1)
        """
        await self.state_machine.update_price(token_id, bid=bid, ask=ask)

        # Update feed timestamp
        async with self._lock:
            if token_id in self.subscribed_markets:
                self.subscribed_markets[token_id] = time.time()

        self.last_data_received = time.time()

    def _parse_price_message(self, data: Dict[str, Any]) -> Optional[tuple]:
        """
        Parse price update from Polymarket WebSocket message.

        Handles different message types:
        - "book": Full order book snapshot
        - "price_change": Price change update
        - Other types: Ignored

        Args:
            data: Parsed JSON message from WebSocket

        Returns:
            Tuple of (token_id, bid, ask) or None if unable to parse
        """
        try:
            msg_type = data.get("type", "")
            asset_id = data.get("asset_id")

            if not asset_id:
                return None

            bid = None
            ask = None

            # Handle "book" messages (snapshot with bids/asks)
            if msg_type == "book":
                bids = data.get("bids", [])
                asks = data.get("asks", [])

                if bids:
                    bid = float(bids[0].get("price", 0))
                if asks:
                    ask = float(asks[0].get("price", 0))

            # Handle "price_change" messages
            elif msg_type == "price_change":
                bid = data.get("bid")
                ask = data.get("ask")

                if bid is not None:
                    bid = float(bid)
                if ask is not None:
                    ask = float(ask)

            # Only return if we got at least one price
            if bid is not None or ask is not None:
                # Use best bid/ask if one is missing
                if bid is None:
                    bid = ask
                if ask is None:
                    ask = bid

                return (asset_id, bid, ask)

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"Error parsing price message: {e}")

        return None

    async def _handle_message(self, message: str) -> None:
        """
        Handle incoming WebSocket message.

        Args:
            message: Raw JSON message from WebSocket
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON message: {e}")
            return

        # Handle heartbeat messages (no-op, just keep connection alive)
        if data.get("type") == "heartbeat":
            return

        # Parse price update
        result = self._parse_price_message(data)
        if result:
            token_id, bid, ask = result
            await self.on_price_update(token_id, bid, ask)

    async def _subscribe_all(self) -> None:
        """Subscribe to all queued markets after connection established"""
        async with self._lock:
            markets = list(self.subscribed_markets.keys())

        if not markets:
            return

        try:
            msg = {
                "type": "subscribe",
                "channel": "market",
                "assets_ids": markets,
            }
            await self.ws.send(json.dumps(msg))
            logger.info(f"Subscribed to {len(markets)} markets on connect")
        except Exception as e:
            logger.error(f"Failed to subscribe on connect: {e}")

    async def _heartbeat_loop(self) -> None:
        """
        Periodically send heartbeats and check feed health.

        Detects stale feeds and triggers reconnection if needed.
        """
        while self.running:
            try:
                # Send heartbeat ping
                if self.connected and self.ws:
                    try:
                        await self.ws.ping()
                    except Exception as e:
                        logger.debug(f"Heartbeat ping failed: {e}")

                # Check for data timeout
                time_since_data = time.time() - self.last_data_received
                if time_since_data > self.data_timeout_seconds:
                    logger.warning(
                        f"No data for {time_since_data:.0f}s - forcing reconnect"
                    )
                    self.connected = False
                    if self.ws:
                        try:
                            await self.ws.close()
                        except:
                            pass

                await asyncio.sleep(self.config.heartbeat_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(1)

    async def _check_stale_feeds(self) -> None:
        """
        Periodically check for stale feeds and trigger callbacks.

        A feed is stale if no update in stale_threshold_ms milliseconds.
        """
        stale_threshold_sec = self.config.stale_threshold_ms / 1000.0

        while self.running:
            try:
                current_time = time.time()
                stale_markets = []

                async with self._lock:
                    for token_id, last_update in self.subscribed_markets.items():
                        time_since_update = current_time - last_update
                        if time_since_update > stale_threshold_sec:
                            stale_markets.append(token_id)

                # Trigger callback for stale feeds (outside lock)
                if stale_markets and self.stale_feed_callback:
                    for token_id in stale_markets:
                        try:
                            await self.stale_feed_callback(token_id)
                        except Exception as e:
                            logger.error(f"Stale feed callback error: {e}")

                await asyncio.sleep(0.1)  # Check frequently for staleness

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stale feed check error: {e}")
                await asyncio.sleep(1)

    async def connect(self) -> bool:
        """
        Connect to WebSocket with reconnection logic.

        Uses exponential backoff for reconnection attempts.

        Returns:
            True if connection successful, False otherwise
        """
        while self.running and self.reconnect_attempts < self.config.max_reconnect_attempts:
            try:
                logger.info(
                    f"Connecting to WebSocket (attempt {self.reconnect_attempts + 1}/"
                    f"{self.config.max_reconnect_attempts}): {self.config.ws_url}"
                )

                self.ws = await websockets.connect(self.config.ws_url)
                self.connected = True
                self.reconnect_attempts = 0
                self.last_connection_error = None
                logger.info("WebSocket connected successfully")

                # Subscribe to all markets
                await self._subscribe_all()
                self.last_data_received = time.time()

                return True

            except Exception as e:
                self.reconnect_attempts += 1
                self.last_connection_error = str(e)
                backoff = min(
                    self.config.reconnect_delay_seconds * (2 ** (self.reconnect_attempts - 1)),
                    30.0,  # Cap backoff at 30 seconds
                )
                logger.warning(
                    f"Connection failed: {e} | "
                    f"Retry in {backoff:.1f}s (attempt {self.reconnect_attempts}/"
                    f"{self.config.max_reconnect_attempts})"
                )
                await asyncio.sleep(backoff)

        if self.running and self.reconnect_attempts >= self.config.max_reconnect_attempts:
            logger.error(
                f"Max reconnection attempts reached ({self.config.max_reconnect_attempts})"
            )

        return False

    async def run(self) -> None:
        """
        Main feed manager loop.

        Maintains WebSocket connection and processes incoming messages.
        """
        self.running = True
        heartbeat_task = None
        stale_check_task = None

        try:
            # Start background tasks
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            stale_check_task = asyncio.create_task(self._check_stale_feeds())

            while self.running:
                try:
                    # Attempt connection with backoff
                    connected = await self.connect()
                    if not connected:
                        break

                    # Message loop
                    try:
                        async for message in self.ws:
                            if not self.running:
                                break
                            await self._handle_message(message)

                    except websockets.exceptions.ConnectionClosed:
                        logger.warning("WebSocket connection closed")
                        self.connected = False
                    except Exception as e:
                        logger.error(f"Message loop error: {e}")
                        self.connected = False

                    # Reconnect if still running
                    if self.running:
                        await asyncio.sleep(1)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Feed manager error: {e}")
                    await asyncio.sleep(5)

        finally:
            self.running = False

            # Cleanup
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass

            if heartbeat_task:
                heartbeat_task.cancel()
            if stale_check_task:
                stale_check_task.cancel()

            logger.info("Feed manager stopped")

    def stop(self) -> None:
        """Stop the feed manager"""
        logger.info("Stopping feed manager...")
        self.running = False

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status of the feed manager.

        Returns:
            Dictionary with connection and subscription status
        """
        time_since_data = time.time() - self.last_data_received
        return {
            "connected": self.connected,
            "running": self.running,
            "subscribed_markets": len(self.subscribed_markets),
            "reconnect_attempts": self.reconnect_attempts,
            "time_since_last_data_seconds": time_since_data,
            "is_healthy": (
                self.connected
                and time_since_data < self.data_timeout_seconds
            ),
            "last_error": self.last_connection_error,
        }
