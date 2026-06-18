"""Tests for monitor language resources and prompt loading."""

from __future__ import annotations

from iteris.guide.context import build_monitor_handoff
from iteris.guide.locale import SUPPORTED_LOCALES, menu_resource, t, validate_locale_resources
from iteris.guide.prompt import (
    monitor_handoff_footer,
    monitor_session_mode,
    monitor_system,
    opening_user_message,
)


def test_locale_resources_are_consistent():
    validate_locale_resources()


def test_monitor_prompt_resources_are_readable():
    for locale in SUPPORTED_LOCALES:
        assert monitor_system(locale)
        assert monitor_session_mode(locale)
        assert monitor_handoff_footer(locale)


def test_monitor_prompts_require_questions_and_confirmation():
    zh_system = monitor_system("zh")
    en_system = monitor_system("en")
    assert "默认每次回复以 1 个简短的推进问题收尾" in zh_system
    assert "主动做只读检查" in zh_system
    assert "必须先简短说明动作并获得用户确认" in zh_system
    assert "不要把可复制命令作为主要答案" in zh_system
    assert "数学上已经推进到哪里" in zh_system
    assert "evolve_status.current_child.nodes[].result_summary/phase" in zh_system
    assert "end every response with exactly 1 short task-moving question" in en_system
    assert "perform read-only inspection without asking first" in en_system
    assert "get user confirmation" in en_system
    assert "Do not give copyable commands as the main answer" in en_system
    assert "what has advanced mathematically" in en_system
    assert "evolve_status.current_child.nodes[].result_summary/phase" in en_system


def test_wizard_strings_are_resource_backed():
    assert "项目目录" in t("zh", "wizard_dir")
    assert "Project directory" in t("en", "wizard_dir")
    assert "1-4" in t("zh", "menu_input")
    assert "1-4" in t("en", "menu_input")


def test_menu_titles_are_concise():
    for locale in SUPPORTED_LOCALES:
        for items in menu_resource(locale).values():
            for _key, title, message in items:
                assert "(" not in title
                assert ")" not in title
                assert "（" not in title
                assert "）" not in title
                assert len(title) <= 32
                if message:
                    assert len(message) > len(title)


def test_menu_seed_messages_drive_dialogue_not_command_lists():
    zh_new_project = menu_resource("zh")["none"][1][2]
    en_new_project = menu_resource("en")["none"][1][2]
    zh_continue = menu_resource("zh")["none"][2][2]
    en_continue = menu_resource("en")["none"][2][2]
    assert zh_new_project is not None
    assert "确认" in zh_new_project
    assert en_new_project is not None
    assert "confirm" in en_new_project.lower()
    assert zh_continue is not None
    assert "不要要求我先切目录或执行命令" in zh_continue
    assert en_continue is not None
    assert "rather than asking me to switch directories or run commands" in en_continue
    for _key, _title, message in menu_resource("zh")["single"]:
        if message:
            assert "问我" in message or "是否" in message
            assert "可复制命令" not in message
    for _key, _title, message in menu_resource("en")["single"]:
        if message:
            assert "ask" in message.lower() or "whether" in message.lower()
            assert "copy-paste commands" not in message


def test_progress_menu_seeds_request_math_progress_first():
    zh_family_progress = menu_resource("zh")["family_root"][0][2]
    en_family_progress = menu_resource("en")["family_root"][0][2]
    assert zh_family_progress is not None
    assert "数学上推进到什么程度" in zh_family_progress
    assert "是否继续一起讨论" in zh_family_progress
    assert "最关心" not in zh_family_progress
    assert en_family_progress is not None
    assert "advanced mathematically" in en_family_progress
    assert "continue discussing" in en_family_progress
    assert "care most" not in en_family_progress


def test_opening_messages_follow_locale():
    zh = opening_user_message(locale="zh", in_project=False, role="none")
    en = opening_user_message(locale="en", in_project=False, role="none")

    assert "项目目录外" in zh
    assert "继续已有项目" in zh
    assert "outside an Iteris project directory" in en
    assert "continue an existing project" in en


