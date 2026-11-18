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
HUNTER_DAILY_LIMIT_SLEEP = 3600  # fallback only; real duration comes from the hunter


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


async def hunter_supervisor():
    while True:
        try:
            print("[HUNTER] Starting Rebate Hunter.")
            result = await run_rebate_hunter()

            # CHANGE 1: dynamic daily-limit sleep.
            # The hunter computes "seconds until UTC midnight" and returns it here.
            # Without this branch, the hunter would fall to the else and retry every 10s,
            # hammering the DB all night for zero work.
            if isinstance(result, str) and result.startswith("DAILY_LIMIT_SLEEP:"):
                try:
                    seconds = int(result.split(":", 1)[1])
                except ValueError:
                    print("[HUNTER WARNING] Malformed DAILY_LIMIT_SLEEP, falling back to 3600s.")
                    seconds = HUNTER_DAILY_LIMIT_SLEEP
                print(f"[HUNTER] Daily limit reached. Sleeping {seconds}s until UTC midnight.")
                await asyncio.sleep(seconds)

            # CHANGE 2: partial-swap halt.
            # The hunter saw an unverifiable swap and refuses to keep trading.
            # Without this branch, "return False" would retry every 10s and re-halt,
            # producing a stop/restart thrash loop all day.
            elif result == "HALTED_PARTIAL_SWAP":
                print("[HUNTER] Partial swap detected. Sleeping 1 hour for manual review.")
                await asyncio.sleep(3600)

            # CHANGE 3: legacy True fallback (kept for deploy-order safety).
            # If rebate_hunter.py hasn't been updated yet, it still returns True.
            # We log loudly so the mismatch is visible instead of silently retrying hourly.
            elif result is True:
                print("[HUNTER WARNING] Hunter returned True, not DAILY_LIMIT_SLEEP. "
                      "Is rebate_hunter.py updated? Falling back to 3600s sleep.")
                await asyncio.sleep(HUNTER_DAILY_LIMIT_SLEEP)

            elif result == "NO_RULES":
                print("[HUNTER] No rules found. Checking again in 60 seconds.")
                await asyncio.sleep(60)

            else:
                print("[HUNTER] Hunter stopped unexpectedly. Retrying in 10 seconds.")
                await asyncio.sleep(HUNTER_RETRY_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[HUNTER ERROR] {type(exc).__name__}: {exc}")
            await asyncio.sleep(HUNTER_RETRY_SECONDS)


async def main():
    init_db()

    # CHANGE 4: boot fingerprint.
    # One [BOOT] line per process. If you see two different PIDs in the log within
    # a minute, Railway is running two containers — that's the double-forward cause.
    # If you see one PID, the double-forward is a code bug inside rebate_hunter.py.
    print(f"[BOOT] PID={os.getpid()} at {time.strftime('%H:%M:%S')}")

    server_config = uvicorn.Config(
        app=app,
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
        workers=1,
    )

    server = uvicorn.Server(server_config)

    print(f"[API] Starting at http://{HOST}:{PORT}")
    print(f"[API] Documentation: http://{HOST}:{PORT}/docs")
    print("[SAFETY] Lean Mode: API and Monitor running. Hunter executing swaps.")

    # Start the API server FIRST so Railway's health check passes
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(2)

    # Start the monitor (live prices only)
    monitor_task = asyncio.create_task(monitor_supervisor(), name="cantex-monitor")

    # Start the rebate hunter (strict ping-pong)
    hunter_task = asyncio.create_task(hunter_supervisor(), name="rebate-hunter")

    try:
        await server_task
    finally:
        monitor_task.cancel()
        hunter_task.cancel()
        for task in (monitor_task, hunter_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        print("[SERVER] API, monitor, and hunter shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user.")