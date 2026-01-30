"""
Notification utilities - Telegram & Discord alerts
"""

import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Notifier:
    """Send alerts to Telegram and Discord"""

    def __init__(self, telegram_token: str = None, telegram_chat: str = None, discord_webhook: str = None):
        self.telegram_token = telegram_token
        self.telegram_chat = telegram_chat
        self.discord_webhook = discord_webhook

    async def send_telegram(self, message: str) -> bool:
        if not self.telegram_token or not self.telegram_chat:
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat, "text": message, "parse_mode": "HTML"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
        return False

    async def send_discord(self, message: str, title: str = "Polymarket Bot") -> bool:
        if not self.discord_webhook:
            return False
        payload = {"embeds": [{"title": title, "description": message, "color": 5814783}]}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.discord_webhook, json=payload) as response:
                    return response.status in (200, 204)
        except Exception as e:
            logger.error(f"Discord error: {e}")
        return False

    async def notify(self, message: str, title: str = "Polymarket Bot"):
        """Send to all channels"""
        logger.info(f"[NOTIFY] {message}")
        await self.send_telegram(message)
        await self.send_discord(message, title)

    async def trade_alert(self, action: str, side: str, price: float, size: float, profit: float = None):
        msg = f"🔔 <b>{action}</b>\nSide: {side}\nPrice: ${price:.3f}\nSize: ${size:.2f}"
        if profit:
            msg += f"\n💰 Profit: ${profit:.4f}"
        await self.notify(msg, f"Trade: {action}")
