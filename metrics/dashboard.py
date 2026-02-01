"""
MetricsDashboard - Terminal-Based Live Monitoring
==================================================

Provides real-time ASCII-art dashboard for monitoring:
- Fill rates and P&L
- Latency metrics (p50, p95, p99)
- System health
- Active alerts

No external UI dependencies - pure terminal rendering.
"""

import asyncio
import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional
from metrics.collector import MetricsCollector, MetricsConfig, AggregatedMetrics

logger = logging.getLogger(__name__)


class DashboardView(Enum):
    """Available dashboard views"""

    SUMMARY = "summary"
    LATENCY = "latency"
    MARKETS = "markets"
    ALERTS = "alerts"


class MetricsDashboard:
    """
    Terminal-based dashboard for real-time monitoring.
    Displays key metrics in a formatted, updating view.
    """

    def __init__(
        self,
        collector: MetricsCollector,
        config: MetricsConfig,
    ):
        self.collector = collector
        self.config = config
        self._current_view = DashboardView.SUMMARY
        self._running = False
        self._refresh_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the dashboard refresh loop"""
        if self._running:
            logger.warning("Dashboard already running")
            return

        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Dashboard started")

    async def stop(self) -> None:
        """Stop the dashboard"""
        self._running = False
        if self._refresh_task:
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.info("Dashboard stopped")

    def set_view(self, view: DashboardView) -> None:
        """Change current view"""
        if isinstance(view, str):
            view = DashboardView(view)
        self._current_view = view
        logger.debug(f"Dashboard view changed to: {view.value}")

    async def _refresh_loop(self) -> None:
        """Background refresh loop"""
        while self._running:
            try:
                await self.render()
                await asyncio.sleep(self.config.dashboard_refresh_seconds)
            except Exception as e:
                logger.error(f"Dashboard render error: {e}", exc_info=True)

    async def render(self) -> str:
        """
        Render current view as formatted string.
        Clears screen and redraws.
        """
        try:
            if self._current_view == DashboardView.SUMMARY:
                output = await self._render_summary_view()
            elif self._current_view == DashboardView.LATENCY:
                output = await self._render_latency_view()
            elif self._current_view == DashboardView.MARKETS:
                output = await self._render_markets_view()
            elif self._current_view == DashboardView.ALERTS:
                output = await self._render_alerts_view()
            else:
                output = "Unknown view"

            # Print with screen clear
            print("\033[2J\033[H", end="")  # ANSI clear screen
            print(output)
            return output

        except Exception as e:
            logger.error(f"Error rendering dashboard: {e}")
            return f"Error: {e}"

    async def _render_summary_view(self) -> str:
        """Render main summary dashboard"""
        session_stats = await self.collector.get_session_stats()
        latency_stats = await self.collector.get_latency_stats()
        alerts = await self.collector.check_alerts()

        elapsed_hours = session_stats["elapsed_seconds"] / 3600
        elapsed_mins = (session_stats["elapsed_seconds"] % 3600) / 60

        fill_rate = session_stats["fill_rate"]
        pnl = session_stats["total_pnl"]

        # Format session time
        if elapsed_hours > 0:
            time_str = f"{int(elapsed_hours)}h {int(elapsed_mins)}m"
        else:
            time_str = f"{int(elapsed_mins)}m"

        # Build output
        lines = [
            "╔" + "═" * 54 + "╗",
            "║     POLYMARKET SNIPER - LIVE DASHBOARD            ║",
            "╠" + "═" * 54 + "╣",
            f"║ Session: {time_str:<10}  Trades: {session_stats['trades_attempted']:<20} ║",
            f"║ Fill Rate: {fill_rate:.0%}  P&L: ${pnl:+.2f}                  ║",
            "╠" + "═" * 54 + "╣",
            "║ KEY METRICS                                      ║",
            "║                                                  ║",
        ]

        # Fill rate bar
        bar = self._progress_bar(fill_rate, 1.0, width=30)
        lines.append(f"║ Fill Rate:  {bar} {fill_rate:>5.0%}         ║")

        # P&L indicator
        pnl_color = "+" if pnl >= 0 else ""
        lines.append(f"║ Session P&L: {pnl_color}${pnl:>6.2f}                   ║")

        # Average edge per fill
        if session_stats["trades_filled"] > 0:
            avg_edge_cents = (pnl * 100) / session_stats["trades_filled"]
            lines.append(f"║ Avg Edge:    {avg_edge_cents:>6.2f}¢ per trade          ║")
        else:
            lines.append(f"║ Avg Edge:    N/A                               ║")

        lines.append("║                                                  ║")
        lines.append("╠" + "═" * 54 + "╣")
        lines.append("║ LATENCY (milliseconds)                          ║")
        lines.append("║                                                  ║")

        # Latency metrics
        p95_decision = latency_stats["p95_decision_ms"]
        p95_ack = latency_stats["p95_order_ack_ms"]
        decision_target = self.config.target_p95_decision_latency_ms
        ack_target = self.config.target_p95_order_ack_latency_ms

        decision_ok = p95_decision <= decision_target
        ack_ok = p95_ack <= ack_target

        decision_mark = "✓" if decision_ok else "✗"
        ack_mark = "✓" if ack_ok else "✗"

        lines.append(
            f"║ Tick→Decision: {p95_decision:>5.0f}ms {decision_mark}  "
            f"Order→Ack: {p95_ack:>5.0f}ms {ack_mark}    ║"
        )
        lines.append(
            f"║ Targets:      {decision_target:>5.0f}ms       "
            f"           {ack_target:>5.0f}ms         ║"
        )

        lines.append("║                                                  ║")
        lines.append("╠" + "═" * 54 + "╣")
        lines.append("║ HEALTH                                          ║")
        lines.append("║                                                  ║")
        lines.append(
            f"║ Missed Trades: {session_stats['missed_trades']:<5} "
            f"Kill Switches: {session_stats['kill_switches']:<2}          ║"
        )
        lines.append(
            f"║ Circuit Breaks: {session_stats['circuit_breakers']:<5}                          ║"
        )

        lines.append("║                                                  ║")
        lines.append("╠" + "═" * 54 + "╣")
        lines.append("║ ALERTS                                          ║")
        lines.append("║                                                  ║")

        if alerts:
            for alert in alerts[:3]:  # Show first 3 alerts
                # Truncate to fit in box
                alert_text = alert[: 50]
                lines.append(f"║ {alert_text:<52} ║")
        else:
            lines.append("║ None                                               ║")

        lines.append("╚" + "═" * 54 + "╝")

        return "\n".join(lines)

    async def _render_latency_view(self) -> str:
        """Detailed latency breakdown view"""
        session_stats = await self.collector.get_session_stats()
        latency_stats = await self.collector.get_latency_stats()

        lines = [
            "╔" + "═" * 54 + "╗",
            "║        POLYMARKET SNIPER - LATENCY DETAIL         ║",
            "╠" + "═" * 54 + "╣",
            f"║ Samples: {latency_stats['samples']:<45} ║",
            "║                                                  ║",
            "║ DECISION LATENCY (Tick → Decision)              ║",
            "║                                                  ║",
            f"║ p50:  {latency_stats['p50_decision_ms']:>6.1f}ms                                   ║",
            f"║ p95:  {latency_stats['p95_decision_ms']:>6.1f}ms  (Target: {self.config.target_p95_decision_latency_ms:.0f}ms) ║",
            f"║ p99:  {latency_stats['p99_decision_ms']:>6.1f}ms                                   ║",
            f"║ Max:  {latency_stats['max_decision_ms']:>6.1f}ms                                   ║",
            "║                                                  ║",
            "║ ORDER ACK LATENCY (Order Sent → Ack)            ║",
            "║                                                  ║",
            f"║ p95:  {latency_stats['p95_order_ack_ms']:>6.1f}ms  (Target: {self.config.target_p95_order_ack_latency_ms:.0f}ms) ║",
            "║                                                  ║",
            "╠" + "═" * 54 + "╣",
            "║ SESSION SUMMARY                                 ║",
            f"║ Attempted: {session_stats['trades_attempted']:<36} ║",
            f"║ Filled: {session_stats['trades_filled']:<40} ║",
            f"║ Fill Rate: {session_stats['fill_rate']:.0%} ({session_stats['trades_filled']}/{session_stats['trades_attempted']}) {' ' * 30} ║",
            "╚" + "═" * 54 + "╝",
        ]

        return "\n".join(lines)

    async def _render_markets_view(self) -> str:
        """Per-market performance breakdown"""
        historical = await self.collector.get_historical(hours=1)

        if not historical:
            return (
                "╔" + "═" * 54 + "╗\n"
                "║        POLYMARKET SNIPER - MARKETS              ║\n"
                "║                                                  ║\n"
                "║ No data available yet                            ║\n"
                "╚" + "═" * 54 + "╝"
            )

        latest = historical[-1]

        lines = [
            "╔" + "═" * 54 + "╗",
            "║        POLYMARKET SNIPER - MARKETS              ║",
            "╠" + "═" * 54 + "╣",
            f"║ Markets Traded: {latest.markets_traded:<33} ║",
            f"║ Volume (USD): ${latest.total_volume_usd:>44.2f} ║",
            "║                                                  ║",
            "║ RECENT PERFORMANCE                             ║",
            "║                                                  ║",
            f"║ Trades: {latest.trades_attempted}/{latest.trades_filled} ({latest.fill_rate:.0%})                                 ║",
            f"║ P&L: ${latest.total_pnl:+.2f}                                      ║",
            f"║ Avg Edge: {latest.avg_edge_per_fill_cents:>5.2f}¢                                  ║",
            f"║ Win Rate: {latest.win_rate:>5.0%}                                    ║",
            "║                                                  ║",
            "╚" + "═" * 54 + "╝",
        ]

        return "\n".join(lines)

    async def _render_alerts_view(self) -> str:
        """Active alerts and warnings"""
        alerts = await self.collector.check_alerts()
        session_stats = await self.collector.get_session_stats()

        lines = [
            "╔" + "═" * 54 + "╗",
            "║        POLYMARKET SNIPER - ALERTS               ║",
            "╠" + "═" * 54 + "╣",
        ]

        if alerts:
            lines.append(f"║ ACTIVE ALERTS ({len(alerts)})                             ║")
            lines.append("║                                                  ║")
            for alert in alerts:
                # Wrap long alerts
                words = alert.split()
                current_line = ""
                for word in words:
                    if len(current_line) + len(word) + 1 <= 50:
                        current_line += word + " "
                    else:
                        if current_line:
                            lines.append(f"║ {current_line:<52} ║")
                        current_line = word + " "
                if current_line:
                    lines.append(f"║ {current_line:<52} ║")
        else:
            lines.append("║ No active alerts                                   ║")

        lines.append("║                                                  ║")
        lines.append("╠" + "═" * 54 + "╣")
        lines.append("║ HEALTH METRICS                                  ║")
        lines.append("║                                                  ║")
        lines.append(
            f"║ Missed Trades: {session_stats['missed_trades']:<36} ║"
        )
        lines.append(
            f"║ Kill Switches: {session_stats['kill_switches']:<37} ║"
        )
        lines.append(
            f"║ Circuit Breakers: {session_stats['circuit_breakers']:<33} ║"
        )
        lines.append("║                                                  ║")
        lines.append("╚" + "═" * 54 + "╝")

        return "\n".join(lines)

    def _progress_bar(
        self, value: float, max_value: float, width: int = 30
    ) -> str:
        """Render ASCII progress bar"""
        if max_value == 0:
            return "░" * width

        percentage = min(value / max_value, 1.0)
        filled = int(width * percentage)
        empty = width - filled

        return "█" * filled + "░" * empty

    def _colorize(self, text: str, color: str) -> str:
        """
        Add ANSI color codes.

        Colors: green, red, yellow
        """
        colors = {
            "green": "\033[92m",
            "red": "\033[91m",
            "yellow": "\033[93m",
            "reset": "\033[0m",
        }

        if color not in colors:
            return text

        return f"{colors[color]}{text}{colors['reset']}"


async def print_quick_stats(collector: MetricsCollector) -> None:
    """Print one-line summary to stdout"""
    fill_rate = await collector.get_current_fill_rate()
    pnl = await collector.get_current_pnl()
    latency = await collector.get_latency_stats()

    status = "OK" if latency["p95_decision_ms"] <= 30.0 else "WARN"
    print(
        f"[{status}] Fill: {fill_rate:.0%} | P&L: ${pnl:+.2f} | "
        f"p95 Latency: {latency['p95_decision_ms']:.0f}ms"
    )
