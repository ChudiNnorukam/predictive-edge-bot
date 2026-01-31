"""
Position Storage Layer
======================

Async SQLite database + Redis caching for position and trade tracking.
Provides persistent state across bot restarts.

Rebuilt with Opus 4.5 audit fixes:
- CRITICAL-2: Fully async with aiosqlite
- CRITICAL-5: Transaction isolation for consistency
- HIGH-5: Division by zero protection in stats
- MED-4: Cache invalidation before write
"""

import aiosqlite
import json
import time
import logging
import os
from typing import Optional, Dict, List, Any
from datetime import datetime
from contextlib import asynccontextmanager

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    try:
        import redis
        REDIS_AVAILABLE = True
        aioredis = None  # Will use sync redis with run_in_executor
    except ImportError:
        REDIS_AVAILABLE = False
        logging.warning("Redis not available - running without cache")

logger = logging.getLogger(__name__)


class PositionStore:
    """
    Persistent storage for positions and trades.

    Fully async implementation using aiosqlite.
    Supports Redis caching for hot data.
    """

    def __init__(self, db_path: str = "data/positions.db", redis_url: Optional[str] = None):
        """
        Initialize position store.

        Args:
            db_path: Path to SQLite database file
            redis_url: Optional Redis connection URL (e.g., redis://localhost:6379)
        """
        self.db_path = db_path
        self.redis_url = redis_url
        self.redis_client = None
        self._initialized = False

        # Ensure data directory exists
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    async def initialize(self):
        """
        Initialize database and connections.

        Must be called before using the store.
        """
        if self._initialized:
            return

        # Initialize SQLite schema
        await self._init_database()

        # Initialize Redis if available
        if REDIS_AVAILABLE and self.redis_url:
            try:
                if aioredis:
                    self.redis_client = aioredis.from_url(
                        self.redis_url,
                        decode_responses=True
                    )
                    await self.redis_client.ping()
                else:
                    # Fallback to sync redis
                    self.redis_client = redis.from_url(
                        self.redis_url,
                        decode_responses=True
                    )
                    self.redis_client.ping()
                logger.info("Redis cache connected")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e} - continuing without cache")
                self.redis_client = None

        self._initialized = True

    async def _init_database(self):
        """Initialize SQLite database schema"""
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrency
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            # Positions table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    token_id TEXT PRIMARY KEY,
                    entry_price REAL NOT NULL,
                    entry_time INTEGER NOT NULL,
                    size REAL NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
                    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'error')),
                    strategy TEXT,
                    metadata TEXT,
                    updated_at INTEGER NOT NULL,
                    take_profit_price REAL,
                    stop_loss_price REAL,
                    max_hold_seconds INTEGER DEFAULT 3600,
                    exit_reason TEXT
                )
            """)

            # Migration: Add exit columns to existing table (if upgrading)
            try:
                await db.execute("ALTER TABLE positions ADD COLUMN take_profit_price REAL")
            except Exception:
                pass  # Column already exists
            try:
                await db.execute("ALTER TABLE positions ADD COLUMN stop_loss_price REAL")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE positions ADD COLUMN max_hold_seconds INTEGER DEFAULT 3600")
            except Exception:
                pass
            try:
                await db.execute("ALTER TABLE positions ADD COLUMN exit_reason TEXT")
            except Exception:
                pass

            # Trades table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('YES', 'NO')),
                    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
                    price REAL NOT NULL CHECK (price >= 0 AND price <= 1),
                    size REAL NOT NULL CHECK (size > 0),
                    fee REAL DEFAULT 0 CHECK (fee >= 0),
                    status TEXT NOT NULL,
                    strategy TEXT,
                    profit REAL DEFAULT 0,
                    metadata TEXT
                )
            """)

            # Indexes for performance
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")

            await db.commit()

        logger.info(f"Database initialized at {self.db_path}")

    @asynccontextmanager
    async def _get_connection(self):
        """Get async database connection as context manager"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def record_trade(
        self,
        token_id: str,
        side: str,
        action: str,
        price: float,
        size: float,
        strategy: str,
        status: str = "executed",
        fee: float = 0.0,
        profit: float = 0.0,
        metadata: Optional[Dict] = None,
    ) -> int:
        """
        Record a trade execution with transaction isolation.

        All database operations are in a single transaction to ensure
        consistency between trades and positions tables.

        Args:
            token_id: Token ID
            side: YES or NO
            action: BUY or SELL
            price: Execution price (0-1)
            size: Trade size in USDC
            strategy: Strategy name that executed trade
            status: executed, failed, cancelled, dry_run
            fee: Trading fees paid
            profit: Realized profit (for closing trades)
            metadata: Additional trade metadata

        Returns:
            Trade ID
        """
        if not self._initialized:
            await self.initialize()

        timestamp = int(time.time())
        metadata_json = json.dumps(metadata) if metadata else None

        # Invalidate cache BEFORE write (prevents stale reads)
        if self.redis_client:
            try:
                if aioredis:
                    await self.redis_client.delete(f"position:{token_id}")
                else:
                    self.redis_client.delete(f"position:{token_id}")
            except Exception as e:
                logger.warning(f"Cache invalidation failed: {e}")

        async with aiosqlite.connect(self.db_path) as db:
            try:
                # Begin transaction
                await db.execute("BEGIN IMMEDIATE")

                # Insert trade
                cursor = await db.execute(
                    """
                    INSERT INTO trades (
                        token_id, timestamp, side, action, price, size,
                        fee, status, strategy, profit, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token_id, timestamp, side, action, price, size,
                     fee, status, strategy, profit, metadata_json),
                )
                trade_id = cursor.lastrowid

                # Update position if this is an executed entry
                if action == "BUY" and status in ("executed", "dry_run"):
                    # Check if position exists
                    cursor = await db.execute(
                        "SELECT token_id FROM positions WHERE token_id = ?",
                        (token_id,)
                    )
                    existing = await cursor.fetchone()

                    if existing:
                        # Update existing position (add to size)
                        await db.execute(
                            """
                            UPDATE positions
                            SET size = size + ?, updated_at = ?
                            WHERE token_id = ?
                            """,
                            (size, timestamp, token_id)
                        )
                    else:
                        # Insert new position
                        await db.execute(
                            """
                            INSERT INTO positions (
                                token_id, entry_price, entry_time, size, side,
                                status, strategy, metadata, updated_at
                            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)
                            """,
                            (token_id, price, timestamp, size, side,
                             strategy, metadata_json, timestamp)
                        )

                # Close/reduce position if this is an executed exit
                if action == "SELL" and status in ("executed", "dry_run"):
                    # Reduce position size
                    await db.execute(
                        """
                        UPDATE positions
                        SET size = size - ?, updated_at = ?
                        WHERE token_id = ?
                        """,
                        (size, timestamp, token_id)
                    )
                    # Mark as closed if size <= 0
                    await db.execute(
                        """
                        UPDATE positions
                        SET status = 'closed'
                        WHERE token_id = ? AND size <= 0
                        """,
                        (token_id,)
                    )

                # Commit transaction
                await db.commit()

                logger.info(
                    f"Trade recorded: {strategy} | {action} {side} | "
                    f"${size:.2f} @ ${price:.3f}"
                )
                return trade_id

            except Exception as e:
                # Rollback on any error
                await db.rollback()
                logger.error(f"Failed to record trade: {e}")
                raise

    async def update_position(
        self,
        token_id: str,
        entry_price: Optional[float] = None,
        size: Optional[float] = None,
        side: Optional[str] = None,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        """
        Update or create a position.

        Args:
            token_id: Token ID
            entry_price: Entry price (required for new positions)
            size: Position size
            side: YES or NO
            strategy: Strategy name
            status: open, closed, error
            metadata: Additional position data
        """
        if not self._initialized:
            await self.initialize()

        now = int(time.time())

        # Invalidate cache BEFORE write
        if self.redis_client:
            try:
                if aioredis:
                    await self.redis_client.delete(f"position:{token_id}")
                else:
                    self.redis_client.delete(f"position:{token_id}")
            except Exception as e:
                logger.warning(f"Cache invalidation failed: {e}")

        # Check if position exists
        existing = await self.get_position(token_id)

        async with aiosqlite.connect(self.db_path) as db:
            if existing:
                # Build dynamic update query
                updates = []
                values = []

                if entry_price is not None:
                    updates.append("entry_price = ?")
                    values.append(entry_price)
                if size is not None:
                    updates.append("size = ?")
                    values.append(size)
                if side is not None:
                    updates.append("side = ?")
                    values.append(side)
                if strategy is not None:
                    updates.append("strategy = ?")
                    values.append(strategy)
                if status is not None:
                    updates.append("status = ?")
                    values.append(status)
                if metadata is not None:
                    updates.append("metadata = ?")
                    values.append(json.dumps(metadata))

                updates.append("updated_at = ?")
                values.append(now)
                values.append(token_id)

                query = f"UPDATE positions SET {', '.join(updates)} WHERE token_id = ?"
                await db.execute(query, values)
                await db.commit()

            else:
                # Insert new position
                if entry_price is None or size is None or side is None:
                    raise ValueError("entry_price, size, and side required for new position")

                metadata_json = json.dumps(metadata) if metadata else None

                await db.execute(
                    """
                    INSERT INTO positions (
                        token_id, entry_price, entry_time, size, side,
                        status, strategy, metadata, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token_id, entry_price, now, size, side,
                     status or "open", strategy, metadata_json, now),
                )
                await db.commit()

    async def get_position(self, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Get position by token ID.

        Args:
            token_id: Token ID

        Returns:
            Position dictionary or None if not found
        """
        if not self._initialized:
            await self.initialize()

        # Try cache first
        if self.redis_client:
            try:
                if aioredis:
                    cached = await self.redis_client.get(f"position:{token_id}")
                else:
                    cached = self.redis_client.get(f"position:{token_id}")
                if cached:
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Cache read failed: {e}")

        # Query database
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM positions WHERE token_id = ?",
                (token_id,),
            )
            row = await cursor.fetchone()

            if row:
                position = dict(row)
                # Parse metadata JSON
                if position.get("metadata"):
                    try:
                        position["metadata"] = json.loads(position["metadata"])
                    except json.JSONDecodeError:
                        position["metadata"] = {}

                # Cache to Redis with 60s TTL
                if self.redis_client:
                    try:
                        if aioredis:
                            await self.redis_client.setex(
                                f"position:{token_id}",
                                60,
                                json.dumps(position),
                            )
                        else:
                            self.redis_client.setex(
                                f"position:{token_id}",
                                60,
                                json.dumps(position),
                            )
                    except Exception as e:
                        logger.warning(f"Cache write failed: {e}")

                return position

        return None

    async def get_open_positions(self, strategy: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open positions.

        Args:
            strategy: Optional strategy name filter

        Returns:
            List of open positions
        """
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if strategy:
                cursor = await db.execute(
                    "SELECT * FROM positions WHERE status = 'open' AND strategy = ?",
                    (strategy,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM positions WHERE status = 'open'"
                )

            rows = await cursor.fetchall()

            positions = []
            for row in rows:
                position = dict(row)
                if position.get("metadata"):
                    try:
                        position["metadata"] = json.loads(position["metadata"])
                    except json.JSONDecodeError:
                        position["metadata"] = {}
                positions.append(position)

            return positions

    async def get_trades(
        self,
        strategy: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get trade history.

        Args:
            strategy: Optional strategy name filter
            limit: Max number of trades to return
            offset: Pagination offset

        Returns:
            List of trades
        """
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if strategy:
                cursor = await db.execute(
                    """
                    SELECT * FROM trades
                    WHERE strategy = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (strategy, limit, offset),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT * FROM trades
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )

            rows = await cursor.fetchall()

            trades = []
            for row in rows:
                trade = dict(row)
                if trade.get("metadata"):
                    try:
                        trade["metadata"] = json.loads(trade["metadata"])
                    except json.JSONDecodeError:
                        trade["metadata"] = {}
                trades.append(trade)

            return trades

    async def get_stats(self, strategy: Optional[str] = None) -> Dict[str, Any]:
        """
        Get trading statistics.

        Args:
            strategy: Optional strategy name filter

        Returns:
            Dictionary with statistics
        """
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Fixed: Added size > 0 check to prevent division by zero
            if strategy:
                cursor = await db.execute(
                    """
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN 1 ELSE 0 END) as executed_trades,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN profit ELSE 0 END) as total_profit,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN size ELSE 0 END) as total_volume,
                        AVG(CASE
                            WHEN status IN ('executed', 'dry_run') AND size > 0
                            THEN profit/size
                            ELSE NULL
                        END) as avg_edge
                    FROM trades
                    WHERE strategy = ?
                    """,
                    (strategy,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN 1 ELSE 0 END) as executed_trades,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN profit ELSE 0 END) as total_profit,
                        SUM(CASE WHEN status IN ('executed', 'dry_run') THEN size ELSE 0 END) as total_volume,
                        AVG(CASE
                            WHEN status IN ('executed', 'dry_run') AND size > 0
                            THEN profit/size
                            ELSE NULL
                        END) as avg_edge
                    FROM trades
                    """
                )

            row = await cursor.fetchone()
            return dict(row) if row else {}

    async def close(self):
        """Close database connections"""
        if self.redis_client and aioredis:
            await self.redis_client.close()
        self._initialized = False
