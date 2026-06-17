from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from iteris.agents.execute import launch_execute_agent
from iteris.agents.explore import launch_explore_agent
from iteris.agents.runtime import create_agent_run
from iteris.cli import app
import iteris.commands.theorem as theorem_command
import iteris.commands.workflow as workflow_command
import iteris.tools.arxiv as arxiv_tool
from iteris.commands.context import build_context
from iteris.commands.goal import build_goal_finalize_report, latest_goal_logs
from iteris.commands.goal import _matching_verifier_processes
from iteris.commands.workflow import _run_state_from_text
from iteris.gitops import checkpoint, init_git, status
from iteris.memory.facts import rebuild_fact_index, write_fact
from iteris.project import init_project, write_json
from iteris.bootstrap import run_once


SOURCE = r"""
\begin{problem}
Prove a sharper stability estimate for a bounded linear operator using its documented structure.
\end{problem}
"""


def test_new_defaults_to_current_directory_and_help_command(tmp_path, monkeypatch):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    init = CliRunner().invoke(app, ["new", "--source", str(source)])

    assert init.exit_code == 0, init.output
    assert (project / "iteris.toml").exists()
    assert (project / "sources" / "problem.tex").exists()
    assert (project / "references" / "README.md").exists()
    assert (project / ".git").exists()

    help_result = CliRunner().invoke(app, ["help"])
    assert help_result.exit_code == 0, help_result.output
    assert "Iteris workflow" in help_result.output
    assert "iteris new --source /path/to/problem.tex" in help_result.output
    assert "iteris run" in help_result.output


