"""
Position Storage Layer
======================

SQLite database + Redis caching for position and trade tracking.
Provides persistent state across bot restarts.
"""

import sqlite3
import json
import time
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime
from contextlib import contextmanager

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logging.warning("Redis not available - running without cache")

logger = logging.getLogger(__name__)


class PositionStore:
    """Persistent storage for positions and trades"""

    def __init__(self, db_path: str = "data/positions.db", redis_url: Optional[str] = None):
        """
        Initialize position store

        Args:
            db_path: Path to SQLite database file
            redis_url: Optional Redis connection URL (e.g., redis://localhost:6379)
        """
        self.db_path = db_path
        self.redis_client = None

        # Initialize SQLite
        self._init_database()

        # Initialize Redis if available
        if REDIS_AVAILABLE and redis_url:
            try:
                self.redis_client = redis.from_url(redis_url, decode_responses=True)
                self.redis_client.ping()
                logger.info("Redis cache connected")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e} - continuing without cache")
                self.redis_client = None

    def _init_database(self):
        """Initialize SQLite database schema"""
        with self._get_connection() as conn:
            # Positions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    token_id TEXT PRIMARY KEY,
                    entry_price REAL NOT NULL,
                    entry_time INTEGER NOT NULL,
                    size REAL NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    strategy TEXT,
                    metadata TEXT,
                    updated_at INTEGER NOT NULL
                )
            """)

            # Trades table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    fee REAL DEFAULT 0,
                    status TEXT NOT NULL,
                    strategy TEXT,
                    profit REAL DEFAULT 0,
                    metadata TEXT
                )
            """)

            # Indexes for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy)")

            conn.commit()

        logger.info(f"Database initialized at {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """Get database connection as context manager"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

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
        Record a trade execution

        Args:
            token_id: Token ID
            side: YES or NO
            action: BUY or SELL
            price: Execution price
            size: Trade size in USDC
            strategy: Strategy name that executed trade
            status: executed, failed, cancelled
            fee: Trading fees paid
            profit: Realized profit (for closing trades)
            metadata: Additional trade metadata

        Returns:
            Trade ID
        """
        timestamp = int(time.time())
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (
                    token_id, timestamp, side, action, price, size,
                    fee, status, strategy, profit, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_id, timestamp, side, action, price, size, fee, status, strategy, profit, metadata_json),
            )
            conn.commit()
            trade_id = cursor.lastrowid

        # Update position if this is an entry
        if action == "BUY" and status == "executed":
            await self.update_position(
                token_id=token_id,
                entry_price=price,
                size=size,
                side=side,
                strategy=strategy,
                status="open",
            )

        # Close position if this is an exit
        if action == "SELL" and status == "executed":
            await self.update_position(token_id=token_id, status="closed")

        logger.info(f"Trade recorded: {strategy} | {action} {side} | ${size:.2f} @ ${price:.3f}")
        return trade_id

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
        Update or create a position

        Args:
            token_id: Token ID
            entry_price: Entry price (required for new positions)
            size: Position size
            side: YES or NO
            strategy: Strategy name
            status: open, closed, error
            metadata: Additional position data
        """
        now = int(time.time())

        # Check if position exists
        existing = await self.get_position(token_id)

        if existing:
            # Update existing position
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

            with self._get_connection() as conn:
                conn.execute(
                    f"UPDATE positions SET {', '.join(updates)} WHERE token_id = ?",
                    values,
                )
                conn.commit()

        else:
            # Insert new position
            if entry_price is None or size is None or side is None:
                raise ValueError("entry_price, size, and side required for new position")

            metadata_json = json.dumps(metadata) if metadata else None

            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO positions (
                        token_id, entry_price, entry_time, size, side,
                        status, strategy, metadata, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_id,
                        entry_price,
                        now,
                        size,
                        side,
                        status or "open",
                        strategy,
                        metadata_json,
                        now,
                    ),
                )
                conn.commit()

        # Invalidate cache
        if self.redis_client:
            self.redis_client.delete(f"position:{token_id}")

    async def get_position(self, token_id: str) -> Optional[Dict[str, Any]]:
        """
        Get position by token ID

        Args:
            token_id: Token ID

        Returns:
            Position dictionary or None if not found
        """
        # Try cache first
        if self.redis_client:
            cached = self.redis_client.get(f"position:{token_id}")
            if cached:
                return json.loads(cached)

        # Query database
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE token_id = ?",
                (token_id,),
            ).fetchone()

            if row:
                position = dict(row)
                # Parse metadata JSON
                if position.get("metadata"):
                    position["metadata"] = json.loads(position["metadata"])

                # Cache to Redis with 60s TTL
                if self.redis_client:
                    self.redis_client.setex(
                        f"position:{token_id}",
                        60,
                        json.dumps(position),
                    )

                return position

        return None

    async def get_open_positions(self, strategy: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open positions

        Args:
            strategy: Optional strategy name filter

        Returns:
            List of open positions
        """
        with self._get_connection() as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status = 'open' AND strategy = ?",
                    (strategy,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status = 'open'"
                ).fetchall()

            positions = []
            for row in rows:
                position = dict(row)
                if position.get("metadata"):
                    position["metadata"] = json.loads(position["metadata"])
                positions.append(position)

            return positions

    async def get_trades(
        self,
        strategy: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get trade history

        Args:
            strategy: Optional strategy name filter
            limit: Max number of trades to return
            offset: Pagination offset

        Returns:
            List of trades
        """
        with self._get_connection() as conn:
            if strategy:
                rows = conn.execute(
                    """
                    SELECT * FROM trades
                    WHERE strategy = ?
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (strategy, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM trades
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()

            trades = []
            for row in rows:
                trade = dict(row)
                if trade.get("metadata"):
                    trade["metadata"] = json.loads(trade["metadata"])
                trades.append(trade)

            return trades

    async def get_stats(self, strategy: Optional[str] = None) -> Dict[str, Any]:
        """
        Get trading statistics

        Args:
            strategy: Optional strategy name filter

        Returns:
            Dictionary with statistics
        """
        with self._get_connection() as conn:
            if strategy:
                stats = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END) as executed_trades,
                        SUM(CASE WHEN status = 'executed' THEN profit ELSE 0 END) as total_profit,
                        SUM(CASE WHEN status = 'executed' THEN size ELSE 0 END) as total_volume,
                        AVG(CASE WHEN status = 'executed' THEN profit/size ELSE 0 END) as avg_edge
                    FROM trades
                    WHERE strategy = ?
                    """,
                    (strategy,),
                ).fetchone()
            else:
                stats = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN status = 'executed' THEN 1 ELSE 0 END) as executed_trades,
                        SUM(CASE WHEN status = 'executed' THEN profit ELSE 0 END) as total_profit,
                        SUM(CASE WHEN status = 'executed' THEN size ELSE 0 END) as total_volume,
                        AVG(CASE WHEN status = 'executed' THEN profit/size ELSE 0 END) as avg_edge
                    FROM trades
                    """
                ).fetchone()

            return dict(stats) if stats else {}
