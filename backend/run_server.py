"""Start the FastAPI server and Cantex monitor together."""

import asyncio

import uvicorn

from api import app
from database import init_db
from monitor import run_monitor


HOST = "127.0.0.1"
PORT = 8000
MONITOR_RETRY_SECONDS = 5


async def monitor_supervisor():
    """Restart the monitoring engine if it exits unexpectedly."""
    while True:
        try:
            print("[ENGINE] Starting Cantex monitoring engine.")
            await run_monitor()

            # run_monitor normally runs continuously. If it returns,
            # restart it after a short delay.
            print("[ENGINE] Monitor stopped; restarting shortly.")

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            print(
                f"[ENGINE ERROR] {type(exc).__name__}: {exc}"
            )

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

    monitor_task = asyncio.create_task(
        monitor_supervisor(),
        name="cantex-monitor-supervisor",
    )

    print(f"[API] Starting at http://{HOST}:{PORT}")
    print(f"[API] Documentation: http://{HOST}:{PORT}/docs")
    print("[SAFETY] Quote-only mode; no swaps will be submitted.")

    try:
        await server.serve()

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