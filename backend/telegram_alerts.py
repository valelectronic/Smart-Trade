"""Notification-only Telegram alerts for the Cantex multi-user Hunter.

Only the two approved alerts are exposed. There are no buttons, polling,
callbacks, or Telegram-triggered trading controls.
"""
from __future__ import annotations

import asyncio
import os

import requests

from postgres_database import get_user_by_id


def _send_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError("Telegram rejected the notification")
    return True


async def _user_label(user_id: str) -> str:
    """Resolve the registered username; retain the ID as an unambiguous reference."""
    clean_user_id = str(user_id)
    try:
        user = await asyncio.to_thread(get_user_by_id, clean_user_id)
        username = str(user.get("username") or "").strip() if user else ""
        if username:
            return f"{username} (ID: {clean_user_id})"
    except Exception as exc:
        # A username lookup failure must not suppress an important alert.
        print(f"[TELEGRAM WARNING] Username lookup failed ({type(exc).__name__}).")
    return f"Unknown username (ID: {clean_user_id})"


async def _safe_send(text: str) -> bool:
    try:
        sent = await asyncio.to_thread(_send_message, text)
        if not sent:
            print("[TELEGRAM WARNING] Notification not sent (bot token or chat ID is missing).")
        return sent
    except Exception as exc:
        # Never print the request URL because it contains the bot token.
        print(f"[TELEGRAM WARNING] Notification could not be sent ({type(exc).__name__}).")
        return False


async def send_daily_allowance_exhausted(user_id: str, daily_limit: int = 25) -> bool:
    user = await _user_label(user_id)
    return await _safe_send(
        "DAILY TARGET COMPLETED\n"
        f"User: {user}\n"
        f"Completed {daily_limit}/{daily_limit} successful swaps today.\n"
        "The Hunter has finished this account's daily target. No further swaps will be attempted until the daily reset.\n"
        "Your Hunter rules have been cleared. Set new rules when you resume tomorrow."
    )


async def send_transaction_needs_review(
    user_id: str, tx_id: str | None = None, reason: str | None = None
) -> bool:
    user = await _user_label(user_id)
    details = ["MANUAL ATTENTION REQUIRED", f"User: {user}"]
    if tx_id:
        details.append(f"Transaction: {tx_id}")
    if reason:
        details.append(f"Reason: {reason[:500]}")
    details.append("The Hunter has stopped this account and requires owner review in the application/database.")
    return await _safe_send("\n".join(details))
