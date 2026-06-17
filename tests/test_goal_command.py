from __future__ import annotations

import re
from pathlib import Path

import iteris.commands.goal as goal_command
from iteris.commands.goal import (
    build_codex_command,
    build_goal_file_reference_prompt,
    build_goal_log_paths,
    build_goal_prompt,
    build_interrupt_command,
    build_kill_session_command,
    build_load_prompt_buffer_command,
    build_paste_prompt_buffer_command,
    build_pipe_pane_command,
    build_send_keys_command,
    build_shell_command,
    build_submit_prompt_command,
    build_tmux_command,
    build_tmux_shell_command,
    codex_prompt_ready,
    codex_project_trust_section,
    codex_trust_prompt_present,
    ensure_codex_project_trusted,
    resolve_goal_defaults,
    tmux_attach_command,
)


def test_goal_prompt_and_tmux_command_are_direct_launchable(tmp_path):
    prompt = build_goal_prompt(
        "Solve the source problem end-to-end.",
        target_artifact="results/problem-001/answer_verified.md",
        problem_id="problem-001",
    )
    root = tmp_path / "project"
    root.mkdir()

    codex_cmd = build_codex_command(root, prompt, yolo=True, no_alt_screen=True)
    assert codex_cmd[:4] == ["codex", "--yolo", "--no-alt-screen", "--cd"]
    assert codex_cmd[4] == str(root)
    assert codex_cmd[-1].startswith("/goal Solve the source problem")
    assert "iteris tool context . --json" in codex_cmd[-1]
    assert "Problem id: `problem-001`" in codex_cmd[-1]
    assert "Terminal artifact path: `results/problem-001/answer_verified.md`" in codex_cmd[-1]
    assert "passed: true" in codex_cmd[-1]
    assert "--mode assembly --claim <goal-summary> --target-artifact results/problem-001/answer_verified.md" in codex_cmd[-1]
    assert "--mode goal_success" in codex_cmd[-1]
    assert "iteris tool goal finalize" in codex_cmd[-1]
    assert "iteris tool task pool show" in codex_cmd[-1]
    assert "iteris tool agent execute" in codex_cmd[-1]
    assert "Do not treat a verified blocker" in codex_cmd[-1]
    assert "NEVER write your own `sleep`/`until`/`while" in codex_cmd[-1]
    assert "iteris tool verify wait" in codex_cmd[-1]
    assert "iteris tool git checkpoint" in codex_cmd[-1]
    assert "genuinely blocked" not in codex_cmd[-1]

    shell_cmd = build_shell_command(root, codex_cmd, prompt)
    assert "goal_prompt.txt" in shell_cmd
    assert "exec codex" in shell_cmd
    assert "--cd" in shell_cmd
    assert "/goal Solve the source problem" in shell_cmd

    tmux_cmd = build_tmux_command("iteris-test", shell_cmd, detached=True)
    assert tmux_cmd[:4] == ["tmux", "new-session", "-d", "-s"]
    assert tmux_cmd[4] == "iteris-test"


def test_goal_command_can_disable_yolo_and_alt_screen():
    prompt = build_goal_prompt("Use conservative approvals.")
    assert "choose a project-local path" in prompt
    cmd = build_codex_command(Path("/tmp/project"), prompt, yolo=False, no_alt_screen=False)
    assert "--yolo" not in cmd
    assert "--no-alt-screen" not in cmd
    assert cmd == ["codex", "--cd", "/tmp/project", prompt]


def test_goal_command_can_use_resolved_codex_executable():
    prompt = build_goal_prompt("Use resolved command.")
    cmd = build_codex_command(Path("/tmp/project"), prompt, executable="/tmp/bin/codex")
    assert cmd[0] == "/tmp/bin/codex"


def test_build_shell_command_pins_path_into_exec_env_prefix(tmp_path):
    # run.py hands build_child_env({})["PATH"] (which pins the iteris console-scripts
    # dir, mirroring sub-agents) to build_shell_command as a PATH env-update, so the
    # main /goal loop's codex and the non-login shells it spawns can resolve bare
    # `iteris tool ...` instead of failing with "iteris: command not found".
    root = tmp_path / "project"
    (root / ".iteris").mkdir(parents=True)
    codex_cmd = build_codex_command(root, executable="codex")
    shell_cmd = build_shell_command(root, codex_cmd, env_updates={"PATH": "/venv/bin:/usr/bin"})
    assert "exec env " in shell_cmd
    assert "PATH=/venv/bin:/usr/bin" in shell_cmd


