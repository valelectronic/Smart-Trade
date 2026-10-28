"""Telegram notifications and non-executing button acknowledgements."""

import asyncio
import time

import requests


class TelegramAlerts:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)

        self.base_url = (
            f"https://api.telegram.org/bot{bot_token}"
        )

        self.offset = None
        self.last_warning_at = 0.0

    def _request(self, method, payload=None, timeout=15):
        response = requests.post(
            f"{self.base_url}/{method}",
            json=payload or {},
            timeout=timeout,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {result}"
            )

        return result.get("result")

    async def send_trade_ready(self, message, callback_id):
        """Send a trade-ready alert with non-executing buttons."""
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "reply_markup": {
                "inline_keyboard": [[
                    {
                        "text": "✅ EXECUTE",
                        "callback_data": f"execute:{callback_id}",
                    },
                    {
                        "text": "❌ SKIP",
                        "callback_data": f"skip:{callback_id}",
                    },
                ]]
            },
            "disable_web_page_preview": True,
        }

        return await asyncio.to_thread(
            self._request,
            "sendMessage",
            payload,
            15,
        )

    async def _handle_update(self, update):
        callback = update.get("callback_query")

        if not callback:
            return

        callback_id = callback.get("id")
        data = callback.get("data", "")

        message = callback.get("message") or {}

        callback_chat = (
            message.get("chat") or {}
        ).get("id")

        if data.startswith("execute:"):
            reply = (
                "Execution logic pending — "
                "no swap executed."
            )

        elif data.startswith("skip:"):
            reply = "Skipped — no swap executed."

        else:
            reply = (
                "Unknown button action. "
                "No swap executed."
            )

        try:
            await asyncio.to_thread(
                self._request,
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": reply,
                    "show_alert": True,
                },
                15,
            )

        except Exception as exc:
            print(
                "[TELEGRAM WARNING] Could not acknowledge "
                f"button click: {exc}"
            )

        target_chat = (
            callback_chat
            if callback_chat is not None
            else self.chat_id
        )

        try:
            await asyncio.to_thread(
                self._request,
                "sendMessage",
                {
                    "chat_id": target_chat,
                    "text": reply,
                },
                15,
            )

        except Exception as exc:
            print(
                "[TELEGRAM WARNING] Could not send "
                f"button response: {exc}"
            )

    async def poll(self):
        """Poll Telegram for button clicks without trading actions."""
        print(
            "[TELEGRAM] Button polling started. "
            "EXECUTE only acknowledges pending logic."
        )

        while True:
            try:
                params = {
                    "timeout": 20,
                    "allowed_updates": ["callback_query"],
                }

                if self.offset is not None:
                    params["offset"] = self.offset

                result = await asyncio.to_thread(
                    self._request,
                    "getUpdates",
                    params,
                    30,
                )

                for update in result or []:
                    self.offset = max(
                        self.offset or 0,
                        update.get("update_id", 0) + 1,
                    )

                    await self._handle_update(update)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                now = time.monotonic()

                if now - self.last_warning_at >= 30:
                    print(
                        "[TELEGRAM WARNING] Polling failed; "
                        f"retrying: {exc}"
                    )

                    self.last_warning_at = now

                await asyncio.sleep(3)