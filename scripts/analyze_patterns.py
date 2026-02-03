#!/usr/bin/env python3
"""
analyze_patterns.py - Pattern Analysis for Trade Journal
========================================================

Analyzes trade patterns from JSONL trade journal files to identify:
- Winning vs losing trade patterns
- Statistical confidence in pattern performance
- Recommended patterns to reinforce
- Patterns to veto based on poor performance

Usage:
    python scripts/analyze_patterns.py [--verbose]
"""

import json
import math
import argparse
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Optional


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Constants
WARMUP_MIN_SAMPLE = 2  # For first 500 total trades
PRODUCTION_MIN_SAMPLE = 5  # After 500 trades
REINFORCE_WIN_RATE = 0.60  # 60%+ win rate
VETO_WIN_RATE = 0.40  # <40% win rate
VETO_LOSS_RATE = 0.60  # 60%+ loss rate
VETO_MIN_SAMPLE = 5  # Minimum sample size for veto recommendation


def wilson_score_interval(wins: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """
    Calculate Wilson score 95% confidence interval.

    Args:
        wins: Number of winning trades
        total: Total number of trades
        z: Z-score for 95% confidence (default 1.96)

    Returns:
        Tuple of (lower_bound, upper_bound)
    """
    if total < 3:
        return (0.0, 1.0)  # Conservative bounds for small N

    if total == 0:
        return (0.0, 0.0)

    p = wins / total
    denominator = 1 + z**2 / total
    center = p + z**2 / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total)

    lower = (center - margin) / denominator
    upper = (center + margin) / denominator

    return (max(0.0, lower), min(1.0, upper))


def calculate_age_weight(trade_timestamp: str) -> float:
    """
    Calculate age weight with 90-day half-life decay.

    Args:
        trade_timestamp: ISO timestamp string (may contain 'Z')

    Returns:
        Weight factor between 0 and 1
    """
    try:
        # Handle both "Z" suffix and timezone-aware formats
        ts_clean = trade_timestamp.replace('Z', '+00:00')
        trade_time = datetime.fromisoformat(ts_clean)
    except (ValueError, AttributeError):
        logger.warning(f"Invalid timestamp: {trade_timestamp}, using weight 0.5")
        return 0.5

    # Get current time in UTC
    now = datetime.now(trade_time.tzinfo) if trade_time.tzinfo else datetime.utcnow()
    age_days = (now - trade_time).days

    # Clamp to prevent negative ages (future timestamps)
    age_days = max(0, age_days)

    return math.exp(-age_days / 90)