def test_main_loop_path_value_contains_iteris_scripts_dir():
    # The value run.py assigns to home_env["PATH"] must contain the running
    # interpreter's scripts dir (where `iteris` lives) so the launch is
    # install-/root-independent.
    import os
    import sys

    from iteris.codex_logs import build_child_env

    scripts_dir = str(Path(sys.executable).parent)
    loop_path = build_child_env({}).get("PATH", "")
    assert scripts_dir in loop_path.split(os.pathsep)


def test_tmux_attach_command_switches_client_inside_tmux():
    assert tmux_attach_command("iteris-test", env={}) == ["tmux", "attach-session", "-t", "iteris-test"]
    assert tmux_attach_command("iteris-test", env={"TMUX": "/tmp/tmux/default,1,0"}) == [
        "tmux",
        "switch-client",
        "-t",
        "iteris-test",
    ]


def test_ensure_codex_project_trusted_appends_config(tmp_path):
    codex_home = tmp_path / "codex-home"
    project = tmp_path / "project"
    project.mkdir()

    result = ensure_codex_project_trusted(project, env={"CODEX_HOME": str(codex_home)})

    section = codex_project_trust_section(project)
    text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert result["updated"] is True
    assert section in text
    assert 'trust_level = "trusted"' in text

    second = ensure_codex_project_trusted(project, env={"CODEX_HOME": str(codex_home)})
    assert second["already_trusted"] is True
    assert second["updated"] is False


def test_ensure_codex_project_trusted_updates_existing_section(tmp_path):
    codex_home = tmp_path / "codex-home"
    config = codex_home / "config.toml"
    project = tmp_path / "project"
    project.mkdir()
    codex_home.mkdir()
    config.write_text(
        "model = \"gpt-5.5\"\n"
        f"{codex_project_trust_section(project)}\n"
        "trust_level = \"untrusted\"\n"
        "extra = \"keep\"\n",
        encoding="utf-8",
    )

    result = ensure_codex_project_trusted(project, env={"CODEX_HOME": str(codex_home)})

    text = config.read_text(encoding="utf-8")
    assert result["updated"] is True
    assert 'trust_level = "trusted"' in text
    assert 'trust_level = "untrusted"' not in text
    assert 'extra = "keep"' in text


def test_goal_defaults_use_project_directory_name():
    problem_id, target = resolve_goal_defaults(Path("/tmp/Iteris My Problem"), problem_id=None, target_artifact=None)
    assert problem_id == "iteris-my-problem"
    assert target == "results/iteris-my-problem/answer.md"

    explicit_problem, explicit_target = resolve_goal_defaults(
        Path("/tmp/project"),
        problem_id="prob-220",
        target_artifact=None,
    )
    assert explicit_problem == "prob-220"
    assert explicit_target == "results/prob-220/answer.md"


def test_goal_command_can_omit_prompt_argument_for_tmux_launch():
    prompt = build_goal_prompt("Keep prompt out of argv.")
    root = Path("/tmp/project")
    cmd = build_codex_command(root, prompt, prompt_argument=False)
    assert cmd == ["codex", "--yolo", "--no-alt-screen", "--cd", str(root)]
    shell_cmd = build_shell_command(root, cmd)
    assert "goal_prompt.txt" not in shell_cmd
    assert "/goal" not in shell_cmd
    file_arg_cmd = build_shell_command(root, cmd, prompt_file_argument=True)
    assert '$(cat .iteris/goal_prompt.txt)' in file_arg_cmd
    assert "Keep prompt out of argv" not in file_arg_cmd


def test_goal_prompt_can_explicitly_allow_blocker_completion():
    prompt = build_goal_prompt("Analyze the proof status.", allow_blocker_completion=True)
    assert "explicitly allowed blocker completion" in prompt
    assert "Do not treat a verified blocker" not in prompt


