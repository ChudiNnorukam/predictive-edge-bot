"""
Execution Window Manager
========================

Manages the T-60s → T-15s → T-3s → T-0 execution phases for individual markets.
Determines which phase a market is in based on time to expiry and coordinates
order preparation, priming, and execution.

Each market gets its own ExecutionWindow instance to track state independently.
"""

import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class ExecutionWindow:
    """
    Manages the lifecycle of a single market from preparation through post-resolution.

    Tracks phases:
    - PREPARATION: T-60s to T-15s - Ensure feed is live, validate fair price
    - PRIMING: T-15s to T-3s - Pre-compute order size, unlock wallet
    - EXECUTION: T-3s to T-0 - Send FOK orders
    - POST_RESOLUTION: After T-0 - Reconcile, cancel unfilled orders
    """

    class Phase(Enum):
        """Execution phases aligned with market expiry"""
        PREPARATION = "preparation"    # T-60s to T-15s
        PRIMING = "priming"            # T-15s to T-3s
        EXECUTION = "execution"        # T-3s to T-0
        POST_RESOLUTION = "post"       # After T-0

    def __init__(self, token_id: str, expiry_timestamp: float):
        """
        Initialize execution window for a market.

        Args:
            token_id: Polymarket token ID
            expiry_timestamp: Unix timestamp when market expires
        """
        self.token_id = token_id
        self.expiry_timestamp = expiry_timestamp
        self.phase = self.Phase.PREPARATION
        self.order_prepared: Optional[dict] = None
        self.order_sent: bool = False
        self.created_at = datetime.now(timezone.utc)
        self.phase_transitions: list[tuple[Phase, float]] = []

    def time_to_expiry_seconds(self) -> float:
        """Calculate seconds until market expiry"""
        now = datetime.now(timezone.utc).timestamp()
        return max(0, self.expiry_timestamp - now)

    def current_phase(self) -> "ExecutionWindow.Phase":
        """
        Determine current phase based on time to expiry.

        Returns:
            Current phase of execution
        """
        tte = self.time_to_expiry_seconds()

        if tte > 60:
            new_phase = self.Phase.PREPARATION
        elif tte > 15:
            new_phase = self.Phase.PREPARATION
        elif tte > 3:
            new_phase = self.Phase.PRIMING
        elif tte > 0:
            new_phase = self.Phase.EXECUTION
        else:
            new_phase = self.Phase.POST_RESOLUTION

        # Track phase transitions for debugging
        if new_phase != self.phase:
            self.phase = new_phase
            self.phase_transitions.append((new_phase, self.time_to_expiry_seconds()))
            logger.debug(
                f"Market {self.token_id} transitioned to {new_phase.value} "
                f"(TTE: {self.time_to_expiry_seconds():.2f}s)"
            )

        return self.phase

    def should_prepare_order(self) -> bool:
        """
        Check if we should prepare order (calculate size, validate price).

        Returns True when entering or in PREPARATION phase.
        """
        phase = self.current_phase()
        return phase == self.Phase.PREPARATION and not self.order_prepared

    def should_prime(self) -> bool:
        """
        Check if we should prime (pre-compute and validate order).

        Returns True when entering or in PRIMING phase.
        """
        phase = self.current_phase()
        return phase == self.Phase.PRIMING and self.order_prepared and not self.order_sent

    def should_execute(self) -> bool:
        """
        Check if we should execute order (send to CLOB).

        Returns True when in EXECUTION phase and order is primed.
        """
        phase = self.current_phase()
        return phase == self.Phase.EXECUTION and self.order_prepared and not self.order_sent

    def is_resolved(self) -> bool:
        """
        Check if market has resolved (past T-0).

        Returns True when in POST_RESOLUTION phase.
        """
        return self.current_phase() == self.Phase.POST_RESOLUTION

    def mark_order_prepared(self, order_data: dict) -> None:
        """
        Record that order has been prepared.

        Args:
            order_data: Order request details (size, price, etc.)
        """
        self.order_prepared = order_data
        logger.debug(f"Market {self.token_id} order prepared: {order_data}")

    def mark_order_sent(self) -> None:
        """Record that order has been sent to CLOB"""
        self.order_sent = True
        logger.debug(f"Market {self.token_id} order sent to CLOB")

    def get_debug_info(self) -> dict:
        """Get debugging information about window state"""
        return {
            "token_id": self.token_id,
            "phase": self.phase.value,
            "time_to_expiry": self.time_to_expiry_seconds(),
            "order_prepared": self.order_prepared is not None,
            "order_sent": self.order_sent,
            "phase_transitions": [
                (phase.value, tte) for phase, tte in self.phase_transitions
            ],
        }
