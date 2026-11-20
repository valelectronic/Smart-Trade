"""Start the FastAPI server, Cantex monitor, and Rebate Hunter together."""

import asyncio
import os
import time

import uvicorn

from api import app
from postgres_database import init_db
from monitor import run_monitor
from rebate_hunter import main as run_rebate_hunter

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 8000))

MONITOR_RETRY_SECONDS = 5
HUNTER_RETRY_SECONDS = 10
HUNTER_CONTROL_RETRY_SECONDS = 60
HUNTER_MANUAL_REVIEW_RETRY_SECONDS = 300
HUNTER_DAILY_LIMIT_FALLBACK_SECONDS = 3600
API_STARTUP_TIMEOUT_SECONDS = 30


async def monitor_supervisor():
    """Restart the monitoring engine if it exits unexpectedly."""
    while True:
        try:
            print("[ENGINE] Starting Cantex monitoring engine.")
            await run_monitor()
            print("[ENGINE] Monitor stopped; restarting shortly.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[ENGINE ERROR] {type(exc).__name__}: {exc}")
        await asyncio.sleep(MONITOR_RETRY_SECONDS)


async def _sleep_until_retry(label: str, seconds: int):
    print(f"[HUNTER] {label}. Retrying in {seconds} seconds.")
    await asyncio.sleep(seconds)


async def hunter_supervisor():
    """Supervise the Hunter without rapid retries during control/DB failures."""
    while True:
        try:
            print("[HUNTER] Starting Rebate Hunter.")
            result = await run_rebate_hunter()

            if isinstance(result, str) and result.startswith("DAILY_LIMIT_SLEEP:"):
                try:
                    seconds = int(result.split(":", 1)[1])
                    if seconds <= 0:
                        seconds = 1
                except (TypeError, ValueError):
                    print("[HUNTER WARNING] Malformed daily-limit sleep; using fallback.")
                    seconds = HUNTER_DAILY_LIMIT_FALLBACK_SECONDS

                print(f"[HUNTER] Daily allowance exhausted. Sleeping {seconds}s until UTC reset.")
                await asyncio.sleep(seconds)

            elif result in ("HALTED_MANUAL_INTERVENTION", "HALTED_PARTIAL_SWAP"):
                await _sleep_until_retry(
                    "Persistent manual-intervention lock is active; review it in the dashboard",
                    HUNTER_MANUAL_REVIEW_RETRY_SECONDS,
                )

            elif result in ("HALTED_CONTROL_STATE", "CONTROL_STATE_UNAVAILABLE"):
                await _sleep_until_retry(
                    "Hunter control state is unavailable; trading remains stopped",
                    HUNTER_CONTROL_RETRY_SECONDS,
                )

            elif result == "NO_RULES":
                await _sleep_until_retry("No active rules found", 60)

            elif result is True:
                # Retained for compatibility with the previous Hunter return contract.
                print("[HUNTER WARNING] Hunter returned True. Using the legacy safety delay.")
                await asyncio.sleep(HUNTER_DAILY_LIMIT_FALLBACK_SECONDS)

            else:
                await _sleep_until_retry(
                    f"Hunter exited with status {result!r}",
                    HUNTER_RETRY_SECONDS,
                )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[HUNTER ERROR] {type(exc).__name__}: {exc}")
            await asyncio.sleep(HUNTER_CONTROL_RETRY_SECONDS)


async def _wait_for_api_start(server: uvicorn.Server) -> bool:
    """Wait for Uvicorn readiness instead of relying on a fixed two-second delay."""
    deadline = time.monotonic() + API_STARTUP_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        if server.started:
            return True
        if server.should_exit:
            return False
        await asyncio.sleep(0.1)

    return server.started


async def main():
    # Database initialisation must succeed before background services start.
    init_db()

    print(f"[BOOT] PID={os.getpid()} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("[SAFETY] Starting API first; monitor and Hunter wait for API readiness.")

    server_config = uvicorn.Config(
        app=app,
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
        workers=1,
    )
    server = uvicorn.Server(server_config)

    server_task = asyncio.create_task(server.serve(), name="fastapi-server")

    monitor_task = None
    hunter_task = None

    try:
        if not await _wait_for_api_start(server):
            raise RuntimeError(
                f"API did not become ready within {API_STARTUP_TIMEOUT_SECONDS} seconds."
            )

        print(f"[API] Ready at http://{HOST}:{PORT}")
        print("[ENGINE] Starting Cantex monitoring engine.")
        monitor_task = asyncio.create_task(
            monitor_supervisor(), name="cantex-monitor"
        )

        print("[HUNTER] Starting Rebate Hunter supervisor.")
        hunter_task = asyncio.create_task(
            hunter_supervisor(), name="rebate-hunter"
        )

        await server_task

    finally:
        for task in (monitor_task, hunter_task):
            if task is not None:
                task.cancel()

        for task in (monitor_task, hunter_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if not server_task.done():
            server.should_exit = True
            try:
                await server_task
            except asyncio.CancelledError:
                pass

        print("[SERVER] API, monitor, and Hunter shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user.")