def test_codex_prompt_ready_detects_interactive_input():
    assert codex_prompt_ready("› Use /skills to list available skills\n\ngpt-5.5 xhigh fast")
    assert codex_prompt_ready("Tip: Use /personality\n\n›")
    assert not codex_prompt_ready("• Booting MCP server: codex_apps")
    trust_prompt = "Do you trust the contents of this directory?\n\n› 1. Yes, continue\n\nPress enter to continue"
    assert codex_trust_prompt_present(trust_prompt)
    assert not codex_prompt_ready(trust_prompt)
    file_prompt = build_goal_file_reference_prompt()
    assert file_prompt.startswith("/goal Read `.iteris/goal_prompt.txt`")
    assert "authoritative" in file_prompt


def test_accept_codex_trust_prompt_handles_codex_header(monkeypatch):
    panes = iter(
        [
            "OpenAI Codex\n\nDo you trust the contents of this directory?\n\n› 1. Yes, continue\n  2. No, quit\n\nPress enter to continue",
            "OpenAI Codex\n\nTip: Use /status\n\n›",
        ]
    )
    sent: list[list[str]] = []

    monkeypatch.setattr(goal_command.session, "capture_pane", lambda session_name, lines=80: next(panes))

    def fake_run(command: list[str], check: bool = False):
        sent.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(goal_command.subprocess, "run", fake_run)

    result = goal_command.accept_codex_trust_prompt("iteris-test", timeout_seconds=2, poll_interval=0)

    assert result == {"observed": True, "accepted": True}
    assert sent == [build_submit_prompt_command("iteris-test")]


def test_goal_logging_paths_and_pipe_command(tmp_path):
    paths = build_goal_log_paths(tmp_path, "iteris-test", stamp="20260603T000000000000Z")
    assert paths["pane_log"].name == "goal-iteris-test-20260603T000000000000Z.pane.log"
    assert paths["meta"].name == "goal-iteris-test-20260603T000000000000Z.meta.json"

    pipe_cmd = build_pipe_pane_command("iteris-test", paths["pane_log"])
    assert pipe_cmd[:5] == ["tmux", "pipe-pane", "-o", "-t", "iteris-test"]
    assert "cat >>" in pipe_cmd[-1]

    shell_session = build_tmux_shell_command("iteris-test")
    assert shell_session == ["tmux", "new-session", "-d", "-s", "iteris-test"]

    send_cmd = build_send_keys_command("iteris-test", "echo ok")
    assert send_cmd == ["tmux", "send-keys", "-t", "iteris-test", "echo ok", "Enter"]

    prompt_file = tmp_path / ".iteris" / "goal_prompt.txt"
    assert build_load_prompt_buffer_command("iteris-test", prompt_file) == [
        "tmux",
        "load-buffer",
        "-t",
        "iteris-test",
        "-b",
        "iteris_goal_prompt",
        str(prompt_file),
    ]
    assert build_paste_prompt_buffer_command("iteris-test") == [
        "tmux",
        "paste-buffer",
        "-t",
        "iteris-test",
        "-b",
        "iteris_goal_prompt",
    ]
    assert build_submit_prompt_command("iteris-test") == ["tmux", "send-keys", "-t", "iteris-test", "Enter"]

    assert build_interrupt_command("iteris-test") == ["tmux", "send-keys", "-t", "iteris-test", "C-c"]
    assert build_kill_session_command("iteris-test") == ["tmux", "kill-session", "-t", "iteris-test"]


def test_prepare_codex_home_respects_codex_home_env(tmp_path, monkeypatch):
    source = tmp_path / "custom-codex-home"
    source.mkdir()
    (source / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source))

    root = tmp_path / "proj"
    root.mkdir()
    home = goal_command.prepare_codex_home(root, "sess", "20260101T000000000000Z")
    link = home / "auth.json"
    assert link.is_symlink()
    assert link.resolve() == (source / "auth.json").resolve()