def test_new_json_output_is_machine_readable(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    result = CliRunner().invoke(app, ["new", str(project), "--source", str(source), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"] == "sources/problem.tex"
    assert payload["target_artifact"] == "results/project/answer.md"
    assert payload["git"]["dirty"] is False
    assert payload["checkpoint"]["committed"] is True
    assert (project / ".git").exists()


def test_public_commands_explain_missing_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    for command in [["run", "--print"], ["status"], ["review", "--no-bundle"], ["stop"]]:
        result = CliRunner().invoke(app, command)
        output = strip_ansi(result.output)
        assert result.exit_code != 0, command
        assert "not an Iteris project" in output
        assert "new" in output
        assert "--source" in output
        assert "Traceback" not in output


def test_new_allow_non_empty_leaves_existing_files_uncommitted(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "secret.txt").write_text("do not commit me\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["new", str(project), "--source", str(source), "--allow-non-empty", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preexisting_files"] == ["secret.txt"]
    assert payload["preexisting_files_committed"] is False
    assert payload["git"]["dirty"] is True
    tracked = subprocess.run(["git", "ls-files"], cwd=project, text=True, stdout=subprocess.PIPE, check=True).stdout.splitlines()
    assert "secret.txt" not in tracked


def test_new_existing_project_is_noop_before_source_validation(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    first = CliRunner().invoke(app, ["new", str(project), "--source", str(source), "--json"])
    assert first.exit_code == 0, first.output

    second = CliRunner().invoke(app, ["new", str(project), "--source", str(tmp_path / "missing.tex"), "--json"])

    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["already_exists"] is True
    assert payload["source"] == "sources/problem.tex"


def test_attach_reports_tmux_failure_without_traceback(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)

    monkeypatch.setattr(workflow_command, "_session_exists", lambda session_name: True)

    def fail_attach(session_name: str) -> None:
        raise RuntimeError("failed to switch tmux client")

    monkeypatch.setattr(workflow_command, "attach_tmux_session", fail_attach)

    result = CliRunner().invoke(app, ["attach", str(project)])

    assert result.exit_code == 1
    assert "failed to switch tmux client" in result.output
    assert "Traceback" not in result.output


def test_run_new_session_prints_unique_session_names(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)

    first = CliRunner().invoke(app, ["run", str(project), "--print", "--new-session", "--json"])
    second = CliRunner().invoke(app, ["run", str(project), "--print", "--new-session", "--json"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.output)["session_name"] != json.loads(second.output)["session_name"]


def test_goal_tmux_print_only_writes_prompt_file_and_stays_concise(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    result = CliRunner().invoke(app, ["tool", "goal", "tmux", str(project), "--goal", "Solve the source problem end-to-end."])

    assert result.exit_code == 0, result.output
    assert "Prompt file: .iteris/goal_prompt.txt" in result.output
    assert "Read `.iteris/goal_prompt.txt`" in result.output
    assert "Terminal artifact path:" not in result.output
    prompt = (project / ".iteris" / "goal_prompt.txt").read_text(encoding="utf-8")
    assert "Terminal artifact path: `results/project/answer.md`" in prompt


def test_public_run_print_status_and_review_are_project_first(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)

    run = CliRunner().invoke(app, ["run", str(project), "--print", "--json"])
    assert run.exit_code == 0, run.output
    payload = json.loads(run.output)
    assert payload["target_artifact"] == "results/project/answer.md"
    assert payload["prompt_file"] == ".iteris/goal_prompt.txt"
    prompt = (project / ".iteris" / "goal_prompt.txt").read_text(encoding="utf-8")
    assert "/goal Solve the source problem" in prompt
    assert "iteris tool context . --json" in prompt

    status_result = CliRunner().invoke(app, ["status", str(project), "--json"])
    assert status_result.exit_code == 0, status_result.output
    assert json.loads(status_result.output)["target_artifact"] == "results/project/answer.md"


def test_codex_run_does_not_reference_claude_binary(tmp_path):
    # A codex-executor run must not probe/launch the claude binary; a
    # claude-executor run must. Lock the executor gating in both directions.
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)

    codex_run = CliRunner().invoke(
        app, ["run", str(project), "--print", "--json", "--executor", "codex"]
    )
    assert codex_run.exit_code == 0, codex_run.output
    codex_payload = json.loads(codex_run.output)
    assert "claude" not in codex_payload.get("command", "").lower()
    assert "codex" in codex_payload["command"].lower()
    assert "error" not in codex_payload

    claude_run = CliRunner().invoke(
        app, ["run", str(project), "--print", "--json", "--executor", "claude"]
    )
    assert claude_run.exit_code == 0, claude_run.output
    assert "claude" in json.loads(claude_run.output)["command"].lower()

    review = CliRunner().invoke(app, ["review", str(project), "--no-bundle", "--json"])
    assert review.exit_code == 0, review.output
    assert "STATUS.md" in json.loads(review.output)["review_files"]


def test_run_rebakes_verification_executor_into_subtree(tmp_path, monkeypatch):
    # tmux-server-env gotcha (one level down): an independently-chosen verification executor
    # must survive this loop's tmux hop, or leaf verifiers fall back to the main
    # executor. The launch command must carry ITERIS_VERIFICATION_EXECUTOR so a
    # cross-model setup (solve claude / verify codex) reaches the verifiers.
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)

    monkeypatch.setenv("ITERIS_VERIFICATION_EXECUTOR", "codex")
    out = CliRunner().invoke(
        app, ["run", str(project), "--print", "--json", "--executor", "claude"]
    )
    assert out.exit_code == 0, out.output
    command = json.loads(out.output)["command"]
    assert "ITERIS_EXECUTOR=claude" in command
    assert "ITERIS_VERIFICATION_EXECUTOR=codex" in command


def test_status_and_review_prefer_recorded_target_artifact(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    recorded_target = "results/problem/answer_verified.md"
    (project / "STATUS.md").write_text(f"phase: verified\ntarget_artifact: {recorded_target}\n", encoding="utf-8")

    status_result = CliRunner().invoke(app, ["status", str(project), "--json"])
    review_result = CliRunner().invoke(app, ["review", str(project), "--no-bundle", "--json"])

    assert status_result.exit_code == 0, status_result.output
    assert review_result.exit_code == 0, review_result.output
    assert json.loads(status_result.output)["target_artifact"] == recorded_target
    assert json.loads(review_result.output)["target_artifact"] == recorded_target


def test_status_target_prefers_current_run_over_status(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    (project / "STATUS.md").write_text("phase: verified\ntarget_artifact: results/status/answer_verified.md\n", encoding="utf-8")
    write_json(
        project / ".iteris" / "current_run.json",
        {
            "schema_version": "iteris.current_run.v0",
            "session_name": "iteris-project",
            "target_artifact": "results/current/answer_verified.md",
        },
    )

    status_result = CliRunner().invoke(app, ["status", str(project), "--json"])

    assert status_result.exit_code == 0, status_result.output
    payload = json.loads(status_result.output)
    assert payload["target_artifact"] == "results/current/answer_verified.md"
    assert payload["target"] == "results/current/answer_verified.md"


def test_completed_pane_text_is_not_active_run_state():
    assert _run_state_from_text("Goal achieved (11m)") == "achieved"
    assert _run_state_from_text("Do you trust the contents of this directory?\n› 1. Yes, continue") == "waiting_for_codex_trust"
    assert _run_state_from_text("Pursuing goal (4m)") == "running"


def test_context_summarizes_agent_entrypoint(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)

    result = build_context(project, query="operator stability", limit=3)

    assert result["source_file"] == "sources/problem.tex"
    assert result["facts_ok"] is True
    assert result["fact_count"] == 1
    assert result["frontier_summary"]["active_frontiers"] == 0
    # Bootstrap intake completes inline, so its umbrella task is closed and no
    # legacy open task remains after run_once.
    assert result["open_tasks"] == []
    assert result["search_results"]
    assert "iteris tool context . --json" in result["recommended_commands"]
    assert "iteris tool frontier refresh . --json" in result["recommended_commands"]


def test_frontier_refresh_and_set_active(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    write_fact(
        project,
        fact_id="fact:project:verified-route:20260604",
        source_task="task-old-route",
        claim_summary="A completed route is verified.",
        statement="A completed route is verified.",
        status="verified",
        review_level="verified",
    )
    rebuild_fact_index(project)

    empty_refresh = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert empty_refresh.exit_code == 0, empty_refresh.output
    refreshed_facts = json.loads(empty_refresh.output)
    assert any(
        "fact:project:verified-route:20260604" in item.get("fact_ids", [])
        for item in refreshed_facts["active_frontiers"]
    )

    add = CliRunner().invoke(
        app,
        [
            "tool",
            "task",
            "pool",
            "add",
            str(project),
            "--task-id",
            "task-proof-frontier",
            "--mode",
            "proof",
            "--objective",
            "Prove the frontier lemma.",
            "--json",
        ],
    )
    assert add.exit_code == 0, add.output

    refresh = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert refresh.exit_code == 0, refresh.output
    payload = json.loads(refresh.output)
    assert any("task-proof-frontier" in item.get("task_ids", []) for item in payload["active_frontiers"])

    health = CliRunner().invoke(app, ["tool", "frontier", "health", str(project), "--json"])
    assert health.exit_code == 0, health.output
    assert json.loads(health.output)["explore_recommended"] is False

    set_active = CliRunner().invoke(
        app,
        [
            "tool",
            "frontier",
            "set-active",
            str(project),
            "--frontier-id",
            "manual-frontier",
            "--title",
            "Manual frontier",
            "--summary",
            "Track one explicit route.",
            "--task",
            "task-proof-frontier",
            "--gap",
            "needs verification",
            "--json",
        ],
    )
    assert set_active.exit_code == 0, set_active.output
    updated = json.loads(set_active.output)
    assert any(item.get("frontier_id") == "manual-frontier" for item in updated["active_frontiers"])

    validation = CliRunner().invoke(app, ["tool", "frontier", "validate", str(project), "--json"])
    assert validation.exit_code == 0, validation.output
    assert json.loads(validation.output)["ok"] is True


def test_frontier_health_recommends_explore_for_repeated_blockers(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    for index in range(3):
        write_fact(
            project,
            fact_id=f"fact:project:same-route-blocker-{index}",
            source_task=f"task-same-route-proof-{index}",
            claim_summary=f"Same route blocker {index}: missing selector obstruction.",
            statement="The current route has a blocker that prevents completion.",
            status="verified",
            review_level="verified",
        )
    rebuild_fact_index(project)

    refresh = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert refresh.exit_code == 0, refresh.output

    health = CliRunner().invoke(app, ["tool", "frontier", "health", str(project), "--json"])
    assert health.exit_code == 0, health.output
    payload = json.loads(health.output)
    assert payload["explore_recommended"] is True
    assert "blocker" in payload["reason"]
    assert payload["recommended_focus"]


def test_frontier_refresh_attributes_escape_explore_to_source_frontier(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    for index in range(3):
        write_fact(
            project,
            fact_id=f"fact:project:s2-fixed-chart-blocker-{index}",
            source_task=f"task-s2-fixed-chart-proof-{index}",
            claim_summary=f"S2 Fixed Chart blocker {index}: missing selector obstruction.",
            statement="The current route has a blocker that prevents completion.",
            status="verified",
            review_level="verified",
        )
    rebuild_fact_index(project)
    refresh = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert refresh.exit_code == 0, refresh.output
    first = json.loads(refresh.output)
    s2 = next(item for item in first["active_frontiers"] if item["frontier_id"] == "auto-s2-fixed-chart")
    assert s2["health"]["explore_recommended"] is True

    index = project / "artifacts" / "ARTIFACT_INDEX.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "iteris.artifact_index_record.v0",
                    "record_type": "workspace_created",
                    "created_at": "2026-06-04T15:11:24Z",
                    "run_id": "explore-test",
                    "role": "explore",
                    "focus": "Escape or reassess S2 Fixed Chart after verified blockers.",
                    "artifact_workspace": "artifacts/route_checks/s2/explore-test",
                    "artifact_manifest": "artifacts/route_checks/s2/explore-test/artifact_manifest.json",
                }
            )
            + "\n"
        )

    second = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    s2 = next(item for item in payload["active_frontiers"] if item["frontier_id"] == "auto-s2-fixed-chart")
    assert s2["explore_run_count"] == 1
    assert s2["last_explore_at"] == "2026-06-04T15:11:24Z"
    assert not any(item["frontier_id"] == "auto-escape-or-reassess" for item in payload["active_frontiers"])


def test_frontier_health_recommends_global_explore_when_many_routes_are_blocked(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    for index in range(12):
        write_fact(
            project,
            fact_id=f"fact:project:alpha{index}-blocker",
            source_task=f"task-alpha{index}-proof",
            claim_summary=f"Alpha{index} lane blocker: missing certificate.",
            statement="This route is blocked.",
            status="verified",
            review_level="verified",
        )
    rebuild_fact_index(project)
    refresh = CliRunner().invoke(app, ["tool", "frontier", "refresh", str(project), "--json"])
    assert refresh.exit_code == 0, refresh.output

    health = CliRunner().invoke(app, ["tool", "frontier", "health", str(project), "--json"])
    assert health.exit_code == 0, health.output
    payload = json.loads(health.output)
    assert payload["explore_recommended"] is True
    assert payload["reason"] == "many blocked frontiers and no ready or running work"
    assert payload["recommended_focus"].startswith("Run a global explore")

    add = CliRunner().invoke(
        app,
        [
            "tool",
            "task",
            "pool",
            "add",
            str(project),
            "--task-id",
            "task-active-repair",
            "--mode",
            "proof",
            "--objective",
            "Continue an active repair before global exploration.",
            "--json",
        ],
    )
    assert add.exit_code == 0, add.output
    suppressed = CliRunner().invoke(app, ["tool", "frontier", "health", str(project), "--json"])
    assert suppressed.exit_code == 0, suppressed.output
    assert json.loads(suppressed.output)["explore_recommended"] is False


def test_memory_search_accepts_positional_query_from_project(tmp_path, monkeypatch):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(app, ["tool", "memory", "search", "operator", "stability", "--json"])

    assert result.exit_code == 0, result.output
    assert "bounded linear operator" in result.output


def test_task_pool_cli_add_select_update_and_validate(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    add = CliRunner().invoke(
        app,
        [
            "tool",
            "task",
            "pool",
            "add",
            str(project),
            "--task-id",
            "task-proof-001",
            "--mode",
            "proof",
            "--objective",
            "Prove the first durable lemma.",
            "--priority",
            "10",
            "--json",
        ],
    )
    assert add.exit_code == 0, add.output
    added = json.loads(add.output)
    assert added["task_id"] == "task-proof-001"
    assert added["mode"] == "proof"

    select = CliRunner().invoke(app, ["tool", "task", "pool", "select-ready", str(project), "--mode", "proof", "--json"])
    assert select.exit_code == 0, select.output
    ready = json.loads(select.output)
    assert [task["task_id"] for task in ready] == ["task-proof-001"]

    update = CliRunner().invoke(
        app,
        ["tool", "task", "pool", "update", str(project), "--task-id", "task-proof-001", "--status", "review", "--json"],
    )
    assert update.exit_code == 0, update.output
    assert json.loads(update.output)["status"] == "review"

    completed_alias = CliRunner().invoke(
        app,
        ["tool", "task", "pool", "update", str(project), "--task-id", "task-proof-001", "--status", "completed", "--json"],
    )
    assert completed_alias.exit_code == 0, completed_alias.output
    assert json.loads(completed_alias.output)["status"] == "done"

    validation = CliRunner().invoke(app, ["tool", "task", "pool", "validate", str(project), "--json"])
    assert validation.exit_code == 0, validation.output
    assert json.loads(validation.output)["ok"] is True
    assert (project / ".iteris" / "logs" / "events.jsonl").exists()


def test_task_pool_parallel_cli_adds_preserve_all_tasks(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    # The subprocess runs `python -m iteris.cli`, which only imports iteris when
    # iteris is pip-installed OR on PYTHONPATH. pytest makes iteris importable
    # in-process (pyproject pythonpath), but that injection does not propagate
    # to child processes — so derive the package's parent dir from the already
    # imported module and pass it through, making the test independent of how
    # iteris was installed.
    import iteris

    pkg_parent = str(Path(iteris.__file__).resolve().parents[1])
    child_env = {**os.environ}
    child_env["PYTHONPATH"] = os.pathsep.join(
        p for p in (pkg_parent, os.environ.get("PYTHONPATH", "")) if p
    )

    def add_task(index: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "iteris.cli",
                "tool",
                "task",
                "pool",
                "add",
                str(project),
                "--task-id",
                f"task-parallel-{index}",
                "--mode",
                "algorithm",
                "--objective",
                f"Parallel task {index}",
                "--json",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=child_env,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(add_task, range(12)))

    assert all(result.returncode == 0 for result in results), [result.stderr or result.stdout for result in results]
    pool = json.loads((project / "tasks" / "TASK_POOL.json").read_text(encoding="utf-8"))
    task_ids = {task["task_id"] for task in pool["tasks"]}
    assert {f"task-parallel-{index}" for index in range(12)} <= task_ids


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_goal_finalize_requires_passed_target_verifications_and_clean_git(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    target = "results/problem-001/answer_verified.md"
    (project / target).parent.mkdir(parents=True, exist_ok=True)
    (project / target).write_text("# Verified answer\n", encoding="utf-8")
    write_json(
        project / "verification" / "results" / "verify-assembly.json",
        {
            "request_id": "verify-assembly",
            "mode": "assembly",
            "passed": True,
            "target_artifact": target,
        },
    )
    write_json(
        project / "verification" / "results" / "verify-goal.json",
        {
            "request_id": "verify-goal",
            "mode": "goal_success",
            "passed": True,
            "target_artifact": target,
        },
    )
    init_git(project)
    checkpoint(project, message="checkpoint: verified final state")

    report = build_goal_finalize_report(project, target_artifact=target)
    assert report["ok"] is True
    assert report["assembly_request_id"] == "verify-assembly"
    assert report["goal_success_request_id"] == "verify-goal"

    result = CliRunner().invoke(app, ["tool", "goal", "finalize", str(project), "--target-artifact", target, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def _write_passed_verifications(project: Path, target: str) -> None:
    for name, mode in (("verify-assembly", "assembly"), ("verify-goal", "goal_success")):
        write_json(
            project / "verification" / "results" / f"{name}.json",
            {"request_id": name, "mode": mode, "passed": True, "target_artifact": target},
        )


def test_finalize_emits_verified_copy_only_when_goal_success_passes(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    target = "results/problem-001/answer.md"
    (project / target).parent.mkdir(parents=True, exist_ok=True)
    (project / target).write_text("# Answer body\n", encoding="utf-8")
    _write_passed_verifications(project, target)

    result = CliRunner().invoke(
        app,
        ["tool", "goal", "finalize", str(project), "--target-artifact", target, "--no-require-clean", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["verified_artifact"] == "results/problem-001/answer_verified.md"

    verified = project / "results" / "problem-001" / "answer_verified.md"
    assert verified.exists()
    body = verified.read_text(encoding="utf-8")
    assert body.startswith("<!-- ITERIS VERIFIED: goal_success verify-goal")
    assert "# Answer body" in body

    status = json.loads((project / "results" / "problem-001" / "VERIFICATION_STATUS.json").read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["verified_artifact"] == "results/problem-001/answer_verified.md"


def test_finalize_does_not_emit_verified_copy_when_goal_success_missing(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    target = "results/problem-001/answer.md"
    (project / target).parent.mkdir(parents=True, exist_ok=True)
    (project / target).write_text("# Answer body\n", encoding="utf-8")
    # Only assembly passes; goal_success is absent -> gate fails.
    write_json(
        project / "verification" / "results" / "verify-assembly.json",
        {"request_id": "verify-assembly", "mode": "assembly", "passed": True, "target_artifact": target},
    )

    result = CliRunner().invoke(
        app,
        ["tool", "goal", "finalize", str(project), "--target-artifact", target, "--no-require-clean", "--json"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False

    assert not (project / "results" / "problem-001" / "answer_verified.md").exists()
    status = json.loads((project / "results" / "problem-001" / "VERIFICATION_STATUS.json").read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert status["verified_artifact"] is None


def test_logs_bundle_copies_raw_pane_log_and_clean_transcript(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    logs_dir = project / ".iteris" / "logs"
    pane_log = logs_dir / "goal-iteris-test-20260603T000000000000Z.pane.log"
    meta = logs_dir / "goal-iteris-test-20260603T000000000000Z.meta.json"
    pane_log.write_text("\x1b[31mGoal achieved\x1b[0m\n", encoding="utf-8")
    write_json(
        meta,
        {
            "schema_version": "iteris.goal_launch.v0",
            "session_name": "iteris-test",
            "pane_log": str(pane_log.relative_to(project)),
        },
    )

    result = CliRunner().invoke(app, ["tool", "logs", "bundle", str(project), "--session", "iteris-test", "--json"])
    assert result.exit_code == 0, result.output
    manifest = json.loads(result.output)
    bundle_dir = project / Path(manifest["manifest_path"]).parent
    assert (bundle_dir / "pane.raw.log").exists()
    transcript = (bundle_dir / "pane.transcript.txt").read_text(encoding="utf-8")
    assert "Goal achieved" in transcript
    assert "\x1b" not in transcript
    assert any(item["kind"] == "pane_log_raw" for item in manifest["copied_files"])


def test_latest_goal_logs_uses_meta_pane_log_pair(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    logs_dir = project / ".iteris" / "logs"
    old_pane = logs_dir / "goal-iteris-test-20260603T000000000000Z.pane.log"
    old_meta = logs_dir / "goal-iteris-test-20260603T000000000000Z.meta.json"
    new_pane = logs_dir / "goal-iteris-test-20260603T000100000000Z.pane.log"
    new_meta = logs_dir / "goal-iteris-test-20260603T000100000000Z.meta.json"
    old_pane.write_text("old\n", encoding="utf-8")
    old_meta.write_text(json.dumps({"pane_log": str(old_pane.relative_to(project))}), encoding="utf-8")
    new_pane.write_text("new\n", encoding="utf-8")
    new_meta.write_text(json.dumps({"pane_log": str(new_pane.relative_to(project))}), encoding="utf-8")
    old_pane.touch()

    logs = latest_goal_logs(project, "iteris-test")

    assert logs["meta"] == str(new_meta)
    assert logs["pane_log"] == str(new_pane)


def test_review_reports_missing_session_logs(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    result = CliRunner().invoke(app, ["review", str(project), "--session", "missing-session", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "no pane log found for session missing-session" in payload["bundle"]["warnings"]
    assert "no launch metadata found for session missing-session" in payload["bundle"]["warnings"]


def test_agent_dry_run_writes_observable_prompt_and_run_state(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    CliRunner().invoke(
        app,
        [
            "tool",
            "task",
            "pool",
            "add",
            str(project),
            "--task-id",
            "task-proof-001",
            "--mode",
            "proof",
            "--objective",
            "Prove the first durable lemma.",
            "--json",
        ],
    )

    execute = CliRunner().invoke(
        app,
        ["tool", "agent", "execute", str(project), "--task-id", "task-proof-001", "--dry-run", "--json"],
    )
    assert execute.exit_code == 0, execute.output
    payload = json.loads(execute.output)
    assert payload["role"] == "execute"
    assert payload["mode"] == "proof"
    assert payload["status"] == "dry_run"
    assert payload["artifact_workspace"].startswith("artifacts/proofs/task-proof-001/")
    assert payload["artifact_manifest"].endswith("/artifact_manifest.json")
    request = json.loads((project / payload["request_path"]).read_text(encoding="utf-8"))
    assert request["artifact_index"] == "artifacts/ARTIFACT_INDEX.jsonl"
    assert request["artifact_workspace"] == payload["artifact_workspace"]
    assert "proof_report" in request["recommended_artifacts"]
    manifest = json.loads((project / payload["artifact_manifest"]).read_text(encoding="utf-8"))
    assert manifest["run_id"] == payload["run_id"]
    assert manifest["mode"] == "proof"
    assert "artifact_workspace" in manifest
    index_lines = (project / "artifacts" / "ARTIFACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["record_type"] == "workspace_created" for line in index_lines if line.strip())
    prompt = (project / payload["prompt_path"]).read_text(encoding="utf-8")
    assert "background execution tool" in prompt
    assert "Execution mode: `proof`" in prompt
    assert "wait at least another 180 seconds before polling again" in prompt
    assert "Canonical artifact workspace" in prompt
    assert "artifacts/ARTIFACT_INDEX.jsonl" in prompt
    assert "output.json" in prompt

    inspect = CliRunner().invoke(app, ["tool", "agent", "inspect", str(project), "--run-id", payload["run_id"], "--json"])
    assert inspect.exit_code == 0, inspect.output
    inspected = json.loads(inspect.output)
    assert inspected["status"]["status"] == "dry_run"

    explore = CliRunner().invoke(app, ["tool", "agent", "explore", str(project), "--focus", "Find a non-obvious route.", "--dry-run", "--json"])
    assert explore.exit_code == 0, explore.output
    explore_payload = json.loads(explore.output)
    explore_prompt = (project / explore_payload["prompt_path"]).read_text(encoding="utf-8")
    assert "non-obvious insight" in explore_prompt
    assert "Escape common literature defaults" in explore_prompt
    assert "problem essence" in explore_prompt
    assert "higher-level view" in explore_prompt
    assert "background tool" in explore_prompt
    assert "wait at least another 180 seconds before polling again" in explore_prompt
    assert "Canonical artifact workspace" in explore_prompt
    assert explore_payload["artifact_workspace"].startswith("artifacts/route_checks/")


def test_experiment_agent_dry_run_includes_discovery_artifacts_and_workflow(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    add_task = CliRunner().invoke(
        app,
        [
            "tool",
            "task",
            "pool",
            "add",
            str(project),
            "--task-id",
            "task-experiment-001",
            "--mode",
            "experiment",
            "--objective",
            "Search stress instances for the current route.",
            "--json",
        ],
    )
    assert add_task.exit_code == 0, add_task.output

    execute = CliRunner().invoke(
        app,
        ["tool", "agent", "execute", str(project), "--task-id", "task-experiment-001", "--dry-run", "--json"],
    )
    assert execute.exit_code == 0, execute.output
    payload = json.loads(execute.output)
    assert payload["mode"] == "experiment"
    request = json.loads((project / payload["request_path"]).read_text(encoding="utf-8"))
    recommended = request["recommended_artifacts"]
    assert recommended["hypotheses"].endswith("/hypotheses.jsonl")
    assert recommended["instances"].endswith("/instances.jsonl")
    assert recommended["best_cases"].endswith("/best_cases.json")
    assert recommended["failure_cases"].endswith("/failure_cases.json")
    assert recommended["feature_report"].endswith("/feature_analysis.md")
    assert recommended["exactification_plan"].endswith("/exactification_plan.md")
    prompt = (project / payload["prompt_path"]).read_text(encoding="utf-8")
    assert "Experiment workflow" in prompt
    assert "baseline, random, adversarial, and known-obstruction instances" in prompt
    assert "floating_only, reproduced_numeric, rationalized, sturm_checked, cad_ready, or verified_fact" in prompt


def test_execute_agent_indexes_mode_workspace_outputs(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text(
        """#!/bin/sh
python - <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["ITERIS_PROJECT_ROOT"])
run_id = os.environ["ITERIS_AGENT_RUN_ID"]
run_dir = root / "artifacts" / "agent_runs" / run_id
request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
workspace = root / request["artifact_workspace"]
workspace.mkdir(parents=True, exist_ok=True)
proof = workspace / "proof.md"
proof.write_text("# Proof\\n\\n## Proof\\n\\nA small checked proof artifact.\\n", encoding="utf-8")
(run_dir / "output.md").write_text("# Agent output\\n\\nCreated a proof artifact.\\n", encoding="utf-8")
(run_dir / "output.json").write_text(json.dumps({
    "schema_version": "iteris.agent_output.v0",
    "role": "execute",
    "run_id": run_id,
    "mode": request["mode"],
    "task_id": request["task_id"],
    "summary": "Created a proof artifact.",
    "status_recommendation": "done",
    "created_artifacts": [str(proof.relative_to(root))],
    "artifact_manifest": request["artifact_manifest"],
    "updated_shared_files": [],
    "candidate_facts": [],
    "verification_requests": [],
    "blockers": [],
    "task_pool_updates": [],
    "next_actions": []
}, indent=2) + "\\n", encoding="utf-8")
print(json.dumps({"timestamp":"2026-06-05T00:00:00Z","type":"session_meta","payload":{"id":"00000000-0000-0000-0000-000000000001","cwd":str(root),"cli_version":"fake","source":"exec"}}))
print(json.dumps({"timestamp":"2026-06-05T00:00:01Z","type":"event_msg","payload":{"type":"agent_message","message":"fake agent completed"}}))
print("fake stderr diagnostic", file=sys.stderr)
PY
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = launch_execute_agent(
        project,
        task={"task_id": "task-proof-002", "mode": "proof", "objective": "Prove another lemma."},
        mode="proof",
        executable=str(fake_codex),
        model="fake",
        reasoning_effort="low",
    )

    assert result["status"] == "completed"
    assert result["artifact_workspace"].startswith("artifacts/proofs/task-proof-002/")
    manifest = json.loads((project / result["artifact_manifest"]).read_text(encoding="utf-8"))
    assert manifest["agent_status"] == "completed"
    assert manifest["agent_output_summary"] == "Created a proof artifact."
    assert manifest["created_artifacts"] == [f"{result['artifact_workspace']}/proof.md"]
    assert (project / manifest["created_artifacts"][0]).exists()
    run_dir = project / result["agent_run_dir"]
    request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
    assert "--json" in request["codex_command"]
    assert (run_dir / "codex.events.jsonl").exists()
    assert (run_dir / "codex.stderr.log").exists()
    assert "fake stderr diagnostic" not in (run_dir / "codex.events.jsonl").read_text(encoding="utf-8")
    assert "fake stderr diagnostic" in (run_dir / "codex.stderr.log").read_text(encoding="utf-8")
    assert "fake agent completed" in (run_dir / "codex.log").read_text(encoding="utf-8")
    assert "fake stderr diagnostic" in (run_dir / "codex.log").read_text(encoding="utf-8")
    log_manifest = json.loads((run_dir / "log_manifest.json").read_text(encoding="utf-8"))
    assert log_manifest["session_id"] == "00000000-0000-0000-0000-000000000001"
    assert log_manifest["events_sha256"]
    assert log_manifest["stderr_sha256"]
    assert (project / ".iteris" / "logs" / "CODEX_RUN_INDEX.jsonl").exists()
    records = [
        json.loads(line)
        for line in (project / "artifacts" / "ARTIFACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [record["record_type"] for record in records] == ["workspace_created", "agent_output_indexed"]
    assert records[-1]["created_artifacts"] == manifest["created_artifacts"]

    bundle = CliRunner().invoke(app, ["tool", "logs", "bundle", str(project), "--json"])
    assert bundle.exit_code == 0, bundle.output
    bundle_payload = json.loads(bundle.output)
    copied_kinds = {item["kind"] for item in bundle_payload["copied_files"]}
    assert "codex_run_index" in copied_kinds
    assert "codex_exec_events" in copied_kinds
    assert "codex_exec_stderr" in copied_kinds
    assert "codex_exec_manifest" in copied_kinds


def test_agent_timeout_with_required_outputs_is_usable(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake_codex = tmp_path / "fake-codex-timeout"
    fake_codex.write_text(
        """#!/bin/sh
python - <<'PY'
import json
import os
import time
from pathlib import Path

root = Path(os.environ["ITERIS_PROJECT_ROOT"])
run_id = os.environ["ITERIS_AGENT_RUN_ID"]
run_dir = root / "artifacts" / "agent_runs" / run_id
request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
(run_dir / "output.md").write_text("# Agent output\\n\\nFinished before timeout.\\n", encoding="utf-8")
(run_dir / "output.json").write_text(json.dumps({
    "schema_version": "iteris.agent_output.v0",
    "role": request["role"],
    "run_id": run_id,
    "summary": "Finished before timeout.",
    "created_artifacts": [],
    "updated_shared_files": [],
    "candidate_facts": [],
    "verification_requests": [],
    "task_pool_updates": [],
    "frontier_updates": [],
    "next_actions": []
}, indent=2) + "\\n", encoding="utf-8")
time.sleep(5)
PY
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    result = create_agent_run(
        project,
        role="explore",
        focus="timeout regression",
        prompt_builder=lambda request: "Write outputs and continue.",
        executable=str(fake_codex),
        model="fake",
        reasoning_effort="low",
        timeout_seconds=1,
    )

    assert result["status"] == "completed"
    status_payload = json.loads((project / result["status_path"]).read_text(encoding="utf-8"))
    assert status_payload["timed_out"] is True
    assert (project / result["codex_events"]).exists()
    assert (project / result["codex_stderr"]).exists()
    assert (project / result["codex_log_manifest"]).exists()
    manifest = json.loads((project / result["artifact_manifest"]).read_text(encoding="utf-8"))
    assert manifest["agent_status"] == "completed"
    assert manifest["agent_output_summary"] == "Finished before timeout."


def test_artifact_gate_requires_markdown_and_scripts_to_be_indexed(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    workspace = project / "artifacts" / "proofs" / "task-proof-003" / "run-manual"
    workspace.mkdir(parents=True)
    (workspace / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "iteris.artifact_manifest.v0",
                "run_id": "run-manual",
                "role": "execute",
                "mode": "proof",
                "task_id": "task-proof-003",
                "agent_run_dir": "artifacts/agent_runs/run-manual",
                "artifact_workspace": "artifacts/proofs/task-proof-003/run-manual",
                "created_artifacts": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "proof.md").write_text("# Proof\n", encoding="utf-8")

    gate = CliRunner().invoke(app, ["tool", "artifact", "gate", str(project), "--json"])

    assert gate.exit_code == 1
    payload = json.loads(gate.output)
    assert any("document artifact is not listed" in item["issue"] for item in payload["errors"])
    assert ".md" in payload["required_index_suffixes"]


def test_agent_run_requires_structured_outputs(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fake_codex = tmp_path / "fake-codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    result = launch_explore_agent(
        project,
        focus="Run a fake agent.",
        executable=str(fake_codex),
        model="fake",
        reasoning_effort="low",
    )
    assert result["status"] == "failed"
    status_payload = json.loads((project / result["status_path"]).read_text(encoding="utf-8"))
    assert "without required output" in status_payload["error"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_init_and_checkpoint_leave_clean_repo(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)

    init_result = init_git(project)
    assert init_result["repo"] is True
    assert (project / ".gitignore").exists()
    assert ".iteris/current_run.json" in (project / ".gitignore").read_text(encoding="utf-8")

    result = checkpoint(project, message="checkpoint: initialize project")

    assert result["committed"] is True
    repo_status = status(project)
    assert repo_status["repo"] is True
    assert repo_status["dirty"] is False

    cli_status = CliRunner().invoke(app, ["tool", "git", "status", str(project)])
    assert cli_status.exit_code == 0, cli_status.output
    assert "working tree clean" in cli_status.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_checkpoint_excludes_current_run_state(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    init_git(project)
    write_json(
        project / ".iteris" / "current_run.json",
        {
            "schema_version": "iteris.current_run.v0",
            "session_name": "iteris-project",
            "target_artifact": "results/project/answer_verified.md",
        },
    )

    result = checkpoint(project, message="checkpoint: ignore transient current run")

    assert result["committed"] is True
    tracked = subprocess.run(
        ["git", "ls-files", ".iteris/current_run.json"],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    assert tracked == ""
    assert status(project)["dirty"] is False


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_memory_validate_is_read_only_by_default_after_checkpoint(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)
    init_git(project)
    checkpoint(project, message="checkpoint: initialize project")

    result = CliRunner().invoke(app, ["tool", "memory", "validate", str(project), "--json"])

    assert result.exit_code == 0, result.output
    payload = result.output
    assert '"ok": true' in payload
    assert '"rebuilt": 0' in payload
    assert status(project)["dirty"] is False


def test_memory_add_fact_creates_durable_fact_and_index(tmp_path):
    source = tmp_path / "problem.tex"
    source.write_text(SOURCE, encoding="utf-8")
    project = tmp_path / "project"
    init_project(project, source=source)
    run_once(project)

    result = CliRunner().invoke(
        app,
        [
            "tool",
            "memory",
            "add-fact",
            str(project),
            "--source-task",
            "task-example",
            "--claim-summary",
            "The structural lemma is source-derived.",
            "--statement",
            "The operator satisfies the structural lemma stated in the source.",
            "--fact-type",
            "structural_lemma",
            "--claim-policy",
            "source_derived",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "source-derived" in result.output
    assert len(list((project / "memory" / "facts").glob("fact-*.md"))) == 2
    assert sum(1 for _ in (project / "memory" / "facts" / "FACT_INDEX.jsonl").open(encoding="utf-8")) == 2


def test_memory_add_fact_defaults_to_stable_claim_not_route_state(tmp_path):
    project = tmp_path / "project"
    init_project(project)

    result = CliRunner().invoke(
        app,
        [
            "tool",
            "memory",
            "add-fact",
            str(project),
            "--source-task",
            "task-example",
            "--claim-summary",
            "The source assumption is fixed.",
            "--statement",
            "The source assumption remains part of the stated problem.",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    fact_text = (project / payload["path"]).read_text(encoding="utf-8")
    assert "fact_type: claim" in fact_text
    assert "claim_policy: stable_claim" in fact_text
    assert "route_state" not in fact_text
    assert "planning_hint" not in fact_text


def test_memory_promote_fact_requires_passed_matching_verification(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    fact_id = "fact:project:promote-me"
    add_result = CliRunner().invoke(
        app,
        [
            "tool",
            "memory",
            "add-fact",
            str(project),
            "--source-task",
            "task-example",
            "--fact-id",
            fact_id,
            "--claim-summary",
            "A promotable fact.",
            "--statement",
            "This fact is structurally ready for verification.",
            "--json",
        ],
    )
    assert add_result.exit_code == 0, add_result.output
    fact_path = json.loads(add_result.output)["path"]

    verify_result = CliRunner().invoke(
        app,
        [
            "tool",
            "verify",
            "submit",
            str(project),
            "--backend",
            "structural",
            "--mode",
            "fact",
            "--claim",
            "Verify promotable fact.",
            "--artifact",
            fact_path,
            "--json",
        ],
    )
    assert verify_result.exit_code == 0, verify_result.output
    request_id = json.loads(verify_result.output)["request_id"]

    promote_result = CliRunner().invoke(
        app,
        [
            "tool",
            "memory",
            "promote-fact",
            str(project),
            "--fact-id",
            fact_id,
            "--verification",
            request_id,
            "--json",
        ],
    )
    assert promote_result.exit_code == 0, promote_result.output
    fact_text = (project / fact_path).read_text(encoding="utf-8")
    assert "status: verified" in fact_text
    assert f"verification: {request_id}" in fact_text

    rows = [
        json.loads(line)
        for line in (project / "memory" / "facts" / "FACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row = next(item for item in rows if item["fact_id"] == fact_id)
    assert row["status"] == "verified"
    assert row["verification"] == request_id


def test_theorem_search_saves_project_local_reference(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)

    def fake_search(query: str, num_results: int = 10, timeout_seconds: int = 30):
        return {
            "query": query,
            "count": 1,
            "endpoint": "https://leansearch.net/thm/search",
            "results": [
                {
                    "title": "A useful theorem",
                    "theorem": "If the assumptions hold, the conclusion follows.",
                    "arxiv_id": "0000.00000",
                    "theorem_id": "thm:test",
                }
            ],
        }

    monkeypatch.setattr(theorem_command, "search_arxiv_theorems", fake_search)
    result = CliRunner().invoke(
        app,
        [
            "tool",
            "theorem",
            "search",
            str(project),
            "--query",
            "useful theorem",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["saved_path"].startswith("artifacts/references/theorem_search/query-")
    assert payload["cached"] is False
    assert (project / payload["saved_path"]).exists()


def test_theorem_fetch_prefers_arxiv_source(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)
    source_bytes = _tar_bytes({"paper.tex": "\\documentclass{article}\\begin{document}Theorem text.\\end{document}"})
    calls: list[str] = []

    def fake_get(url: str, timeout: int = 60):
        calls.append(url)
        return _FakeResponse(200, source_bytes)

    monkeypatch.setattr(arxiv_tool.requests, "get", fake_get)
    result = CliRunner().invoke(
        app,
        [
            "tool",
            "theorem",
            "fetch",
            str(project),
            "--arxiv-id",
            "https://arxiv.org/abs/1234.56789",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["arxiv_id"] == "1234.56789"
    assert payload["source"]["ok"] is True
    assert payload["pdf"] is None
    assert calls == ["https://arxiv.org/e-print/1234.56789"]
    assert (project / payload["manifest_path"]).exists()
    saved_manifest = json.loads((project / payload["manifest_path"]).read_text(encoding="utf-8"))
    assert saved_manifest["manifest_path"] == payload["manifest_path"]
    assert any(path.endswith("paper.tex") for path in payload["primary_paths"])


def test_theorem_fetch_falls_back_to_pdf_text(tmp_path, monkeypatch):
    project = tmp_path / "project"
    init_project(project)
    calls: list[str] = []

    def fake_get(url: str, timeout: int = 60):
        calls.append(url)
        if "/e-print/" in url:
            return _FakeResponse(404, b"not found")
        return _FakeResponse(200, b"%PDF-1.7\nfake pdf")

    def fake_extract_pdf_text(path):
        assert path.name == "paper.pdf"
        return "parsed pdf text\n"

    monkeypatch.setattr(arxiv_tool.requests, "get", fake_get)
    monkeypatch.setattr(arxiv_tool, "extract_pdf_text", fake_extract_pdf_text)
    result = CliRunner().invoke(
        app,
        [
            "tool",
            "theorem",
            "fetch",
            str(project),
            "--arxiv-id",
            "1234.56789",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["source"]["ok"] is False
    assert payload["pdf"]["ok"] is True
    assert calls == ["https://arxiv.org/e-print/1234.56789", "https://arxiv.org/pdf/1234.56789.pdf"]
    assert (project / payload["pdf"]["text_path"]).read_text(encoding="utf-8") == "parsed pdf text\n"


def test_verify_finalize_adopts_completed_agent_run(tmp_path):
    project = tmp_path / "project"
    init_project(project)
    request_id = "verify-test-finalize"
    run_dir = project / "verification" / "agent_runs" / request_id
    run_dir.mkdir(parents=True)
    request = {
        "schema_version": "iteris.verification_request.v0",
        "request_id": request_id,
        "backend": "agent",
        "mode": "fact",
        "claim": "PROJECT.md exists.",
        "artifacts": ["PROJECT.md"],
        "fact_ids": [],
        "target_artifact": None,
        "created_at": "2026-06-03T00:00:00Z",
    }
    verification = {
        "verification_report": {
            "summary": "PROJECT.md exists and supports the scoped claim.",
            "critical_errors": [],
            "gaps": [],
        },
        "verdict": "correct",
        "repair_hints": "No repair needed.",
        "checked_artifacts": ["PROJECT.md"],
        "checked_fact_ids": [],
    }
    (run_dir / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (run_dir / "verification.json").write_text(json.dumps(verification), encoding="utf-8")
    (run_dir / "codex.log").write_text("completed\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["tool", "verify", "finalize", str(project), "--request-id", request_id, "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["request_id"] == request_id
    assert payload["passed"] is True
    assert (project / "verification" / "results" / f"{request_id}.json").exists()

    second = CliRunner().invoke(app, ["tool", "verify", "finalize", str(project), "--request-id", request_id, "--json"])
    assert second.exit_code == 0, second.output
    index_lines = [
        line
        for line in (project / "verification" / "VERIFICATION_INDEX.jsonl").read_text(encoding="utf-8").splitlines()
        if request_id in line
    ]
    assert len(index_lines) == 1


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content


def _tar_bytes(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_goal_stop_matches_project_verification_agents(tmp_path):
    project = tmp_path / "project"
    other = tmp_path / "other"
    ps_output = "\n".join(
        [
            f"101 1 node /bin/codex exec -C {project} You are the Iteris Verification Agent.",
            f"102 1 node /bin/codex exec -C {other} You are the Iteris Verification Agent.",
            f"103 1 node /bin/codex --cd {project} /goal something",
            f"104 1 node /bin/codex exec -C {project} -m gpt-5.5 --dangerously-bypass-approvals-and-sandbox -",
            f"105 1 node /bin/codex exec -C {project} -m gpt-5.5 -",
        ]
    )

    envs = {
        104: "ITERIS_PROCESS_ROLE=verification_agent\n",
        105: "ITERIS_PROCESS_ROLE=subagent_execute\n",
    }
    matches = _matching_verifier_processes(ps_output, str(project), env_by_pid=lambda pid: envs.get(pid, ""))

    assert [item["pid"] for item in matches] == [101, 104]


def test_finalize_principled_stop_emits_reduced_copy(tmp_path):
    # A certified principled stop finalizes to answer_reduced_verified.md
    # (distinct from a solved answer_verified.md), gated on a passed principled_stop
    # verification instead of goal_success.
    project = tmp_path / "project"
    init_project(project)
    target = "results/problem-001/answer.md"
    (project / target).parent.mkdir(parents=True, exist_ok=True)
    (project / target).write_text("# Strongest valid result + verified obstruction\n", encoding="utf-8")
    for name, mode in (("verify-assembly", "assembly"), ("verify-pstop", "principled_stop")):
        write_json(
            project / "verification" / "results" / f"{name}.json",
            {"request_id": name, "mode": mode, "passed": True, "target_artifact": target},
        )
    result = CliRunner().invoke(
        app,
        ["tool", "goal", "finalize", str(project), "--target-artifact", target, "--principled-stop", "--no-require-clean", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["terminal_mode"] == "principled_stop"
    assert payload["verified_artifact"] == "results/problem-001/answer_reduced_verified.md"
    assert (project / "results/problem-001/answer_reduced_verified.md").exists()
    # the solved-only signal must NOT be created by a principled stop
    assert not (project / "results/problem-001/answer_verified.md").exists()
    body = (project / "results/problem-001/answer_reduced_verified.md").read_text(encoding="utf-8")
    assert "ITERIS PRINCIPLED-STOP CERTIFIED" in body


def test_finalize_principled_stop_requires_principled_stop_verification(tmp_path):
    # A passed goal_success does NOT satisfy --principled-stop: the two terminals
    # are distinct and must each be gated on their own verification.
    project = tmp_path / "project"
    init_project(project)
    target = "results/problem-001/answer.md"
    (project / target).parent.mkdir(parents=True, exist_ok=True)
    (project / target).write_text("# Answer body\n", encoding="utf-8")
    _write_passed_verifications(project, target)  # assembly + goal_success only
    result = CliRunner().invoke(
        app,
        ["tool", "goal", "finalize", str(project), "--target-artifact", target, "--principled-stop", "--no-require-clean", "--json"],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    names = {c["name"]: c["ok"] for c in payload["checks"]}
    assert names.get("principled_stop_verification_passed") is False
    assert not (project / "results/problem-001/answer_reduced_verified.md").exists()
