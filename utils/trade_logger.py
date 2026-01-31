"""
trade_logger.py - Structured Trade Logging for RAG Analysis
============================================================

Outputs JSON-structured logs that can be ingested by RAG systems
for pattern recognition and strategy optimization.

Usage:
    from utils.trade_logger import TradeLogger

    logger = TradeLogger()
    logger.log_opportunity(market_data)
    logger.log_execution(trade_result)
    logger.log_settlement(settlement_data)
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path


class TradeLogger:
    """Structured trade logger for RAG-ready data collection."""

    def __init__(self, log_dir: str = "logs/trades"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Daily log files for easy querying
        self.current_date = datetime.utcnow().strftime("%Y-%m-%d")
        self.log_file = self.log_dir / f"trades_{self.current_date}.jsonl"

    def _rotate_if_needed(self):
        """Rotate log file at midnight UTC."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if today != self.current_date:
            self.current_date = today
            self.log_file = self.log_dir / f"trades_{self.current_date}.jsonl"

    def _write_log(self, event_type: str, data: Dict[str, Any]):
        """Write a structured log entry."""
        self._rotate_if_needed()

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            **data
        }

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_scan(self, markets_found: int, asset: str, scan_mode: str):
        """Log market scan results."""
        self._write_log("SCAN", {
            "markets_found": markets_found,
            "asset": asset,
            "scan_mode": scan_mode,
            "hour_utc": datetime.utcnow().hour,
            "day_of_week": datetime.utcnow().strftime("%A")
        })

    def log_opportunity(
        self,
        token_id: str,
        market_question: str,
        current_price: float,
        time_remaining_seconds: float,
        bid: float,
        ask: float,
        spread: float,
        is_neg_risk: bool,
        spot_price: Optional[float] = None,
        spot_change_pct: Optional[float] = None
    ):
        """Log detected trading opportunity."""
        self._write_log("OPPORTUNITY", {
            "token_id": token_id,
            "market_question": market_question[:100],  # Truncate for storage
            "current_price": round(current_price, 4),
            "time_remaining_seconds": round(time_remaining_seconds, 2),
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "spread": round(spread, 4),
            "implied_edge": round(1.0 - ask, 4) if ask > 0 else 0,
            "is_neg_risk": is_neg_risk,
            "spot_price": spot_price,
            "spot_change_pct": round(spot_change_pct, 4) if spot_change_pct else None,
            "hour_utc": datetime.utcnow().hour,
            "day_of_week": datetime.utcnow().strftime("%A")
        })

    def log_execution(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        order_type: str,
        success: bool,
        error_message: Optional[str] = None,
        execution_time_ms: Optional[float] = None,
        order_id: Optional[str] = None
    ):
        """Log trade execution attempt."""
        self._write_log("EXECUTION", {
            "token_id": token_id,
            "side": side,
            "size": round(size, 4),
            "price": round(price, 4),
            "order_type": order_type,
            "success": success,
            "error_message": error_message,
            "execution_time_ms": round(execution_time_ms, 2) if execution_time_ms else None,
            "order_id": order_id,
            "expected_profit": round((1.0 - price) * size, 4) if success else 0
        })

    def log_settlement(
        self,
        token_id: str,
        entry_price: float,
        settlement_price: float,
        size: float,
        pnl: float,
        win: bool,
        hold_time_seconds: float
    ):
        """Log trade settlement/result."""
        self._write_log("SETTLEMENT", {
            "token_id": token_id,
            "entry_price": round(entry_price, 4),
            "settlement_price": round(settlement_price, 4),
            "size": round(size, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round((pnl / (entry_price * size)) * 100, 2) if entry_price * size > 0 else 0,
            "win": win,
            "hold_time_seconds": round(hold_time_seconds, 2)
        })

    def log_skip(
        self,
        token_id: str,
        reason: str,
        current_price: float,
        time_remaining_seconds: float
    ):
        """Log why a trade was skipped."""
        self._write_log("SKIP", {
            "token_id": token_id,
            "reason": reason,
            "current_price": round(current_price, 4),
            "time_remaining_seconds": round(time_remaining_seconds, 2)
        })

    def log_error(
        self,
        error_type: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None
    ):
        """Log errors for debugging."""
        self._write_log("ERROR", {
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {}
        })

    def log_session_start(self, config: Dict[str, Any]):
        """Log bot session start with configuration."""
        # Sanitize config - never log private keys
        safe_config = {
            k: v for k, v in config.items()
            if "key" not in k.lower() and "secret" not in k.lower() and "pass" not in k.lower()
        }
        self._write_log("SESSION_START", {
            "config": safe_config
        })

    def log_session_end(self, stats: Dict[str, Any]):
        """Log bot session end with statistics."""
        self._write_log("SESSION_END", {
            "stats": stats
        })


# Convenience singleton
_default_logger: Optional[TradeLogger] = None

def get_trade_logger() -> TradeLogger:
    """Get the default trade logger instance."""
    global _default_logger
    if _default_logger is None:
        _default_logger = TradeLogger()
    return _default_logger
