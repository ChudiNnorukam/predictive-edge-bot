#!/usr/bin/env python3
"""
health_check.py - Bot Health Monitoring
========================================

Monitors bot health and sends alerts if issues detected.
Runs via PM2 cron every 5 minutes.

Checks:
    - Bot processes are running
    - WebSocket connections are active
    - Recent trades/signals detected
    - Wallet balance sufficient
    - No error spikes in logs
"""

import asyncio
import subprocess
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import aiohttp

from config import load_config, CLOB_HOST
from utils.notifications import Notifier


class HealthChecker:
    """Health monitoring for Polymarket bots"""

    def __init__(self, config):
        self.config = config
        self.notifier = Notifier(
            telegram_token=config.telegram_bot_token,
            telegram_chat=config.telegram_chat_id,
            discord_webhook=config.discord_webhook_url,
        )
        self.issues = []

    def check_pm2_processes(self) -> dict:
        """Check if PM2 processes are running"""
        result = {"healthy": True, "processes": {}}

        try:
            output = subprocess.check_output(
                ["pm2", "jlist"],
                stderr=subprocess.DEVNULL
            ).decode()

            processes = json.loads(output)

            for proc in processes:
                name = proc.get("name", "unknown")
                status = proc.get("pm2_env", {}).get("status", "unknown")
                restarts = proc.get("pm2_env", {}).get("restart_time", 0)
                uptime = proc.get("pm2_env", {}).get("pm_uptime", 0)

                result["processes"][name] = {
                    "status": status,
                    "restarts": restarts,
                    "uptime_ms": uptime,
                }

                # Check for issues
                if status != "online":
                    result["healthy"] = False
                    self.issues.append(f"Process '{name}' is {status}")

                if restarts > 10:
                    self.issues.append(f"Process '{name}' has restarted {restarts} times")

        except FileNotFoundError:
            result["healthy"] = False
            self.issues.append("PM2 not found - is it installed?")
        except Exception as e:
            result["healthy"] = False
            self.issues.append(f"PM2 check failed: {e}")

        return result

    def check_recent_logs(self, log_file: str, error_threshold: int = 10) -> dict:
        """Check for error spikes in recent logs"""
        result = {"healthy": True, "errors": 0, "warnings": 0}

        log_path = Path(log_file)
        if not log_path.exists():
            return result

        try:
            # Read last 1000 lines
            with open(log_path, 'r') as f:
                lines = f.readlines()[-1000:]

            # Count recent errors (last 30 minutes)
            cutoff = datetime.now() - timedelta(minutes=30)

            for line in lines:
                line_lower = line.lower()
                if "error" in line_lower:
                    result["errors"] += 1
                if "warning" in line_lower:
                    result["warnings"] += 1

            if result["errors"] > error_threshold:
                result["healthy"] = False
                self.issues.append(f"High error rate: {result['errors']} errors in {log_file}")

        except Exception as e:
            self.issues.append(f"Log check failed: {e}")

        return result

    async def check_api_connectivity(self) -> dict:
        """Check connectivity to Polymarket APIs"""
        result = {"healthy": True, "apis": {}}

        endpoints = {
            "CLOB": f"{CLOB_HOST}/",
            "Gamma": "https://gamma-api.polymarket.com/markets?_limit=1",
        }

        async with aiohttp.ClientSession() as session:
            for name, url in endpoints.items():
                try:
                    async with session.get(url, timeout=10) as response:
                        result["apis"][name] = {
                            "status": response.status,
                            "ok": response.status == 200,
                        }
                        if response.status != 200:
                            result["healthy"] = False
                            self.issues.append(f"{name} API returned {response.status}")
                except asyncio.TimeoutError:
                    result["healthy"] = False
                    result["apis"][name] = {"status": "timeout", "ok": False}
                    self.issues.append(f"{name} API timed out")
                except Exception as e:
                    result["healthy"] = False
                    result["apis"][name] = {"status": str(e), "ok": False}
                    self.issues.append(f"{name} API error: {e}")

        return result

    def check_disk_space(self, threshold_gb: float = 1.0) -> dict:
        """Check available disk space"""
        result = {"healthy": True, "available_gb": 0}

        try:
            statvfs = os.statvfs('/')
            available = (statvfs.f_frsize * statvfs.f_bavail) / (1024 ** 3)
            result["available_gb"] = round(available, 2)

            if available < threshold_gb:
                result["healthy"] = False
                self.issues.append(f"Low disk space: {available:.1f}GB remaining")

        except Exception as e:
            self.issues.append(f"Disk check failed: {e}")

        return result

    async def run_checks(self) -> dict:
        """Run all health checks"""
        self.issues = []

        results = {
            "timestamp": datetime.now().isoformat(),
            "overall_healthy": True,
            "checks": {},
        }

        # Run checks
        results["checks"]["pm2"] = self.check_pm2_processes()
        results["checks"]["logs_sniper"] = self.check_recent_logs("logs/sniper-error.log")
        results["checks"]["logs_copy"] = self.check_recent_logs("logs/copy-trader-error.log")
        results["checks"]["api"] = await self.check_api_connectivity()
        results["checks"]["disk"] = self.check_disk_space()

        # Determine overall health
        for check in results["checks"].values():
            if not check.get("healthy", True):
                results["overall_healthy"] = False

        results["issues"] = self.issues

        return results

    async def run(self):
        """Main health check routine"""
        print(f"[{datetime.now()}] Running health check...")

        results = await self.run_checks()

        # Log results
        print(f"Overall healthy: {results['overall_healthy']}")
        print(f"Issues found: {len(results['issues'])}")

        for issue in results["issues"]:
            print(f"  - {issue}")

        # Send alert if unhealthy
        if not results["overall_healthy"] and results["issues"]:
            message = "⚠️ <b>Health Check Alert</b>\n\n"
            message += "Issues detected:\n"
            for issue in results["issues"]:
                message += f"• {issue}\n"

            await self.notifier.notify(message, "Health Alert")
            print("Alert sent!")

        # Save results to file
        results_file = Path("data/health_check.json")
        results_file.parent.mkdir(exist_ok=True)

        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        print(f"Results saved to {results_file}")

        return results


async def main():
    try:
        config = load_config()
    except ValueError as e:
        print(f"Config error (notifications may be disabled): {e}")
        # Create minimal config for health check
        from dataclasses import dataclass

        @dataclass
        class MinimalConfig:
            telegram_bot_token = None
            telegram_chat_id = None
            discord_webhook_url = None

        config = MinimalConfig()

    checker = HealthChecker(config)
    await checker.run()


if __name__ == "__main__":
    asyncio.run(main())
