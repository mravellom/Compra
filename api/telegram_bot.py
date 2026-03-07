"""
Telegram Bot para enviar alertas de oportunidades.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

ROI_ALERT_THRESHOLD = float(os.getenv("ROI_ALERT_THRESHOLD", "0.20"))  # 20%


def format_opportunity_message(
    product_name: str,
    buy_price: float,
    sell_price: float,
    net_profit: float,
    roi: float,
    buy_marketplace: str,
    sell_marketplace: str,
    buy_url: str,
    sell_url: str,
) -> str:
    roi_emoji = "🔥" if roi >= 0.50 else "🟢" if roi >= 0.30 else "📈"

    return (
        f"{roi_emoji} *Oportunidad de Arbitraje*\n"
        f"\n"
        f"📦 *{product_name}*\n"
        f"\n"
        f"💰 Comprar: ${buy_price:.2f} ({buy_marketplace})\n"
        f"💵 Vender: ${sell_price:.2f} ({sell_marketplace})\n"
        f"\n"
        f"📊 *Profit: ${net_profit:.2f}*\n"
        f"📈 *ROI: {roi * 100:.1f}%*\n"
        f"\n"
        f"🔗 [Comprar]({buy_url})\n"
        f"🔗 [Vender]({sell_url})"
    )


async def send_telegram_alert(chat_id: str, message: str) -> bool:
    """Envía un mensaje formateado a un chat de Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping alert")
        return False

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
            logger.info("Telegram alert sent to chat %s", chat_id)
            return True
        except httpx.HTTPError:
            logger.error("Failed to send Telegram alert to %s", chat_id, exc_info=True)
            return False


async def notify_opportunity(
    chat_id: str,
    product_name: str,
    buy_price: float,
    sell_price: float,
    net_profit: float,
    roi: float,
    buy_marketplace: str,
    sell_marketplace: str,
    buy_url: str,
    sell_url: str,
) -> bool:
    """Envía alerta solo si el ROI supera el threshold."""
    if roi < ROI_ALERT_THRESHOLD:
        return False

    message = format_opportunity_message(
        product_name, buy_price, sell_price, net_profit, roi,
        buy_marketplace, sell_marketplace, buy_url, sell_url,
    )
    return await send_telegram_alert(chat_id, message)
