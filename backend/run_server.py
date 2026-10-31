"""Start the FastAPI server and Cantex monitor together."""

import asyncio
import os

import uvicorn

from api import app
# --- CHANGED: Points to new Postgres file ---
from postgres_database import init_db
from monitor import run_monitor


HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", 8000))

MONITOR_RETRY_SECONDS = 5


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
    print("[SAFETY] Quote-only mode; no swaps will be submitted.")

    # Start the API server FIRST so Railway's health check passes immediately
    server_task = asyncio.create_task(server.serve())

    # Give the server a moment to bind to the port
    await asyncio.sleep(2)

    # Now start the monitor in the background
    monitor_task = asyncio.create_task(
        monitor_supervisor(),
        name="cantex-monitor-supervisor",
    )

    try:
        await server_task
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        print("[SERVER] API and monitor shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user.")