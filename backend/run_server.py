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
from postgres_database import (
    clear_user_hunter_resume_intent,
    get_user_hunter_control_state,
    get_user_hunter_resume_intents,
    init_db,
    set_user_hunter_resume_intent,
)
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
        # Users who reach the daily target remain eligible for automatic
        # resumption on the next UTC date, unless they explicitly stop.
        self._daily_exhausted: set[str] = set()
        self._daily_exhausted_on: dict[str, str] = {}
        self._auto_resume_users: set[str] = set()
        self._explicitly_stopped: set[str] = set()
        self._resume_retry_after: dict[str, float] = {}

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

            # A requested/queued Hunter must survive a process restart. A NULL
            # exhausted_date means it is intended to be active, not waiting for
            # the next UTC reset.
            await asyncio.to_thread(set_user_hunter_resume_intent, user_id, None)
            self._explicitly_stopped.discard(user_id)
            self._auto_resume_users.add(user_id)
            self._daily_exhausted.discard(user_id)
            self._daily_exhausted_on.pop(user_id, None)
            self._resume_retry_after.pop(user_id, None)

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
                # Preserve the durable intent, but avoid a rapid restart loop
                # when credentials/network/runtime setup is temporarily failing.
                self._resume_retry_after[user_id] = (
                    time.monotonic() + CONTROL_STATE_RETRY_SECONDS
                )
                print(
                    f"[HUNTER ERROR] User {user_id}: "
                    f"{type(exc).__name__}: {exc}; retry is delayed."
                )
                return

            if isinstance(outcome, str) and outcome.startswith("DAILY_LIMIT_SLEEP:"):
                # Persist the exhausted UTC date. The intent survives a Railway
                # restart and is eligible only after the DB date rolls forward.
                self._auto_resume_users.add(user_id)
                self._daily_exhausted.add(user_id)
                try:
                    control_state = await asyncio.to_thread(
                        get_user_hunter_control_state, user_id
                    )
                    exhausted_date = str(control_state.get("daily_date") or "")
                    await asyncio.to_thread(
                        set_user_hunter_resume_intent,
                        user_id,
                        exhausted_date or None,
                    )
                    if exhausted_date:
                        self._daily_exhausted_on[user_id] = exhausted_date
                except Exception as exc:
                    # Keep the persistent active intent if the exhaustion write
                    # fails. The supervisor checks the DB allowance before restart.
                    print(
                        f"[SLOTS] Could not persist daily exhaustion for {user_id}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                print(f"[SLOTS] User {user_id} exhausted today's allowance.")
            elif outcome in ("HALTED_MANUAL_INTERVENTION", "NO_RULES"):
                await asyncio.to_thread(clear_user_hunter_resume_intent, user_id)
                self._auto_resume_users.discard(user_id)
                self._daily_exhausted.discard(user_id)
                self._daily_exhausted_on.pop(user_id, None)
                print(f"[SLOTS] User {user_id} ended Hunter with status {outcome}.")
            elif outcome == "HALTED_CONTROL_STATE":
                self._resume_retry_after[user_id] = (
                    time.monotonic() + CONTROL_STATE_RETRY_SECONDS
                )
                print(
                    f"[SLOTS] User {user_id} could not read control state; "
                    "releasing this runtime. The persisted intent will be retried."
                )
            elif outcome is False or outcome is None:
                await asyncio.to_thread(clear_user_hunter_resume_intent, user_id)
                self._auto_resume_users.discard(user_id)
                print(
                    f"[SLOTS] User {user_id} run_user exited unexpectedly "
                    f"with {outcome!r}; releasing the runtime."
                )
            else:
                self._resume_retry_after[user_id] = (
                    time.monotonic() + CONTROL_STATE_RETRY_SECONDS
                )
                print(
                    f"[SLOTS] User {user_id} ended with status {outcome!r}; "
                    "releasing the runtime. A retry is delayed."
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
        """Explicitly stop/dequeue a Hunter and permanently clear its resume intent."""
        user_id = str(user_id)
        async with self._lock:
            self._explicitly_stopped.add(user_id)
            self._auto_resume_users.discard(user_id)
            self._daily_exhausted.discard(user_id)
            self._daily_exhausted_on.pop(user_id, None)
            self._resume_retry_after.pop(user_id, None)
            was_waiting = user_id in self.waiting
            if was_waiting:
                self.waiting.remove(user_id)
            slot = self.active.get(user_id)
            task = slot.task if slot is not None else None
            if task is not None:
                task.cancel()

        # Clear durable intent even when the user is already exhausted and has
        # no active task. This is what prevents a restart from reviving a stop.
        await asyncio.to_thread(clear_user_hunter_resume_intent, user_id)

        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

        # A task cancelled before its coroutine gets its first event-loop turn
        # cannot execute _run_slot's finally block. Reconcile the slot here too.
        async with self._lock:
            current = self.active.get(user_id)
            if current is not None and current.task is task:
                self.active.pop(user_id, None)
                current.runtime.private_ws_task = None
            if promote:
                await self._promote_waiting_locked()
        return bool(task is not None or was_waiting)

    async def clear_daily_exhaustion(self, user_id: str) -> None:
        """Allow an explicit new request after the user's daily reset."""
        async with self._lock:
            user_id = str(user_id)
            self._daily_exhausted.discard(user_id)
            self._daily_exhausted_on.pop(user_id, None)

    async def resume_after_daily_reset(self) -> None:
        """Restore persisted intents and resume only after a safe UTC rollover."""
        intents = await asyncio.to_thread(get_user_hunter_resume_intents)

        async with self._lock:
            for intent in intents:
                user_id = str(intent["user_id"])
                if user_id in self._explicitly_stopped:
                    continue
                self._auto_resume_users.add(user_id)
                exhausted_date = intent.get("exhausted_date")
                if exhausted_date:
                    self._daily_exhausted.add(user_id)
                    self._daily_exhausted_on[user_id] = str(exhausted_date)
                else:
                    self._daily_exhausted.discard(user_id)
                    self._daily_exhausted_on.pop(user_id, None)

            candidates = [
                user_id for user_id in self._auto_resume_users
                if user_id not in self.active
                and user_id not in self.waiting
                and user_id not in self._explicitly_stopped
                and time.monotonic() >= self._resume_retry_after.get(user_id, 0.0)
            ]

        for user_id in candidates:
            try:
                state = await asyncio.to_thread(
                    get_user_hunter_control_state, user_id
                )
                current_date = str(state.get("daily_date") or "")
                remaining = int(state.get("swaps_remaining", 0))
                exhausted_date = self._daily_exhausted_on.get(user_id)

                # A persistent manual lock always wins over an old resume intent.
                if state.get("manual_intervention_required"):
                    await asyncio.to_thread(
                        clear_user_hunter_resume_intent, user_id
                    )
                    async with self._lock:
                        self._auto_resume_users.discard(user_id)
                        self._daily_exhausted.discard(user_id)
                        self._daily_exhausted_on.pop(user_id, None)
                    print(
                        f"[SLOTS] Auto-resume withheld for {user_id}: "
                        "manual intervention is required."
                    )
                    continue

                if not current_date:
                    continue

                if remaining <= 0:
                    # Covers a crash after the last successful swap but before
                    # the exhaustion date was written. Never restart on 0 quota.
                    if not exhausted_date:
                        await asyncio.to_thread(
                            set_user_hunter_resume_intent, user_id, current_date
                        )
                        async with self._lock:
                            self._daily_exhausted.add(user_id)
                            self._daily_exhausted_on[user_id] = current_date
                    continue

                # An exhausted Hunter waits for the DB's UTC date to advance.
                if exhausted_date and current_date <= exhausted_date:
                    continue

                async with self._lock:
                    if (
                        user_id in self._explicitly_stopped
                        or user_id in self.active
                        or user_id in self.waiting
                    ):
                        continue
                    self._daily_exhausted.discard(user_id)
                    self._daily_exhausted_on.pop(user_id, None)

                result = await self.request_slot(user_id)
                print(
                    f"[SLOTS] Daily reset/restart recovery for {user_id}: {result}."
                )
                async with self._lock:
                    self._resume_retry_after.pop(user_id, None)
            except Exception as exc:
                async with self._lock:
                    self._resume_retry_after[user_id] = (
                        time.monotonic() + CONTROL_STATE_RETRY_SECONDS
                    )
                print(
                    f"[SLOTS] Resume recovery failed for {user_id}: "
                    f"{type(exc).__name__}: {exc}; will retry."
                )

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


async def daily_reset_supervisor() -> None:
    """Check UTC allowance rollover and resume eligible Hunters."""
    while True:
        try:
            await slot_manager.resume_after_daily_reset()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[DAILY RESET ERROR] {type(exc).__name__}: {exc}")
        await asyncio.sleep(LOOP_RETRY_SECONDS)


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
    daily_reset_task: Optional[asyncio.Task] = None

    try:
        if not await wait_for_api_start(server):
            raise RuntimeError(
                f"API did not become ready within {API_STARTUP_TIMEOUT_SECONDS} seconds."
            )

        print(f"[API] Ready at http://{HOST}:{PORT}")
        monitor_task = asyncio.create_task(
            monitor_supervisor(), name="multiuser-market-monitor"
        )
        daily_reset_task = asyncio.create_task(
            daily_reset_supervisor(), name="daily-reset-supervisor"
        )
        await server_task

    finally:
        # Stop Hunter tasks first so each run_user finally block closes its
        # private WebSocket before the shared services are shut down.
        await slot_manager.shutdown()

        if daily_reset_task is not None:
            daily_reset_task.cancel()
            await asyncio.gather(daily_reset_task, return_exceptions=True)

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
