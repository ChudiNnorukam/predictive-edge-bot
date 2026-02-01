"""
Metrics and Observability Module
=================================

Collects, aggregates, and visualizes performance metrics for the Polymarket bot.

Key exports:
- MetricsCollector: Collects and aggregates trade metrics
- MetricsDashboard: Terminal-based live monitoring
- TradeMetrics: Individual trade metric record
- AggregatedMetrics: Aggregated time-period metrics
- MetricsConfig: Configuration dataclass
"""

from metrics.collector import (
    MetricsCollector,
    MetricsConfig,
    TradeMetrics,
    AggregatedMetrics,
)
from metrics.dashboard import (
    MetricsDashboard,
    DashboardView,
)

__all__ = [
    "MetricsCollector",
    "MetricsConfig",
    "TradeMetrics",
    "AggregatedMetrics",
    "MetricsDashboard",
    "DashboardView",
]
