"""Start the FastAPI server, Cantex monitor, and Rebate Hunter together."""

import asyncio
import os

import uvicorn

from api import app
from postgres_database import init_db
from monitor import run_monitor
from rebate_hunter import main as run_rebate_hunter

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 8000))

MONITOR_RETRY_SECONDS = 5
HUNTER_RETRY_SECONDS = 10
HUNTER_DAILY_LIMIT_SLEEP = 3600  # 1 hour


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
            
            if result is True:
                print(f"[HUNTER] Daily limit reached. Sleeping for 1 hour.")
                await asyncio.sleep(3600)
            elif result == "NO_RULES":
                print(f"[HUNTER] No rules found. Checking again in 60 seconds.")
                await asyncio.sleep(60)  # 60 seconds, not 5 minutes
            else:
                print(f"[HUNTER] Hunter stopped unexpectedly. Retrying in 10 seconds.")
                await asyncio.sleep(10)
                
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[HUNTER ERROR] {type(exc).__name__}: {exc}")
            await asyncio.sleep(10)
            


async def main():
    init_db()

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