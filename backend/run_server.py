"""Multi-user Cantex server orchestration.

Starts one FastAPI server and one shared public market monitor.
Hunter execution is managed by a five-slot, FIFO SlotManager. The API can
request/release slots through app.state.slot_manager once its endpoints are
wired to this interface.

A per-user runtime factory must be registered before a user can acquire a
slot. It must create a fresh UserRuntime and SDK client from that user's
securely stored credentials. This module deliberately does not read shared
trading keys from environment variables.
"""

import asyncio
import inspect
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import uvicorn

from api import app
from monitor_multiuser import run_monitor
from postgres_database import init_db
from rebate_hunter import UserRuntime, run_user


HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))

MAX_ACTIVE_HUNTERS = 5
MONITOR_RETRY_SECONDS = 5
API_STARTUP_TIMEOUT_SECONDS = 30
CONTROL_STATE_RETRY_SECONDS = 60
LOOP_RETRY_SECONDS = 10


RuntimeFactory = Callable[[str], Awaitable[UserRuntime] | UserRuntime]


@dataclass
class HunterSlot:
    user_id: str
    task: asyncio.Task
    runtime: UserRuntime


class SlotManager:
    """Owns up to five Hunter tasks and promotes waiting users FIFO."""

    def __init__(
        self,
        runtime_factory: Optional[RuntimeFactory] = None,
        max_slots: int = MAX_ACTIVE_HUNTERS,
    ):
        if max_slots < 1:
            raise ValueError("max_slots must be at least 1")
        self.max_slots = min(int(max_slots), MAX_ACTIVE_HUNTERS)
        self.runtime_factory = runtime_factory
        self.active: dict[str, HunterSlot] = {}
        self.waiting: list[str] = []
        self._lock = asyncio.Lock()
        self._closing = False
        # Users who exhausted today's allowance are not automatically
        # re-enqueued by this manager. They must request a slot again.
        self._daily_exhausted: set[str] = set()

    def set_runtime_factory(self, factory: RuntimeFactory) -> None:
        """Register the secure per-user SDK/runtime factory."""
        self.runtime_factory = factory

    async def request_slot(self, user_id: str) -> str:
        user_id = str(user_id)
        async with self._lock:
            if self._closing:
                raise RuntimeError("Hunter slot manager is shutting down")
            if user_id in self.active:
                return "active"
            if user_id in self.waiting:
                return "waiting"
            if self.runtime_factory is None:
                raise RuntimeError(
                    "Per-user runtime factory is not configured; no Hunter was started."
                )
            if len(self.active) < self.max_slots:
                await self._start_locked(user_id)
                return "started"
            self.waiting.append(user_id)
            print(
                f"[SLOTS] User {user_id} queued "
                f"(active={len(self.active)}/{self.max_slots}, waiting={len(self.waiting)})."
            )
            return "waiting"

    async def _make_runtime(self, user_id: str) -> UserRuntime:
        factory = self.runtime_factory
        if factory is None:
            raise RuntimeError("Per-user runtime factory is not configured")
        runtime = factory(user_id)
        if inspect.isawaitable(runtime):
            runtime = await runtime
        if not isinstance(runtime, UserRuntime):
            raise TypeError("Runtime factory must return a UserRuntime")
        if str(runtime.user_id) != user_id:
            raise ValueError("Runtime factory returned a runtime for the wrong user")
        if runtime.client is None:
            raise ValueError("Runtime factory returned a runtime without an SDK client")
        return runtime

    async def _start_locked(self, user_id: str) -> None:
        # Called while _lock is held. Runtime construction is deliberately
        # completed before reserving a slot, so failed setup cannot leak one.
        runtime = await self._make_runtime(user_id)
        task = asyncio.create_task(
            self._run_slot(user_id, runtime),
            name=f"rebate-hunter-{user_id}",
        )
        self.active[user_id] = HunterSlot(
            user_id=user_id, task=task, runtime=runtime
        )
        print(
            f"[SLOTS] Started Hunter for {user_id} "
            f"(active={len(self.active)}/{self.max_slots})."
        )

    async def _run_slot(self, user_id: str, runtime: UserRuntime) -> None:
        promote = True
        try:
            try:
                # One run_user invocation per fresh runtime/slot acquisition.
                outcome = await run_user(runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"[HUNTER ERROR] User {user_id}: "
                    f"{type(exc).__name__}: {exc}"
                )
                return

            if isinstance(outcome, str) and outcome.startswith("DAILY_LIMIT_SLEEP:"):
                self._daily_exhausted.add(user_id)
                print(f"[SLOTS] User {user_id} exhausted today's allowance.")
            elif outcome in ("HALTED_MANUAL_INTERVENTION", "NO_RULES"):
                print(f"[SLOTS] User {user_id} ended Hunter with status {outcome}.")
            elif outcome == "HALTED_CONTROL_STATE":
                print(
                    f"[SLOTS] User {user_id} could not read control state; "
                    "releasing this runtime. A fresh slot request can retry."
                )
            elif outcome is False or outcome is None:
                print(
                    f"[SLOTS] User {user_id} run_user exited unexpectedly "
                    f"with {outcome!r}; releasing the runtime."
                )
            else:
                print(
                    f"[SLOTS] User {user_id} ended with status {outcome!r}; "
                    "releasing the runtime."
                )
        finally:
            # run_user's finally block closes its private event-reader task
            # before the runtime is discarded and another user is promoted.
            await self._finish_slot(user_id, runtime, promote=promote)

    async def _finish_slot(
        self, user_id: str, runtime: UserRuntime, *, promote: bool
    ) -> None:
        async with self._lock:
            slot = self.active.get(user_id)
            if slot is not None and slot.runtime is runtime:
                self.active.pop(user_id, None)
            # Do not keep runtime/client references after a slot ends.
            runtime.private_ws_task = None
            if promote and not self._closing:
                await self._promote_waiting_locked()
            print(
                f"[SLOTS] Released {user_id} "
                f"(active={len(self.active)}/{self.max_slots}, waiting={len(self.waiting)})."
            )

    async def _promote_waiting_locked(self) -> None:
        while (
            self.waiting
            and len(self.active) < self.max_slots
            and not self._closing
        ):
            next_user = self.waiting.pop(0)
            if next_user in self._daily_exhausted:
                continue
            try:
                await self._start_locked(next_user)
            except Exception as exc:
                print(
                    f"[SLOTS] Could not start queued user {next_user}: "
                    f"{type(exc).__name__}: {exc}"
                )
                # Skip this failed setup and continue to the next queued user.
                continue

    async def release_slot(self, user_id: str, *, promote: bool = True) -> bool:
        """Cancel a user's task, await its cleanup, discard runtime, optionally promote."""
        user_id = str(user_id)
        async with self._lock:
            slot = self.active.get(user_id)
            if slot is None:
                if user_id in self.waiting:
                    self.waiting.remove(user_id)
                    return True
                return False
            task = slot.task
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        if promote:
            async with self._lock:
                await self._promote_waiting_locked()
        return True

    async def clear_daily_exhaustion(self, user_id: str) -> None:
        """Allow an explicit new request after the user's daily reset."""
        async with self._lock:
            self._daily_exhausted.discard(str(user_id))

    async def shutdown(self) -> None:
        """Stop accepting requests and await all Hunter cleanup."""
        async with self._lock:
            self._closing = True
            self.waiting.clear()
            tasks = [slot.task for slot in self.active.values()]
            for task in tasks:
                task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        async with self._lock:
            self.active.clear()
            self.waiting.clear()
        print("[SLOTS] All Hunter tasks stopped and runtimes released.")

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_slots": self.max_slots,
            "active_count": len(self.active),
            "active_users": list(self.active.keys()),
            "waiting_count": len(self.waiting),
            "waiting_users": list(self.waiting),
        }


