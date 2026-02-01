"""
MetricsCollector - Trade Metrics Collection and Aggregation
============================================================

Collects, aggregates, and analyzes performance metrics for latency-arbitrage trades.

Key metrics tracked (from PRD Section 7):
- Fill Rate: filled_trades / attempted_trades (target: >50% at scale)
- Average Edge per Fill: avg(payout - entry_price) in cents
- Latency percentiles: p50/p95/p99 for tick→decision and order→ack
- P&L: Total and per-trade
- System health: Missed trades, kill switch triggers, circuit breaker trips
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path
from statistics import quantiles

logger = logging.getLogger(__name__)


@dataclass
class MetricsConfig:
    """Configuration for metrics collection and aggregation"""

    # Collection intervals
    aggregation_interval_seconds: int = 60  # Aggregate metrics every minute
    dashboard_refresh_seconds: int = 5  # Dashboard update frequency

    # Retention
    history_hours: int = 24  # Keep 24h of detailed metrics

    # Alerts
    fill_rate_warning_threshold: float = 0.5  # Warn if fill rate < 50%
    latency_warning_ms: float = 50.0  # Warn if p95 latency > 50ms

    # Performance targets (from PRD)
    target_p95_decision_latency_ms: float = 30.0  # Tick to decision
    target_p95_order_ack_latency_ms: float = 150.0  # Order to ack


@dataclass
class TradeMetrics:
    """Metrics for a single executed trade"""

    timestamp: datetime

    # Trade identification
    market_id: str

    # Outcome
    attempted: bool = True
    filled: bool = False
    fill_amount: float = 0.0

    # Timing (all in milliseconds)
    tick_to_decision_ms: float = 0.0  # Time from price update to decision
    decision_to_order_ms: float = 0.0  # Time from decision to order sent
    order_to_ack_ms: float = 0.0  # Time from order sent to ack
    total_latency_ms: float = 0.0  # End-to-end

    # P&L
    entry_price: float = 0.0
    expected_payout: float = 1.0
    edge_cents: float = 0.0  # Expected profit in cents
    actual_pnl: float = 0.0  # Realized P&L

    # Reason for outcome
    outcome_reason: str = ""  # "filled", "no_liquidity", "timeout", "killed", etc.


@dataclass
class AggregatedMetrics:
    """Aggregated metrics for a time period"""

    period_start: datetime
    period_end: datetime

    # Fill rate
    trades_attempted: int = 0
    trades_filled: int = 0
    fill_rate: float = 0.0  # filled / attempted

    # P&L
    total_pnl: float = 0.0
    avg_edge_per_fill_cents: float = 0.0
    win_rate: float = 0.0

    # Latency percentiles (milliseconds)
    p50_decision_latency_ms: float = 0.0
    p95_decision_latency_ms: float = 0.0
    p99_decision_latency_ms: float = 0.0
    max_decision_latency_ms: float = 0.0

    p50_order_ack_ms: float = 0.0
    p95_order_ack_ms: float = 0.0
    p99_order_ack_ms: float = 0.0
    max_order_ack_ms: float = 0.0

    # Volume
    markets_traded: int = 0
    total_volume_usd: float = 0.0

    # System health
    missed_trades: int = 0  # Identified opportunity but failed to execute
    kill_switch_triggers: int = 0
    circuit_breaker_trips: int = 0

    # Sample counts for transparency
    decision_latency_samples: int = 0
    order_ack_samples: int = 0


class MetricsCollector:
    """
    Collects, aggregates, and reports performance metrics.

    Key metrics from PRD:
    1. Fill Rate - percentage of trades that filled
    2. Average Edge per Fill - profit per filled trade in cents
    3. Latency percentiles - p50/p95/p99 for key decision points
    """

    def __init__(self, config: MetricsConfig, log_dir: str = "logs/metrics"):
        self.config = config
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Raw metrics storage
        self._raw_metrics: List[TradeMetrics] = []
        self._aggregated: List[AggregatedMetrics] = []
        self._lock = asyncio.Lock()

        # Running counters for real-time queries
        self._session_trades_attempted = 0
        self._session_trades_filled = 0
        self._session_pnl = 0.0
        self._session_missed_trades = 0
        self._session_kill_switches = 0
        self._session_circuit_breakers = 0
        self._session_start = datetime.now(timezone.utc)

        # Last aggregation time
        self._last_aggregation = datetime.now(timezone.utc)

    async def record_trade(self, metrics: TradeMetrics) -> None:
        """Record a single trade's metrics"""
        async with self._lock:
            self._raw_metrics.append(metrics)

            # Update running counters
            self._session_trades_attempted += 1
            if metrics.filled:
                self._session_trades_filled += 1
            if metrics.actual_pnl != 0:
                self._session_pnl += metrics.actual_pnl

            logger.debug(
                f"Recorded trade: {metrics.market_id} | Filled: {metrics.filled} | "
                f"Latency: {metrics.total_latency_ms:.1f}ms"
            )

    async def record_missed_trade(
        self,
        market_id: str,
        reason: str,
    ) -> None:
        """Record when opportunity identified but execution failed"""
        async with self._lock:
            self._session_missed_trades += 1
            logger.warning(f"Missed trade: {market_id} | Reason: {reason}")

    async def record_latency(
        self,
        market_id: str,
        tick_to_decision_ms: float,
        decision_to_order_ms: float = 0.0,
        order_to_ack_ms: float = 0.0,
    ) -> None:
        """
        Record latency measurements (can be called without full trade).

        Used for tracking latency on failed execution attempts.
        """
        async with self._lock:
            metric = TradeMetrics(
                timestamp=datetime.now(timezone.utc),
                market_id=market_id,
                attempted=False,
                tick_to_decision_ms=tick_to_decision_ms,
                decision_to_order_ms=decision_to_order_ms,
                order_to_ack_ms=order_to_ack_ms,
                total_latency_ms=tick_to_decision_ms
                + decision_to_order_ms
                + order_to_ack_ms,
            )
            self._raw_metrics.append(metric)

    async def record_kill_switch_trigger(self) -> None:
        """Record a kill switch trigger event"""
        async with self._lock:
            self._session_kill_switches += 1
            logger.warning("Kill switch triggered")

    async def record_circuit_breaker_trip(self) -> None:
        """Record a circuit breaker trip event"""
        async with self._lock:
            self._session_circuit_breakers += 1
            logger.warning("Circuit breaker tripped")

    async def aggregate(self) -> AggregatedMetrics:
        """
        Aggregate raw metrics since last aggregation.
        Calculates fill rate, P&L, latency percentiles.
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            period_start = self._last_aggregation
            period_end = now

            # Filter metrics from this period
            period_metrics = [m for m in self._raw_metrics if period_start <= m.timestamp <= period_end]

            # Calculate aggregates
            result = AggregatedMetrics(
                period_start=period_start,
                period_end=period_end,
            )

            if not period_metrics:
                self._last_aggregation = now
                self._aggregated.append(result)
                return result

            # Trade counts and fill rate
            attempted = [m for m in period_metrics if m.attempted]
            filled = [m for m in period_metrics if m.filled]

            result.trades_attempted = len(attempted)
            result.trades_filled = len(filled)
            result.fill_rate = (
                len(filled) / len(attempted) if len(attempted) > 0 else 0.0
            )

            # P&L
            result.total_pnl = sum(m.actual_pnl for m in filled)
            if len(filled) > 0:
                result.avg_edge_per_fill_cents = sum(m.edge_cents for m in filled) / len(
                    filled
                )
                result.win_rate = sum(1 for m in filled if m.actual_pnl > 0) / len(filled)

            # Latency percentiles - decision latency
            decision_latencies = [
                m.tick_to_decision_ms
                for m in period_metrics
                if m.tick_to_decision_ms > 0
            ]
            if decision_latencies:
                result.decision_latency_samples = len(decision_latencies)
                sorted_vals = sorted(decision_latencies)
                result.p50_decision_latency_ms = self._calculate_percentile(
                    decision_latencies, 50
                )
                result.p95_decision_latency_ms = self._calculate_percentile(
                    decision_latencies, 95
                )
                result.p99_decision_latency_ms = self._calculate_percentile(
                    decision_latencies, 99
                )
                result.max_decision_latency_ms = max(decision_latencies)

            # Latency percentiles - order ack
            order_ack_latencies = [
                m.order_to_ack_ms for m in period_metrics if m.order_to_ack_ms > 0
            ]
            if order_ack_latencies:
                result.order_ack_samples = len(order_ack_latencies)
                result.p50_order_ack_ms = self._calculate_percentile(
                    order_ack_latencies, 50
                )
                result.p95_order_ack_ms = self._calculate_percentile(
                    order_ack_latencies, 95
                )
                result.p99_order_ack_ms = self._calculate_percentile(
                    order_ack_latencies, 99
                )
                result.max_order_ack_ms = max(order_ack_latencies)

            # Volume and markets
            result.markets_traded = len(set(m.market_id for m in period_metrics))
            result.total_volume_usd = sum(m.fill_amount for m in filled)

            # System health
            result.missed_trades = self._session_missed_trades
            result.kill_switch_triggers = self._session_kill_switches
            result.circuit_breaker_trips = self._session_circuit_breakers

            self._last_aggregation = now
            self._aggregated.append(result)

            logger.info(
                f"Aggregation: {result.trades_attempted} attempted | "
                f"{result.trades_filled} filled ({result.fill_rate:.0%}) | "
                f"P&L: ${result.total_pnl:+.2f} | p95 lat: {result.p95_decision_latency_ms:.0f}ms"
            )

            return result

    def _calculate_percentile(
        self, values: List[float], percentile: float
    ) -> float:
        """Calculate Nth percentile of values using linear interpolation"""
        if not values:
            return 0.0

        sorted_vals = sorted(values)
        n = len(sorted_vals)

        if percentile == 50:
            # Median
            if n % 2 == 0:
                return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
            else:
                return float(sorted_vals[n // 2])

        # Linear interpolation for other percentiles
        idx = (percentile / 100.0) * (n - 1)
        lower_idx = int(idx)
        upper_idx = min(lower_idx + 1, n - 1)
        fraction = idx - lower_idx

        if lower_idx >= n:
            return float(sorted_vals[-1])
        if upper_idx >= n:
            return float(sorted_vals[-1])

        return sorted_vals[lower_idx] * (1 - fraction) + sorted_vals[upper_idx] * fraction

    async def get_current_fill_rate(self) -> float:
        """Real-time fill rate for current session"""
        async with self._lock:
            if self._session_trades_attempted == 0:
                return 0.0
            return self._session_trades_filled / self._session_trades_attempted

    async def get_current_pnl(self) -> float:
        """Real-time P&L for current session"""
        async with self._lock:
            return self._session_pnl

    async def get_current_trades(self) -> tuple[int, int]:
        """Get (attempted, filled) trade counts for current session"""
        async with self._lock:
            return (self._session_trades_attempted, self._session_trades_filled)

    async def get_latency_stats(self) -> Dict[str, Any]:
        """
        Returns latency statistics from recent trades:
        {
            "p50_decision_ms": 12.5,
            "p95_decision_ms": 28.0,
            "p99_decision_ms": 45.0,
            "max_decision_ms": 120.0,
            "p95_order_ack_ms": 120.0,
            "samples": 150,
        }
        """
        async with self._lock:
            if not self._raw_metrics:
                return {
                    "p50_decision_ms": 0.0,
                    "p95_decision_ms": 0.0,
                    "p99_decision_ms": 0.0,
                    "max_decision_ms": 0.0,
                    "p95_order_ack_ms": 0.0,
                    "samples": 0,
                }

            decision_latencies = [
                m.tick_to_decision_ms
                for m in self._raw_metrics
                if m.tick_to_decision_ms > 0
            ]
            order_ack_latencies = [
                m.order_to_ack_ms
                for m in self._raw_metrics
                if m.order_to_ack_ms > 0
            ]

            return {
                "p50_decision_ms": self._calculate_percentile(
                    decision_latencies, 50
                )
                if decision_latencies
                else 0.0,
                "p95_decision_ms": self._calculate_percentile(
                    decision_latencies, 95
                )
                if decision_latencies
                else 0.0,
                "p99_decision_ms": self._calculate_percentile(
                    decision_latencies, 99
                )
                if decision_latencies
                else 0.0,
                "max_decision_ms": max(decision_latencies)
                if decision_latencies
                else 0.0,
                "p95_order_ack_ms": self._calculate_percentile(
                    order_ack_latencies, 95
                )
                if order_ack_latencies
                else 0.0,
                "samples": len(decision_latencies),
            }

    async def get_historical(
        self, hours: int = 24
    ) -> List[AggregatedMetrics]:
        """Get aggregated metrics for past N hours"""
        async with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            return [m for m in self._aggregated if m.period_end >= cutoff]

    async def check_alerts(self) -> List[str]:
        """
        Check current metrics against thresholds.
        Returns list of alert messages.
        """
        alerts = []

        fill_rate = await self.get_current_fill_rate()
        if fill_rate < self.config.fill_rate_warning_threshold and fill_rate > 0:
            alerts.append(
                f"ALERT: Fill rate low ({fill_rate:.0%}) - below {self.config.fill_rate_warning_threshold:.0%} threshold"
            )

        latency_stats = await self.get_latency_stats()
        if (
            latency_stats["p95_decision_ms"] > self.config.latency_warning_ms
            and latency_stats["samples"] > 0
        ):
            alerts.append(
                f"ALERT: Decision latency high ({latency_stats['p95_decision_ms']:.0f}ms p95) - "
                f"above {self.config.latency_warning_ms:.0f}ms target"
            )

        if (
            latency_stats["p95_order_ack_ms"] > self.config.target_p95_order_ack_latency_ms
            and latency_stats["samples"] > 0
        ):
            alerts.append(
                f"ALERT: Order ack latency high ({latency_stats['p95_order_ack_ms']:.0f}ms p95) - "
                f"above {self.config.target_p95_order_ack_latency_ms:.0f}ms target"
            )

        return alerts

    async def prune_old_metrics(self) -> int:
        """Remove metrics older than history_hours. Returns count removed."""
        async with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.history_hours)
            before_count = len(self._raw_metrics)
            self._raw_metrics = [m for m in self._raw_metrics if m.timestamp >= cutoff]
            after_count = len(self._raw_metrics)
            removed = before_count - after_count

            if removed > 0:
                logger.info(f"Pruned {removed} old metrics")

            return removed

    def to_jsonl(self) -> str:
        """Export raw metrics as JSONL for RAG ingestion"""
        lines = []
        for metric in self._raw_metrics:
            # Convert to dict and serialize
            data = asdict(metric)
            # Convert datetime to ISO format
            data["timestamp"] = metric.timestamp.isoformat()
            lines.append(json.dumps(data))

        return "\n".join(lines)

    async def write_metrics_log(self) -> None:
        """Write current metrics to JSONL log file"""
        async with self._lock:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = self.log_dir / f"metrics_{timestamp}.jsonl"

            jsonl = self.to_jsonl()
            with open(log_file, "w") as f:
                f.write(jsonl)

            logger.debug(f"Wrote {len(self._raw_metrics)} metrics to {log_file}")

    async def get_session_stats(self) -> Dict[str, Any]:
        """Get comprehensive session statistics"""
        async with self._lock:
            elapsed = (datetime.now(timezone.utc) - self._session_start).total_seconds()
            attempted, filled = self._session_trades_attempted, self._session_trades_filled

            return {
                "session_start": self._session_start.isoformat(),
                "elapsed_seconds": elapsed,
                "trades_attempted": attempted,
                "trades_filled": filled,
                "fill_rate": filled / attempted if attempted > 0 else 0.0,
                "total_pnl": self._session_pnl,
                "missed_trades": self._session_missed_trades,
                "kill_switches": self._session_kill_switches,
                "circuit_breakers": self._session_circuit_breakers,
            }
