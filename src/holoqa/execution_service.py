from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .browser_runner import CancelledBrowserRun, execute_cases
from .runs import RunStore


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="holoqa-runner")


def start_background_run(plan_id: str, state_database: str, base_url: str) -> dict[str, Any]:
    store = RunStore(Path(state_database))
    run = store.queue(plan_id)
    if run.status == "BLOCKED":
        return run.model_dump()
    _executor.submit(_execute, run.run_id, plan_id, Path(state_database), base_url)
    return run.model_dump()


def _execute(run_id: str, plan_id: str, database: Path, base_url: str) -> None:
    store = RunStore(database)
    try:
        plan = store.get_plan(plan_id)
        store.mark_running(run_id)

        def on_event(**event: Any) -> None:
            store.progress(
                run_id,
                event_type=event.get("event_type", "run_event"),
                test_id=event.get("test_id"),
                step=event.get("step"),
                status=event.get("status"),
                message=event.get("message", ""),
                completed_cases=event.get("completed_cases"),
                payload=event.get("payload"),
            )

        results = execute_cases(
            plan.cases,
            base_url,
            database.parent / "artifacts" / run_id,
            on_event=on_event,
            cancel_check=lambda: store.cancellation_requested(run_id),
        )
        status = "PASS" if results and all(result["status"] == "PASS" for result in results) else "FAIL"
        store.complete(run_id, status, f"Executed {len(results)} test case(s)", results)
    except CancelledBrowserRun as error:
        store.complete(run_id, "BLOCKED", str(error), [])
    except Exception as error:
        store.complete(run_id, "FAIL", f"Worker error: {error}", [])


def cancel_run(run_id: str, state_database: str) -> dict[str, Any]:
    store = RunStore(Path(state_database))
    store.request_cancel(run_id)
    return store.get(run_id).model_dump()