def test_monitor_handoff_uses_zh_prompt_resources():
    text = build_monitor_handoff(
        project_root=None,
        user_message="当前项目进度如何？",
        lookups={},
        role="none",
        executor="codex",
        locale="zh",
    )

    assert "你是 Iteris Monitor 助手" in text
    assert "提出 1 个推进任务的问题" in text
    assert "context_hints" in text
    assert "当前项目进度如何？" in text


def test_monitor_handoff_uses_en_prompt_resources():
    text = build_monitor_handoff(
        project_root=None,
        user_message="How do I start?",
        lookups={},
        role="none",
        executor="codex",
        locale="en",
    )

    assert "You are the Iteris monitor assistant" in text
    assert "ask 1 question that moves the task forward" in text
    assert "context_hints" in text
    assert "How do I start?" in text


def test_handoff_context_hints_follow_role():
    single = build_monitor_handoff(
        project_root=None,
        user_message="status",
        lookups={},
        role="single",
        executor="codex",
        locale="en",
    )
    child = build_monitor_handoff(
        project_root=None,
        user_message="status",
        lookups={},
        role="family_child",
        executor="codex",
        locale="en",
    )

    assert "lookups.status.math_progress.frontier" in single
    assert "lookups.evolve_status.current_child" in child


def test_handoff_task_critical_snapshot_precedes_guide():
    lookups = {
        "status": {
            "project_path": "/tmp/project",
            "run_state": {"status": "running"},
            "run_active": True,
            "needs_recovery": False,
            "target_artifact": "results/project/answer.md",
            "target_exists": True,
            "session_live": True,
            "math_progress": {
                "status_excerpt": "phase: proof_search",
                "target_artifact": "results/project/answer.md",
                "target_exists": True,
                "facts": {"total": 2},
                "frontier": {"active": [{"frontier_id": "frontier-main"}]},
                "blockers": {"blocked_tasks": [{"task_id": "task-blocked"}]},
                "tasks": {"by_status": {"blocked": 1}},
            },
        }
    }
    text = build_monitor_handoff(
        project_root=None,
        user_message="status",
        lookups=lookups,
        role="single",
        executor="codex",
        locale="en",
    )

    assert text.index("# TASK-CRITICAL SNAPSHOT") < text.index("# GUIDE_INDEX")
    assert '"run_state": {' in text
    assert "task-blocked" in text
    assert "frontier-main" in text


def test_handoff_includes_report_lookup_when_present():
    lookups = {
        "report_status": {
            "reports_dir": "reports",
            "report_count": 1,
            "recent_reports": [{"report_id": "demo", "main_tex": "reports/demo/versions/v001/main.tex"}],
            "templates": ["iteris-report"],
            "styles": ["theory"],
        }
    }
    text = build_monitor_handoff(
        project_root=None,
        user_message="write a LaTeX report",
        lookups=lookups,
        role="single",
        executor="codex",
        locale="en",
    )

    assert "lookups.report_status" in text
    assert '"report_count": 1' in text
    assert "reports/demo/versions/v001/main.tex" in text


def test_family_child_handoff_names_current_child_and_generalization():
    lookups = {
        "status": {
            "math_progress": {
                "generalization": {
                    "evolve_root": {"path": "/tmp/family"},
                    "direction": {"title": "Curved boundary perturbation"},
                }
            }
        },
        "evolve_status": {
            "current_child": {
                "nodes": [{"node_id": "node-child", "phase": "running", "result_summary": "Reduced to commutator."}],
                "directions": [{"direction_id": "dir-child", "title": "Curved boundary perturbation"}],
            },
            "math_progress": {"recent_failed_paths": [{"reason": "Endpoint loss."}]},
        },
    }
    text = build_monitor_handoff(
        project_root=None,
        user_message="查看本节点进度",
        lookups=lookups,
        role="family_child",
        executor="codex",
        locale="zh",
    )

    assert "evolve_status.current_child.nodes[].result_summary/phase" in text
    assert "current_child" in text
    assert "generalization" in text
    assert "Reduced to commutator" in text
    assert "recent_failed_paths" in text