# One process-wide manager. API routes can access it as request.app.state.slot_manager.
slot_manager = SlotManager()
app.state.slot_manager = slot_manager


async def monitor_supervisor() -> None:
    """Run exactly one shared public market monitor; restart after failures."""
    while True:
        try:
            print("[ENGINE] Starting shared multi-user Cantex market monitor.")
            await run_monitor()
            print("[ENGINE] Shared market monitor stopped; restarting shortly.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[MONITOR ERROR] {type(exc).__name__}: {exc}")
        await asyncio.sleep(MONITOR_RETRY_SECONDS)


async def wait_for_api_start(server: uvicorn.Server) -> bool:
    deadline = time.monotonic() + API_STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if server.started:
            return True
        if server.should_exit:
            return False
        await asyncio.sleep(0.1)
    return server.started


async def main() -> None:
    # Validate the expected multi-user schema before starting network services.
    # This does not run migrations.
    init_db()

    print(f"[BOOT] PID={os.getpid()} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"[SLOTS] Five-slot manager ready "
        f"(maximum {slot_manager.max_slots} active Hunters)."
    )
    print(
        "[SAFETY] Hunter starts are accepted only after the API/application "
        "registers a secure per-user runtime factory."
    )
    print(
        "[WEBSOCKET] Each UserRuntime owns its private account WebSocket. "
        "Cantex's concurrent private-WebSocket limit is not yet verified."
    )

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
    monitor_task: Optional[asyncio.Task] = None

    try:
        if not await wait_for_api_start(server):
            raise RuntimeError(
                f"API did not become ready within {API_STARTUP_TIMEOUT_SECONDS} seconds."
            )

        print(f"[API] Ready at http://{HOST}:{PORT}")
        monitor_task = asyncio.create_task(
            monitor_supervisor(), name="multiuser-market-monitor"
        )
        await server_task

    finally:
        # Stop Hunter tasks first so each run_user finally block closes its
        # private WebSocket before the shared services are shut down.
        await slot_manager.shutdown()

        if monitor_task is not None:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)

        if not server_task.done():
            server.should_exit = True
            await asyncio.gather(server_task, return_exceptions=True)

        print("[SERVER] API, Hunters and shared market monitor shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Server stopped by user.")
