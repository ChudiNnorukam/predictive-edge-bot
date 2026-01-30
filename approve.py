#!/usr/bin/env python3
"""
approve.py - Authorize USDC Spending on Polymarket
==================================================

This script authorizes the Polymarket exchange to spend your USDC.
You only need to run this ONCE before trading.

Usage:
    python approve.py
"""

import sys
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds
from config import load_config, CLOB_HOST


def main():
    print("=" * 60)
    print("Polymarket USDC Approval Script")
    print("=" * 60)
    print()

    # Load configuration
    try:
        config = load_config()
        print("[OK] Configuration loaded")
    except ValueError as e:
        print(f"[ERROR] {e}")
        print("Please copy .env.example to .env and fill in your values")
        sys.exit(1)

    # Initialize CLOB client
    print(f"[...] Connecting to Polymarket (Chain ID: {config.chain_id})")

    try:
        client = ClobClient(
            host=CLOB_HOST,
            key=config.private_key,
            chain_id=config.chain_id,
            signature_type=1,  # 1 for EOA wallets (MetaMask/Phantom)
            funder=config.wallet_address,
        )
        print("[OK] CLOB client initialized")
    except Exception as e:
        print(f"[ERROR] Failed to initialize client: {e}")
        sys.exit(1)

    # Create or derive API credentials
    print("[...] Setting up API credentials")
    try:
        if config.clob_api_key and config.clob_secret and config.clob_passphrase:
            creds = ApiCreds(
                api_key=config.clob_api_key,
                api_secret=config.clob_secret,
                api_passphrase=config.clob_passphrase,
            )
            client.set_api_creds(creds)
            print("[OK] API credentials set from .env")
        else:
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
            print("[OK] API credentials derived from private key")
            print()
            print("TIP: Save these credentials to your .env file:")
            print(f"    CLOB_API_KEY={creds.api_key}")
            print(f"    CLOB_SECRET={creds.api_secret}")
            print(f"    CLOB_PASSPHRASE={creds.api_passphrase}")
            print()
    except Exception as e:
        print(f"[ERROR] Failed to set API credentials: {e}")
        sys.exit(1)

    # Set allowance (approve USDC spending)
    print("[...] Approving USDC spending on Polymarket exchange")
    print("      This may take a moment for the transaction to confirm...")

    try:
        result = client.set_allowance()
        print()
        print("[SUCCESS] USDC spending approved!")
        print()
        print("You can now run scanner.py to find markets")
        print("and sniper.py to execute trades.")
        if result:
            print(f"Transaction result: {result}")
    except Exception as e:
        error_msg = str(e)
        if "already approved" in error_msg.lower():
            print()
            print("[OK] USDC spending was already approved!")
            print("     You're ready to trade.")
        else:
            print(f"[ERROR] Failed to set allowance: {e}")
            print()
            print("Common issues:")
            print("  - Insufficient MATIC for gas fees")
            print("  - Invalid private key")
            print("  - Network connectivity issues")
            sys.exit(1)

    print()
    print("=" * 60)
    print("Setup complete! Next steps:")
    print("  1. Run: python scanner.py  (find active markets)")
    print("  2. Run: python sniper.py   (start trading bot)")
    print("=" * 60)


if __name__ == "__main__":
    main()