def test_prune_goal_runs_keeps_newest(tmp_path, monkeypatch):
    import os
    import time

    # Isolate from the host's real tmux sessions.
    monkeypatch.setattr(goal_command, "_live_goal_session_slugs", lambda: set())

    root = tmp_path / "proj"
    home_root = root / ".iteris" / "codex_home"
    logs_dir = root / ".iteris" / "logs"
    logs_dir.mkdir(parents=True)
    base = time.time() - 1000
    for i in range(4):
        d = home_root / f"goal-s-2026010{i}T000000000000Z"
        d.mkdir(parents=True)
        os.utime(d, (base + i, base + i))
        pane = logs_dir / f"goal-s-2026010{i}T000000000000Z.pane.log"
        pane.write_text("x", encoding="utf-8")
        meta = logs_dir / f"goal-s-2026010{i}T000000000000Z.meta.json"
        meta.write_text("{}", encoding="utf-8")
        os.utime(pane, (base + i, base + i))

    goal_command.prune_goal_runs(root, keep=2)

    remaining_dirs = sorted(p.name for p in home_root.iterdir())
    assert remaining_dirs == ["goal-s-20260102T000000000000Z", "goal-s-20260103T000000000000Z"]
    remaining_panes = sorted(p.name for p in logs_dir.glob("goal-*.pane.log"))
    assert remaining_panes == [
        "goal-s-20260102T000000000000Z.pane.log",
        "goal-s-20260103T000000000000Z.pane.log",
    ]
    # Meta logs of pruned runs go with their pane logs.
    assert not (logs_dir / "goal-s-20260100T000000000000Z.meta.json").exists()
    assert (logs_dir / "goal-s-20260103T000000000000Z.meta.json").exists()


def test_stamp_status_phase_pins_contract_vocabulary(tmp_path):
    from iteris.commands.goal import _stamp_status_phase

    (tmp_path / "STATUS.md").write_text("phase: complete\nnext: finalized\n", encoding="utf-8")
    assert _stamp_status_phase(tmp_path, "goal_success_verified") is True
    text = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
    assert "phase: goal_success_verified" in text and "phase: complete" not in text
    # Idempotent.
    assert _stamp_status_phase(tmp_path, "goal_success_verified") is False
    # Missing phase line: prepend.
    (tmp_path / "STATUS.md").write_text("next: done\n", encoding="utf-8")
    assert _stamp_status_phase(tmp_path, "goal_success_verified") is True
    assert (tmp_path / "STATUS.md").read_text(encoding="utf-8").startswith("phase: goal_success_verified")


def test_stamp_status_phase_refreshes_last_updated(tmp_path):
    # A CLI-side STATUS write must not leave a stale
    # last_updated: header.
    from iteris.commands.goal import _stamp_status_phase

    (tmp_path / "STATUS.md").write_text(
        "phase: executing\nlast_updated: 2026-01-01T00:00:00Z\nnext: x\n", encoding="utf-8"
    )
    assert _stamp_status_phase(tmp_path, "goal_success_verified") is True
    text = (tmp_path / "STATUS.md").read_text(encoding="utf-8")
    assert "last_updated: 2026-01-01T00:00:00Z" not in text
    assert re.search(r"(?m)^last_updated: \d{4}-\d{2}-\d{2}T", text)
    # Header inserted after phase when absent.
    (tmp_path / "STATUS.md").write_text("phase: executing\nnext: y\n", encoding="utf-8")
    assert _stamp_status_phase(tmp_path, "goal_success_verified") is True
    assert re.search(r"(?m)^last_updated: \d{4}-", (tmp_path / "STATUS.md").read_text(encoding="utf-8"))


def test_goal_prompt_has_inbox_poll_after_wait_contract():
    # A parked loop must re-check the inbox on each wait boundary.
    prompt = build_goal_prompt("Solve it.", target_artifact="results/p/answer.md", problem_id="p")
    assert "After every `iteris tool agent wait`" in prompt
    assert "re-check the inbox" in prompt


def test_goal_prompt_has_principled_stop_escape_valve():
    # The contract offers a verifier-gated honest terminal (principled
    # stop) instead of churning forever on an impossible-as-stated goal.
    prompt = build_goal_prompt("Solve it.", target_artifact="results/p/answer.md", problem_id="p")
    assert "PRINCIPLED STOP" in prompt
    assert "--mode principled_stop" in prompt
    assert "answer_reduced_verified.md" in prompt
    assert "goal finalize . --principled-stop" in prompt


def test_goal_prompt_forbids_self_narrowing_and_demands_matching_lower_bound():
    # The contract judges success against the source's full quantifier structure
    # (no self-narrowing to fake goal_success) and requires a matching lower bound
    # for optimal/sharp goals.
    prompt = build_goal_prompt("Solve it.", target_artifact="results/p/answer.md", problem_id="p")
    assert "full quantifier structure" in prompt
    assert "honest PARTIAL" in prompt
    assert "matching lower bound" in prompt
    assert "NOT a license to give up" in prompt
    assert "a principled stop is certified" in prompt
