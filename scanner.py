#!/usr/bin/env python3
"""
scanner.py - Find Active 15-Minute Crypto Markets
==================================================

Usage:
    python scanner.py
    python scanner.py --asset BTC
    python scanner.py --asset ETH
    python scanner.py --direct  # Use direct slug lookup (more reliable)
"""

import asyncio
import aiohttp
import argparse
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import pytz
from config import GAMMA_API

ET = pytz.timezone("America/New_York")


def get_15min_market_slug(coin: str = "btc") -> str:
    """
    Generate the correct slug for current 15-minute market.

    This is the PROVEN method from GitHub Issue #244 (Jan 2026).
    The slug format is: {coin}-updown-15m-{unix_timestamp}
    where timestamp is rounded down to nearest 900 seconds (15 min).
    """
    ts = int(time.time() // 900) * 900
    return f"{coin.lower()}-updown-15m-{ts}"


def get_next_15min_market_slug(coin: str = "btc") -> str:
    """Generate slug for the NEXT 15-minute market window."""
    ts = int(time.time() // 900) * 900 + 900  # Add 15 minutes
    return f"{coin.lower()}-updown-15m-{ts}"


async def fetch_market_by_slug(session: aiohttp.ClientSession, slug: str) -> Optional[Dict[str, Any]]:
    """
    Fetch market directly by slug - more reliable than search.

    This bypasses text matching issues and directly queries the market.
    """
    url = f"{GAMMA_API}/markets/{slug}"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 404:
                return None
    except Exception as e:
        print(f"[WARN] Failed to fetch market by slug: {e}")
    return None


async def fetch_15min_markets_direct(session: aiohttp.ClientSession, asset: str = "BTC") -> List[Dict[Any, Any]]:
    """
    Fetch 15-minute markets using direct slug lookup (RECOMMENDED).

    This is more reliable than the search-based approach, especially
    during off-peak hours when markets might not appear in general search.
    """
    markets = []
    coin = asset.lower()

    # Try current window
    current_slug = get_15min_market_slug(coin)
    market = await fetch_market_by_slug(session, current_slug)
    if market:
        market["_source"] = "direct_current"
        markets.append(market)
        print(f"[OK] Found current market: {current_slug}")
    else:
        print(f"[--] No market at: {current_slug}")

    # Try next window (for pre-positioning)
    next_slug = get_next_15min_market_slug(coin)
    market = await fetch_market_by_slug(session, next_slug)
    if market:
        market["_source"] = "direct_next"
        markets.append(market)
        print(f"[OK] Found next market: {next_slug}")

    return markets


def get_current_window() -> tuple[datetime, datetime]:
    """Calculate the current 15-minute window in ET."""
    now = datetime.now(ET)
    minute = (now.minute // 15) * 15
    window_start = now.replace(minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=15)
    return window_start, window_end


def format_time_remaining(end_time: datetime) -> str:
    """Format time remaining until market ends"""
    now = datetime.now(ET)
    remaining = end_time - now
    if remaining.total_seconds() < 0:
        return "ENDED"
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    return f"{minutes}m {seconds}s"


async def fetch_markets(session: aiohttp.ClientSession, asset: str = "BTC") -> List[Dict[Any, Any]]:
    """Fetch active markets from Gamma API."""
    asset_names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP"}
    asset_full = asset_names.get(asset.upper(), asset)

    all_markets = []
    url = f"{GAMMA_API}/markets"
    params = {"closed": "false", "active": "true", "_limit": 100}

    try:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                markets = await response.json()
                all_markets.extend(markets)
    except Exception as e:
        print(f"[WARN] Failed to fetch markets: {e}")

    # Filter for 15-minute crypto markets
    fifteen_min_markets = []
    now = datetime.now(ET)

    for market in all_markets:
        question = market.get("question", "").lower()
        description = market.get("description", "").lower()

        is_crypto = any(term.lower() in question or term.lower() in description
                       for term in [asset_full.lower(), asset.lower()])
        if not is_crypto:
            continue

        end_date_str = market.get("endDate") or market.get("end_date_iso")
        if not end_date_str:
            continue

        try:
            if "T" in end_date_str:
                end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            else:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d %H:%M:%S")
                end_date = ET.localize(end_date)

            end_date_et = end_date.astimezone(ET)
            time_until_end = (end_date_et - now).total_seconds()

            if 0 < time_until_end < 1200:  # Within 20 minutes
                market["end_date_et"] = end_date_et
                market["time_remaining_seconds"] = time_until_end
                fifteen_min_markets.append(market)
        except:
            continue

    fifteen_min_markets.sort(key=lambda x: x.get("time_remaining_seconds", float("inf")))
    return fifteen_min_markets


def extract_market_info(market: Dict[Any, Any]) -> Dict[str, Any]:
    """Extract relevant trading information from a market."""
    info = {
        "question": market.get("question", "Unknown"),
        "condition_id": market.get("conditionId") or market.get("condition_id"),
        "end_time": market.get("end_date_et"),
        "time_remaining": format_time_remaining(market.get("end_date_et")) if market.get("end_date_et") else "Unknown",
    }

    # Get tokens
    tokens = market.get("tokens", [])
    for token in tokens:
        outcome = token.get("outcome", "").upper()
        if outcome == "YES":
            info["yes_token_id"] = token.get("token_id")
            info["yes_price"] = token.get("price", 0)
        elif outcome == "NO":
            info["no_token_id"] = token.get("token_id")
            info["no_price"] = token.get("price", 0)

    if "clobTokenIds" in market:
        clob_tokens = market["clobTokenIds"]
        if len(clob_tokens) >= 2:
            info["yes_token_id"] = clob_tokens[0]
            info["no_token_id"] = clob_tokens[1]

    outcomes = market.get("outcomes", [])
    outcome_prices = market.get("outcomePrices", [])
    if outcomes and outcome_prices and len(outcomes) == len(outcome_prices):
        for outcome, price in zip(outcomes, outcome_prices):
            if outcome.upper() == "YES":
                info["yes_price"] = float(price) if price else 0
            elif outcome.upper() == "NO":
                info["no_price"] = float(price) if price else 0

    return info


async def scan_markets(asset: str = "BTC", use_direct: bool = False) -> List[Dict[str, Any]]:
    """
    Main function to scan for active markets.

    Args:
        asset: Crypto asset to scan (BTC, ETH, SOL, XRP)
        use_direct: Use direct slug lookup (more reliable for 15-min markets)
    """
    async with aiohttp.ClientSession() as session:
        if use_direct:
            # Use proven direct slug method (GitHub Issue #244 fix)
            raw_markets = await fetch_15min_markets_direct(session, asset)
        else:
            # Fall back to search-based method
            raw_markets = await fetch_markets(session, asset)

        results = []
        for market in raw_markets:
            info = extract_market_info(market)
            if info.get("condition_id"):
                # Add neg_risk flag to output (important for sniper.py)
                info["neg_risk"] = market.get("negRisk", False)
                results.append(info)
        return results


def print_market(market: Dict[str, Any], index: int):
    """Pretty print a market"""
    print(f"\n{'='*60}")
    print(f"Market #{index + 1}")
    print(f"{'='*60}")
    print(f"Question: {market.get('question', 'Unknown')}")
    print(f"Time Remaining: {market.get('time_remaining', 'Unknown')}")
    print()
    print(f"Condition ID: {market.get('condition_id', 'N/A')}")
    print()
    print("Token IDs:")
    print(f"  YES: {market.get('yes_token_id', 'N/A')}")
    print(f"  NO:  {market.get('no_token_id', 'N/A')}")
    print()
    yes_price = market.get('yes_price', 0)
    no_price = market.get('no_price', 0)
    print("Current Prices:")
    print(f"  YES: ${yes_price:.3f}" if yes_price else "  YES: N/A")
    print(f"  NO:  ${no_price:.3f}" if no_price else "  NO:  N/A")

    if yes_price and no_price:
        total = yes_price + no_price
        spread = 1.0 - total
        print()
        print(f"Combined: ${total:.3f}")
        if spread > 0:
            print(f"Arbitrage Spread: ${spread:.3f} ({spread*100:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Scan for active Polymarket 15-minute markets")
    parser.add_argument("--asset", "-a", default="BTC", choices=["BTC", "ETH", "SOL", "XRP"],
                       help="Asset to scan for (default: BTC)")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--direct", "-d", action="store_true",
                       help="Use direct slug lookup (more reliable, recommended)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"Polymarket Market Scanner - {args.asset}")
    print("=" * 60)

    window_start, window_end = get_current_window()
    print(f"Current Window: {window_start.strftime('%H:%M')} - {window_end.strftime('%H:%M')} ET")
    print(f"Current Time: {datetime.now(ET).strftime('%H:%M:%S')} ET")

    if args.direct:
        # Show the slugs we're looking for
        current_slug = get_15min_market_slug(args.asset)
        next_slug = get_next_15min_market_slug(args.asset)
        print(f"Looking for: {current_slug}")
        print(f"        and: {next_slug}")
    print()
    print(f"Scanning for active markets... {'(direct mode)' if args.direct else '(search mode)'}")

    markets = asyncio.run(scan_markets(args.asset, use_direct=args.direct))

    if not markets:
        print()
        print("[!] No active 15-minute markets found")
        if not args.direct:
            print("    TIP: Try --direct flag for more reliable detection")
        print("    15-min markets typically run 9 AM - 11 PM ET")
        return

    if args.json:
        import json
        print(json.dumps(markets, indent=2, default=str))
    else:
        print(f"\nFound {len(markets)} active market(s):")
        for i, market in enumerate(markets):
            print_market(market, i)
            # Show neg_risk warning
            if market.get("neg_risk"):
                print(f"  ⚠️  NEGATIVE RISK MARKET - sniper.py will handle automatically")

        print()
        print("=" * 60)
        print("To use with sniper.py, copy the YES Token ID above:")
        if markets:
            yes_token = markets[0].get('yes_token_id', 'N/A')
            print(f"  python sniper.py --token-id {yes_token}")
        print("=" * 60)


if __name__ == "__main__":
    main()