def load_trades(journal_dir: str) -> List[Dict[str, Any]]:
    """
    Load all trades from JSONL files in journal directory.

    Args:
        journal_dir: Directory containing trade_*.jsonl files

    Returns:
        List of trade dictionaries
    """
    trades = []
    journal_path = Path(journal_dir)

    if not journal_path.exists():
        logger.warning(f"Journal directory not found: {journal_dir}")
        return trades

    jsonl_files = sorted(journal_path.glob("trade_*.jsonl"))

    if not jsonl_files:
        logger.warning(f"No JSONL files found in {journal_dir}")
        return trades

    for jsonl_file in jsonl_files:
        try:
            with open(jsonl_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        trade = json.loads(line)
                        # Only include SETTLEMENT events (completed trades)
                        if trade.get("event_type") == "SETTLEMENT":
                            trades.append(trade)
                    except json.JSONDecodeError as e:
                        logger.warning(f"{jsonl_file}:{line_num} - Invalid JSON: {e}")
        except IOError as e:
            logger.error(f"Error reading {jsonl_file}: {e}")

    logger.info(f"Loaded {len(trades)} settlement trades from {len(jsonl_files)} files")
    return trades


def group_by_tags(trades: List[Dict[str, Any]]) -> Dict[Tuple[str, ...], List[Dict[str, Any]]]:
    """
    Group trades by tag combinations.

    Args:
        trades: List of trade dictionaries

    Returns:
        Dictionary mapping tag tuples to list of trades
    """
    grouped = defaultdict(list)

    for trade in trades:
        # Extract tags, default to empty list
        tags = trade.get("tags", [])
        if not tags:
            tags = ["untagged"]

        # Sort tags for consistency
        tag_key = tuple(sorted(tags))
        grouped[tag_key].append(trade)

    logger.info(f"Grouped {len(trades)} trades into {len(grouped)} patterns")
    return grouped


def calculate_pattern_stats(
    trades: List[Dict[str, Any]],
    total_trades: int
) -> Dict[str, Any]:
    """
    Calculate statistics for a pattern's trades.

    Args:
        trades: List of trades for this pattern
        total_trades: Total trades in the system (for warmup mode determination)

    Returns:
        Dictionary with win_rate, sample_size, confidence bounds, expected_value_usd
    """
    if not trades:
        return {}

    sample_size = len(trades)

    # Count wins (trades where 'win' field is True)
    wins = sum(1 for t in trades if t.get("win", False))
    losses = sample_size - wins

    # Determine minimum sample size requirement
    min_sample = WARMUP_MIN_SAMPLE if total_trades < 500 else PRODUCTION_MIN_SAMPLE

    # Only include patterns that meet minimum sample requirement
    if sample_size < min_sample:
        return {}

    win_rate = wins / sample_size if sample_size > 0 else 0.0
    loss_rate = losses / sample_size if sample_size > 0 else 0.0

    # Calculate Wilson score confidence interval
    lower, upper = wilson_score_interval(wins, sample_size)

    # Calculate expected value in USD
    # Sum all PnL values and divide by sample size
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    expected_value_usd = total_pnl / sample_size if sample_size > 0 else 0.0

    return {
        "sample_size": sample_size,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "loss_rate": round(loss_rate, 4),
        "confidence_lower": round(lower, 4),
        "confidence_upper": round(upper, 4),
        "expected_value_usd": round(expected_value_usd, 4),
        "total_pnl": round(total_pnl, 4)
    }


def apply_age_decay(
    pattern_groups: Dict[Tuple[str, ...], List[Dict[str, Any]]]
) -> Dict[Tuple[str, ...], List[Dict[str, Any]]]:
    """
    Apply age decay weighting to trades (for future use).
    Currently returns trades as-is, but calculates weights.

    Args:
        pattern_groups: Dictionary mapping tag tuples to trades

    Returns:
        Dictionary with age-weighted information
    """
    decayed_groups = {}

    for tags, trades in pattern_groups.items():
        decayed_trades = []
        for trade in trades:
            weight = calculate_age_weight(trade.get("timestamp", ""))
            trade_with_weight = {**trade, "_age_weight": weight}
            decayed_trades.append(trade_with_weight)

        decayed_groups[tags] = decayed_trades

    return decayed_groups


def classify_patterns(
    stats_by_pattern: Dict[Tuple[str, ...], Dict[str, Any]],
    total_trades: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Classify patterns as "reinforce" or "veto" candidates.

    Args:
        stats_by_pattern: Dictionary mapping tag tuples to statistics
        total_trades: Total number of trades in system

    Returns:
        Tuple of (reinforce_patterns, veto_candidates)
    """
    reinforce = []
    veto = []

    for tags, stats in stats_by_pattern.items():
        if not stats:  # Skip patterns with insufficient data
            continue

        pattern_info = {
            "tags": list(tags),
            "sample_size": stats["sample_size"],
            "win_rate": stats["win_rate"],
            "confidence_lower": stats["confidence_lower"],
            "confidence_upper": stats["confidence_upper"],
            "expected_value_usd": stats["expected_value_usd"]
        }

        win_rate = stats["win_rate"]
        loss_rate = stats["loss_rate"]
        sample_size = stats["sample_size"]

        # Determine warmup mode
        warmup_mode = total_trades < 500

        # Check for reinforce recommendation (60%+ win rate)
        if win_rate >= REINFORCE_WIN_RATE:
            pattern_info["recommendation"] = "reinforce"
            reinforce.append(pattern_info)

        # Check for veto candidate (<40% win rate AND 5+ samples AND 60%+ loss rate)
        elif (win_rate < VETO_WIN_RATE and
              sample_size >= VETO_MIN_SAMPLE and
              loss_rate >= VETO_LOSS_RATE):
            pattern_info["recommendation"] = "veto"
            pattern_info["loss_rate"] = loss_rate
            veto.append(pattern_info)

    return reinforce, veto


def main():
    """Main entry point for pattern analysis."""
    parser = argparse.ArgumentParser(description="Analyze trading patterns from journal")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    logger.info("Starting pattern analysis...")

    # Determine paths
    base_dir = Path(__file__).parent.parent
    journal_dir = base_dir / "data" / "trade_journal"
    output_file = base_dir / "data" / "pattern_analysis.json"

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Load trades
    trades = load_trades(str(journal_dir))
    total_trades = len(trades)

    if total_trades == 0:
        logger.warning("No trades found. Creating empty analysis file.")
        output = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_trades": 0,
            "warmup_mode": True,
            "patterns": [],
            "veto_candidates": []
        }
        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"Analysis written to {output_file}")
        return

    # Group by tags
    pattern_groups = group_by_tags(trades)

    # Apply age decay (for informational purposes)
    decayed_groups = apply_age_decay(pattern_groups)

    # Calculate statistics for each pattern
    stats_by_pattern = {}
    for tags, decayed_trades in decayed_groups.items():
        stats = calculate_pattern_stats(decayed_trades, total_trades)
        if stats:  # Only include patterns with sufficient data
            stats_by_pattern[tags] = stats

    # Classify patterns
    reinforce_patterns, veto_candidates = classify_patterns(stats_by_pattern, total_trades)

    # Determine warmup mode
    warmup_mode = total_trades < 500

    # Build output structure
    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_trades": total_trades,
        "warmup_mode": warmup_mode,
        "patterns": sorted(reinforce_patterns, key=lambda p: p["expected_value_usd"], reverse=True),
        "veto_candidates": sorted(veto_candidates, key=lambda p: p["win_rate"])
    }

    # Write output file
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    # Print summary
    logger.info("=" * 60)
    logger.info("PATTERN ANALYSIS SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total trades analyzed: {total_trades}")
    logger.info(f"Warmup mode: {warmup_mode}")
    logger.info(f"Total patterns identified: {len(stats_by_pattern)}")
    logger.info(f"Patterns to reinforce: {len(reinforce_patterns)}")
    logger.info(f"Patterns to veto: {len(veto_candidates)}")
    logger.info("")

    if reinforce_patterns:
        logger.info("TOP PATTERNS TO REINFORCE:")
        for i, pattern in enumerate(reinforce_patterns[:5], 1):
            logger.info(f"  {i}. Tags: {', '.join(pattern['tags'])}")
            logger.info(f"     Win Rate: {pattern['win_rate']:.1%} | "
                       f"Samples: {pattern['sample_size']} | "
                       f"EV: ${pattern['expected_value_usd']:.2f}")

    if veto_candidates:
        logger.info("")
        logger.info("PATTERNS TO VETO:")
        for i, pattern in enumerate(veto_candidates[:5], 1):
            logger.info(f"  {i}. Tags: {', '.join(pattern['tags'])}")
            logger.info(f"     Win Rate: {pattern['win_rate']:.1%} | "
                       f"Samples: {pattern['sample_size']}")

    logger.info("")
    logger.info(f"Analysis written to {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
