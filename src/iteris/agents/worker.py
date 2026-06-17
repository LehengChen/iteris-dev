"""Detached agent worker entrypoint."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from iteris.agents.runtime import run_agent_codex, write_status
from iteris.events import record_event
from iteris.project import now_iso, read_json
from iteris.tasks import update_pool_task


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m iteris.agents.worker <run-dir>", file=sys.stderr)
        return 2
    run_dir = Path(args[0]).resolve()
    request = read_json(run_dir / "request.json", default={})
    project_root = Path(str(request.get("project_path", run_dir.parent))).resolve() if isinstance(request, dict) else run_dir.parent
    try:
        result = run_agent_codex(run_dir)
        if isinstance(request, dict) and request.get("role") == "execute" and request.get("task_id"):
            update_pool_task(
                project_root,
                str(request["task_id"]),
                status="review" if result["status"] == "completed" else "blocked",
                assigned_agent_run=run_dir.name,
            )
        record_event(
            project_root,
            "agent_run_completed" if result["status"] == "completed" else "agent_run_failed",
            {
                "run_id": run_dir.name,
                "role": request.get("role") if isinstance(request, dict) else None,
                "mode": request.get("mode") if isinstance(request, dict) else None,
                "task_id": request.get("task_id") if isinstance(request, dict) else None,
                "returncode": result["returncode"],
                "status": result["status"],
            },
        )
        return 0 if result["status"] == "completed" else 1
    except Exception as exc:
        write_status(run_dir, {"status": "failed", "error": str(exc), "updated_at": now_iso()})
        if isinstance(request, dict) and request.get("role") == "execute" and request.get("task_id"):
            try:
                update_pool_task(project_root, str(request["task_id"]), status="blocked", assigned_agent_run=run_dir.name)
            except Exception:
                pass
        record_event(project_root, "agent_run_failed", {"run_id": run_dir.name, "error": str(exc)})
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
