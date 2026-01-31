"""
Learning Capture
================

Automatic learning extraction from trade outcomes.

Hooks into the trading lifecycle to capture:
- Successful trade patterns (profitable exits)
- Error patterns (failed trades, API errors)
- Decision patterns (what worked, what didn't)

This creates a feedback loop for continuous improvement.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from .knowledge_store import KnowledgeStore

logger = logging.getLogger(__name__)


class LearningCapture:
    """
    Captures learnings from trade outcomes automatically.

    Integrates with:
    - OrderExecutor (capture trade results)
    - ExitManager (capture exit outcomes)
    - Strategies (capture signal quality)
    """

    def __init__(self, knowledge_store: KnowledgeStore):
        """
        Initialize learning capture.

        Args:
            knowledge_store: KnowledgeStore instance for persistence
        """
        self.knowledge_store = knowledge_store

        # Learning thresholds
        self.profit_threshold_significant = 0.02  # 2% profit = significant learning
        self.loss_threshold_significant = 0.01  # 1% loss = significant learning
        self.consecutive_wins_threshold = 3
        self.consecutive_losses_threshold = 2

        # Tracking state
        self.strategy_outcomes: Dict[str, List[Dict]] = {}  # strategy -> recent outcomes

    async def capture_trade_outcome(
        self,
        strategy: str,
        token_id: str,
        action: str,
        entry_price: float,
        exit_price: Optional[float],
        size: float,
        profit: float,
        exit_reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Capture learning from a trade outcome.

        Automatically generates learnings based on:
        - Profit/loss magnitude
        - Exit reason effectiveness
        - Pattern recognition across trades
        """
        # Calculate profit percentage
        profit_pct = profit / size if size > 0 else 0

        # Track outcome
        outcome = {
            "token_id": token_id,
            "action": action,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "profit": profit,
            "profit_pct": profit_pct,
            "exit_reason": exit_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if strategy not in self.strategy_outcomes:
            self.strategy_outcomes[strategy] = []
        self.strategy_outcomes[strategy].append(outcome)

        # Keep only last 50 outcomes per strategy
        if len(self.strategy_outcomes[strategy]) > 50:
            self.strategy_outcomes[strategy] = self.strategy_outcomes[strategy][-50:]

        # Generate learnings based on outcome
        await self._analyze_and_capture(strategy, outcome, metadata)

    async def _analyze_and_capture(
        self,
        strategy: str,
        outcome: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Analyze outcome and capture relevant learnings"""
        profit_pct = outcome["profit_pct"]
        exit_reason = outcome.get("exit_reason")

        # Significant profit - capture successful pattern
        if profit_pct >= self.profit_threshold_significant:
            content = (
                f"Successful trade pattern for {strategy}: "
                f"Entry at ${outcome['entry_price']:.3f}, "
                f"Exit at ${outcome['exit_price']:.3f} ({exit_reason}), "
                f"Profit: {profit_pct*100:.2f}%. "
                f"Exit reason '{exit_reason}' was effective."
            )
            await self.knowledge_store.add_learning(
                learning_type="successful_pattern",
                content=content,
                metadata={
                    "strategy": strategy,
                    "profit_pct": profit_pct,
                    "exit_reason": exit_reason,
                    "entry_price": outcome["entry_price"],
                    "exit_price": outcome["exit_price"],
                    **(metadata or {}),
                },
                tags=[strategy, exit_reason or "unknown", "profit"],
            )
            logger.info(f"Captured successful pattern: {profit_pct*100:.1f}% profit")

        # Significant loss - capture error pattern
        elif profit_pct <= -self.loss_threshold_significant:
            content = (
                f"Loss pattern for {strategy}: "
                f"Entry at ${outcome['entry_price']:.3f}, "
                f"Exit at ${outcome['exit_price']:.3f} ({exit_reason}), "
                f"Loss: {abs(profit_pct)*100:.2f}%. "
                f"Consider adjusting entry criteria or tighter stop-loss."
            )
            await self.knowledge_store.add_learning(
                learning_type="error_pattern",
                content=content,
                metadata={
                    "strategy": strategy,
                    "profit_pct": profit_pct,
                    "exit_reason": exit_reason,
                    "entry_price": outcome["entry_price"],
                    "exit_price": outcome["exit_price"],
                    **(metadata or {}),
                },
                tags=[strategy, exit_reason or "unknown", "loss"],
            )
            logger.info(f"Captured error pattern: {abs(profit_pct)*100:.1f}% loss")

        # Check for streaks
        await self._check_streaks(strategy)

    async def _check_streaks(self, strategy: str):
        """Check for winning/losing streaks and capture learnings"""
        outcomes = self.strategy_outcomes.get(strategy, [])
        if len(outcomes) < 3:
            return

        # Get recent outcomes
        recent = outcomes[-5:]

        # Count consecutive wins/losses
        consecutive_wins = 0
        consecutive_losses = 0

        for outcome in reversed(recent):
            if outcome["profit"] > 0:
                if consecutive_losses > 0:
                    break
                consecutive_wins += 1
            elif outcome["profit"] < 0:
                if consecutive_wins > 0:
                    break
                consecutive_losses += 1
            else:
                break

        # Capture streak patterns
        if consecutive_wins >= self.consecutive_wins_threshold:
            total_profit = sum(o["profit"] for o in recent[-consecutive_wins:])
            content = (
                f"Winning streak for {strategy}: {consecutive_wins} consecutive wins, "
                f"Total profit: ${total_profit:.2f}. "
                f"Current strategy parameters are working well."
            )
            await self.knowledge_store.add_learning(
                learning_type="successful_pattern",
                content=content,
                metadata={
                    "strategy": strategy,
                    "streak_length": consecutive_wins,
                    "total_profit": total_profit,
                    "pattern_type": "winning_streak",
                },
                tags=[strategy, "streak", "winning"],
            )
            logger.info(f"Captured winning streak: {consecutive_wins} wins")

        if consecutive_losses >= self.consecutive_losses_threshold:
            total_loss = sum(o["profit"] for o in recent[-consecutive_losses:])
            content = (
                f"Losing streak for {strategy}: {consecutive_losses} consecutive losses, "
                f"Total loss: ${abs(total_loss):.2f}. "
                f"Consider pausing strategy or adjusting parameters."
            )
            await self.knowledge_store.add_learning(
                learning_type="error_pattern",
                content=content,
                metadata={
                    "strategy": strategy,
                    "streak_length": consecutive_losses,
                    "total_loss": total_loss,
                    "pattern_type": "losing_streak",
                },
                tags=[strategy, "streak", "losing", "warning"],
            )
            logger.warning(f"Captured losing streak: {consecutive_losses} losses")

    async def capture_api_error(
        self,
        strategy: str,
        error_type: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None,
    ):
        """Capture API errors for pattern recognition"""
        content = (
            f"API error in {strategy}: {error_type} - {error_message}. "
            f"Context: {context or 'None provided'}."
        )
        await self.knowledge_store.add_learning(
            learning_type="error_pattern",
            content=content,
            metadata={
                "strategy": strategy,
                "error_type": error_type,
                "error_message": error_message,
                **(context or {}),
            },
            tags=[strategy, "api_error", error_type],
        )
        logger.info(f"Captured API error: {error_type}")

    async def capture_decision(
        self,
        strategy: str,
        decision: str,
        reasoning: str,
        outcome: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Capture a decision and its outcome for learning"""
        content = (
            f"Decision in {strategy}: {decision}. "
            f"Reasoning: {reasoning}. "
            f"Outcome: {outcome or 'Pending'}."
        )
        await self.knowledge_store.add_learning(
            learning_type="decision",
            content=content,
            metadata={
                "strategy": strategy,
                "decision": decision,
                "reasoning": reasoning,
                "outcome": outcome,
                **(metadata or {}),
            },
            tags=[strategy, "decision"],
        )

    async def get_relevant_learnings(
        self,
        strategy: str,
        context: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Get relevant learnings for a trading decision.

        Args:
            strategy: Strategy name
            context: Description of current situation
            n_results: Number of results to return

        Returns:
            List of relevant learnings
        """
        # Search with both context and strategy tag
        return await self.knowledge_store.search_learnings(
            query=f"{strategy} {context}",
            tags=[strategy],
            n_results=n_results,
        )
