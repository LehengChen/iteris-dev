"""Goal-contract prompt construction (the text the codex /goal loop reads)."""

from __future__ import annotations

from iteris.project import read_json
from pathlib import Path
from typing import Sequence


def build_generalization_block(generalization: dict) -> str:
    """Render the generalization-context block injected into a goal prompt.

    ``generalization`` is the lineage context produced by the generalize feature:
    parent project name, the parent verified result path, the direction title and
    its in-project sources file, and the inherited facts (child fact id + summary).
    Inherited facts enter the child project as ``reviewed`` and must be re-verified
    for applicability before they are used in the final assembly.
    """
    parent_name = generalization.get("parent_name") or "the parent project"
    source_result = generalization.get("source_result") or "(parent result)"
    title = generalization.get("direction_title") or "(unspecified direction)"
    sources_file = generalization.get("direction_sources_file")
    direction_ref = f" (see `sources/{sources_file}` for the full statement / first steps)" if sources_file else ""
    inherited = generalization.get("inherited_facts") or []
    if inherited:
        fact_lines = "\n".join(
            f"    - `{item.get('child_fact_id')}` — {item.get('claim_summary', '')}" for item in inherited
        )
        inherited_text = (
            "- Inherited facts (status: reviewed, NOT verified). These are the PARENT's "
            "instance-specific results; your task is to abstract or re-prove each as the "
            "generalized-setting version, not to reuse it verbatim:\n"
            + fact_lines
            + "\n"
            + "  Each was verified only in the parent's specific setting. For every one you rely "
            "on, either (a) re-verify that the same statement still holds in the generalized "
            "setting, or (b) replace it with the abstracted lemma the generalization needs and "
            "verify that; if it does not transfer, mark it rejected and prove the replacement. "
            "Do not put an inherited fact into the final assembly without re-verification.\n"
        )
    else:
        inherited_text = "- No facts were inherited; establish the needed lemmas from scratch.\n"
    family_context = str(generalization.get("family_context") or "").strip()
    # Membership guidance (family search + supervisor messages) is gated on
    # evolve membership, NOT on the digest being non-empty: the first children
    # of a new family are seeded before any family intelligence exists, and
    # they are exactly the runs the supervisor steers.
    evolve_member = bool(generalization.get("evolve_root")) or bool(family_context)
    family_text = ""
    if family_context:
        indented = "\n".join(f"  {line}" for line in family_context.splitlines())
        family_text += (
            "- Family intelligence (from sibling generalization projects; leads, NOT local facts — "
            "anything you rely on must be imported and re-verified locally):\n"
            + indented
            + "\n"
        )
    if evolve_member:
        family_text += (
            "- This project belongs to an evolve family: `iteris tool memory search` also returns "
            "family-scope results tagged `scope: family`; search before investing in a route.\n"
            "- A supervisor may steer this run via structured messages. Check "
            "`iteris tool message list . --unread --json` when reassessing the frontier and before "
            "starting a new task; after processing a message, acknowledge it with "
            "`iteris tool message ack . --msg-id <id> --disposition applied|noted|declined`. "
            "High-priority messages require an ack.\n"
        )
    return (
        "Generalization context:\n"
        f"- This is a generalization task. Parent verified result: `{parent_name}/{source_result}`.\n"
        f"- Direction: {title}{direction_ref}.\n"
        + inherited_text
        + family_text
        + "- Goal: prove the generalized theorem stated in the direction, producing the terminal "
        "artifact only after fact, assembly, and goal-success verification pass.\n\n"
    )


def build_project_context_lines(root: Path) -> list[str]:
    """Goal-contract lines derived from project state set up at `iteris new` time.

    Covers user-provided reference packs (references/MANIFEST.json) and advisory
    boundary knowledge inherited from a prior project (.iteris/inherit.json).
    """
    lines: list[str] = []
    if (root / "references" / "MANIFEST.json").exists():
        lines.append(
            "- User-provided reference material is indexed in `references/MANIFEST.json`. Read the manifest early and consult the listed files before re-deriving known results or fetching external references.\n"
        )
    inherit_payload = read_json(root / ".iteris" / "inherit.json", default=None)
    if isinstance(inherit_payload, dict):
        for parent in inherit_payload.get("parents") or []:
            if not isinstance(parent, dict):
                continue
            parent_id = parent.get("parent_project")
            summary = parent.get("summary_path")
            count = len(parent.get("imported_facts") or [])
            lines.append(
                f"- This project inherited {count} advisory boundary fact(s) from the prior project `{parent_id}` (verified blockers and rejected routes; `fact_type: inherited_boundary`, status `reviewed`). "
                + (f"Read `{summary}` before planning. " if summary else "")
                + "Do not re-explore a lane the boundary marks dead unless you have a genuinely new idea, and re-verify any inherited fact locally before making it load-bearing in a proof or assembly.\n"
            )
    return lines


def build_goal_prompt(
    goal: str,
    *,
    target_artifact: str | None = None,
    problem_id: str | None = None,
    allow_blocker_completion: bool = False,
    generalization: dict | None = None,
    project_context_lines: Sequence[str] | None = None,
) -> str:
    target_text = (
        f"Terminal artifact path: `{target_artifact}`.\n"
        if target_artifact
        else "No terminal artifact path was supplied. Before substantive work, choose a project-local path such as "
        "`results/<problem_id>/answer.md`, record it in STATUS.md, and use it as the terminal artifact. Do not name it "
        "`answer_verified.md`: `iteris tool goal finalize` writes the `_verified` copy itself once goal-success verification passes.\n"
    )
    problem_text = f"Problem id: `{problem_id}`.\n" if problem_id else ""
    assembly_command = (
        f"`iteris tool verify submit . --backend agent --mode assembly --claim <goal-summary> --target-artifact {target_artifact} --json`"
        if target_artifact
        else "`iteris tool verify submit . --backend agent --mode assembly --claim <goal-summary> --target-artifact <terminal-artifact> --json`"
    )
    goal_success_command = (
        f"`iteris tool verify submit . --backend agent --mode goal_success --claim <original-goal> --artifact {target_artifact} --target-artifact {target_artifact} --json`"
        if target_artifact
        else "`iteris tool verify submit . --backend agent --mode goal_success --claim <original-goal> --artifact <terminal-artifact> --target-artifact <terminal-artifact> --json`"
    )
    finalize_command = (
        f"`iteris tool goal finalize . --target-artifact {target_artifact} --json`"
        if target_artifact
        else "`iteris tool goal finalize . --target-artifact <terminal-artifact> --json`"
    )
    blocker_text = (
        "- The user explicitly allowed blocker completion for this run. Only treat a verified blocker or gap report as completion when it is the requested terminal answer; otherwise keep working.\n"
        if allow_blocker_completion
        else "- Do not treat a verified blocker, gap report, or partial negative result as goal completion. Verified blockers are intermediate facts: record and verify them, then keep exploring alternate routes or repairing assumptions until the requested answer artifact itself passes assembly and goal-success verification. Narrowing the goal to a self-chosen sub-model or scope does NOT produce goal completion: success is judged against the SOURCE problem's full quantifier structure, so a narrowed result is an honest PARTIAL — finalize it via a principled stop (below), do not relabel it as a full solution.\n"
    )
    generalization_block = build_generalization_block(generalization) if generalization else ""
    return (
        "/goal "
        + goal.strip()
        + "\n\n"
        + generalization_block
        + "Iteris goal contract:\n"
        + problem_text
        + target_text
        + "- Treat checkpoints as archival/recovery only; a checkpoint is not goal completion.\n"
        + "- Keep the project recoverable: after a substantial research artifact, promoted fact, verification result, or route change, run `iteris tool git checkpoint . --message \"checkpoint: <summary>\"`. Avoid checkpointing every small search.\n"
        + "- Keep working until EITHER (a) the terminal artifact exists, answers the requested goal, and real verification-agent assembly + goal-success verifications pass, OR (b) a principled stop is certified (see the PRINCIPLED STOP step below), unless the user explicitly stops the run.\n"
        + "- When you edit STATUS.md, bump its `last_updated:` header to the real edit time (UTC ISO-8601). A reader trusting a stale header is misled about how current the status is. For a long inline task you are working on yourself (no assigned sub-agent run), post a brief heartbeat into its `tasks/TASK_POOL.json` `notes` periodically so stale-task attention has positive liveness evidence rather than firing a false 'stalled' signal.\n"
        + "- Work persistently and bravely inside the same `/goal`: when one route is blocked, use the verified blocker to choose the next route rather than ending the run.\n"
        + blocker_text
        + "- The terminal artifact should be concise and verifiable: include the original goal/problem summary, a fact index with `fact:` ids, each fact status/verification link, and a short assembly explaining how the facts answer the goal.\n"
        + "- Durable fact definition: a `fact:` is a stable, reusable claim that is absolutely true within its explicit assumptions and evidence scope. Do not use facts for mutable project state, route status, plans, priorities, literature impressions, or claims like what has been tried so far; put those in scratch memory, TASK_POOL, FRONTIER_INDEX, or artifacts.\n"
        + "- When a fact's statement or proof relies on earlier facts, record them with `--predecessor <fact:id>` (repeatable) at add-fact time. The dependency graph is what identifies keystone facts that deserve panel verification; a fact whose body cites `fact:` ids that are missing from its predecessors will fail validation cleanliness.\n"
        + "- A numerical experiment fact must declare its discriminating power: name the baseline/control (what a trivial, random, or fixed-instance baseline scores on the same instances) and what outcome would falsify the claim. An experiment that any baseline also passes is 0-discriminating and must not be cited as empirical support; design adversarial-vs-baseline instance suites, not pass-only suites.\n"
        + "- Certify before building on sampling evidence: finite instance sweeps, modular computations, and fitted curves are provisional, however many points agree. Before spawning route-building tasks anchored on such evidence, attempt a cheap exact certification (interval arithmetic, Sturm/Groebner certificates, exact rational evaluation at the relevant points) and record the certified scope in the fact; evidence that resists certification is a lead to test, not a foundation to build on.\n"
        + "- Use only verified facts in the final assembly. If a required fact fails verification, repair it, replace it, or mark it rejected and continue.\n"
        + "- Run real fact verification with `iteris tool verify submit . --backend agent --mode fact ... --json` for durable claims before relying on them in the final assembly. After a fact verification passes, use `iteris tool memory promote-fact . --fact-id <fact:id> --verification <request-id> --json`; do not hand-edit fact metadata unless the CLI is unavailable.\n"
        + "- `iteris tool verify submit . --backend agent ... --json` RUNS the verifier synchronously and RETURNS the verdict as JSON when it finishes (usually several minutes); read that returned JSON directly. NEVER background a verify submit with `&`, and NEVER write your own `sleep`/`until`/`while [ -f verification/results/... ]` shell loop to wait for a verdict: a hand-written waiter that misses the result file (or whose verifier died) deadlocks the entire `/goal` loop and the run stops making progress. Just let the submit command return.\n"
        + "- If you must wait on a verification that was submitted out-of-band (not by the submit you are currently running), use `iteris tool verify wait . --request-id <id> --timeout <seconds> --json`. It always returns: `status: done` with the verdict, `status: dead` (verifier gone — salvage with `iteris tool verify finalize . --request-id <id> --json` or resubmit), or `status: timeout`. Do not hand-roll this wait.\n"
        + "- Never park the loop on a single verification: verification agents can die silently, leaving a request with no result forever. If a request has no result after ~20 minutes, check `attention.stale_verifications` in `iteris tool context . --json`; when it reports the verifier dead, salvage with `iteris tool verify finalize . --request-id <id> --json` or resubmit, and keep doing other frontier work in the meantime.\n"
        + "- Use `tasks/TASK_POOL.json` as the work queue. Inspect it with `iteris tool task pool show . --json`, select work with `iteris tool task pool select-ready . --json`, and update status with `iteris tool task pool update . --task-id <task-id> --status ready|running|review|blocked|done|rejected|paused --json`. Use `done` for completed task-pool work.\n"
        + "- Act on the `attention` block in `iteris tool context . --json`. Stale running/review tasks are unharvested debt: inspect, harvest, or reset them before launching new work. A `rejection_streaks` entry means the same claim keeps failing verification; do NOT submit another proof revision of it. First run a falsification task against the claim itself (an execute agent whose objective is to find a counterexample or disprove it, especially near degenerate parameter regions), or change the decomposition. Resume direct proof attempts only after falsification fails. When more than 5 tasks sit in `review`, drain the review queue first — harvest each completed agent's output into facts/artifacts and mark the task done or rejected — before launching new execute agents; an unharvested output is work the project cannot use.\n"
        + "- Verification depth must scale with how load-bearing a fact is. An `attention.under_verified_keystones` entry marks a fact many later facts depend on (high in-degree: predecessors plus `fact:` citations in fact bodies) that has only one passed verification — a single flaw there collapses everything downstream. Before building further on a keystone fact, run `iteris tool verify panel . --mode fact --claim \"<fact claim>\" --artifact <fact-file> --fact-id <fact:id> --runs 2 --json` (independent adversarial verifiers; the panel passes only if every run passes) and repair or demote the fact if the panel fails.\n"
        + "".join(project_context_lines or [])
        + "- Operator messages are part of the loop: at every checkpoint and before starting a new task, check `unread_messages` in `iteris tool context . --json` (or run `iteris tool message list . --unread --json`). Act on high-priority messages before opening new work, and acknowledge every processed message with `iteris tool message ack . --msg-id <id> --disposition applied|noted|declined`; consuming a message's content without acking leaves the operator blind to whether it landed.\n"
        + "- To wait for a detached subagent, use `iteris tool agent wait . --run-id <run-id> --timeout <seconds> --json` instead of hand-written shell polling loops; it returns the terminal status and resolves dead workers instead of hanging.\n"
        + "- After every `iteris tool agent wait`/`iteris tool verify wait` returns — whether the work finished OR the wait timed out — re-check the inbox (`unread_messages` in `iteris tool context . --json`, or `iteris tool message list . --unread --json`) before launching the next step. A long sub-agent can park the loop inside one turn for many minutes; polling on each wait boundary keeps high-priority operator steers seen within one wait window instead of sitting unacked until the turn ends. Keep `--timeout` bounded (minutes, not hours) so the cadence stays tight.\n"
        + "- Keep `memory/facts/FRONTIER_INDEX.json` as the route map over facts. After major verified facts, rejected claims, or task-pool changes, run `iteris tool frontier refresh . --json`, then `iteris tool frontier health . --json`.\n"
        + "- If frontier health reports `explore_recommended: true`, launch `iteris tool agent explore . --focus \"<recommended_focus>\" --detach --json` before adding more execute tasks inside the same blocker pattern.\n"
        + "- Use subagents as background tools when useful: `iteris tool agent explore . --focus \"...\" --detach --json` for broad route discovery, `iteris tool agent execute . --task-id <task-id> --mode foundation|proof|experiment|algorithm --detach --json` for focused work, then inspect progress with `iteris tool agent runs . --json` and `iteris tool agent inspect . --run-id <run-id> --json`. Subagent completion is not goal completion; use their logs and outputs to update the task pool and frontier map.\n"
        + "- Use the structured artifact layout. Raw subagent logs stay in `artifacts/agent_runs/`. Route summaries belong in `artifacts/route_checks/`; proof attempts and verification claims in `artifacts/proofs/`; reproducible scripts/configs/raw outputs in `artifacts/experiments/`; reusable prototypes or implementation notes in `artifacts/code/`. Every subagent request includes an `artifact_workspace` and `artifact_manifest`; prefer those paths for new durable artifacts and list them in TASK_POOL expected outputs when adding tasks. Use `artifacts/ARTIFACT_INDEX.jsonl` as the global append-only artifact index and keep files grouped under `artifacts/<kind>/<task-label>/<run-id>/` to avoid flat-directory explosion. You may create artifact files directly, but before treating a script/report-producing task as ready/done, run `iteris tool artifact gate . --json` and fix unindexed scripts or manifest schema issues.\n"
        + "- Keep retrieval evidence project-local. Use `iteris tool theorem search . --query ... --json` for theorem search when relevant. For arXiv references, use `iteris tool theorem fetch . --arxiv-id <id> --json`; it fetches arXiv source first and only falls back to PDF text extraction when source is unavailable. Do not start with ad hoc `pdftotext`.\n"
        + f"- Before final completion, run {assembly_command}; if it does not report `passed: true`, repair and verify again.\n"
        + f"- Then run {goal_success_command}; if it does not report `passed: true`, the original goal is not complete. Continue exploring, planning, executing, repairing facts, and re-verifying until it passes or the user explicitly stops the run.\n"
        + "- For `goal_success`, attach the source file and the passed assembly verification result as extra `--artifact` values when available; using only the terminal artifact is a fallback, not the preferred reproducible path.\n"
        + "- goal_success is judged against the SOURCE problem's full quantifier structure, not a claim you author: do not shrink the `--claim` to a self-chosen sub-model/scope to make it pass (a narrowed result is an honest PARTIAL — use the principled stop). If the source's deliverable is to PROVE a quantity OPTIMAL/SHARP/TIGHT (not merely to achieve a stated bound or give an algorithm), `optimal` requires a verified constructive upper bound AND a matching lower bound in the intended regime and the SAME resource model; a trivializing model (e.g. an exact-cost model so the constant collapses to 1) does not prove it.\n"
        + "- PRINCIPLED STOP (a controlled honest terminal — NOT a license to give up): if, after genuine exploration, you reach a VERIFIED obstruction showing the full goal is unreachable exactly as stated (a verified impossibility fact for the goal as written) or that it provably reduces to a precisely-characterized open subproblem you cannot close, you MAY finalize via a principled stop instead of looping forever. To do so: (1) write into the terminal artifact the STRONGEST valid result you actually achieved AND verified (e.g. the conditional/partial theorem) PLUS the verified obstruction, exactly why it blocks the full goal, and the rejected routes you explored; (2) run the assembly verification above, then `iteris tool verify submit . --backend agent --mode principled_stop --claim <original-goal> --artifact <source-file> --artifact <terminal-artifact> --target-artifact <terminal-artifact> --json`; (3) ONLY if it reports `passed: true`, run `iteris tool goal finalize . --principled-stop --target-artifact <terminal-artifact> --json` (this emits `answer_reduced_verified.md`, a terminal distinct from a solved `answer_verified.md`). The principled_stop verifier is ADVERSARIAL and defaults to reject: it rejects unestablished/handwaved impossibility, misformalized or degenerate-instance obstructions (the mirror of the goal_success degeneracy check), and premature stops with obvious unexplored routes. Do not invoke it to escape hard work — only when the obstruction is real, verified, and on the genuine full goal.\n"
        + "- If run-log archival is needed, run `iteris tool logs bundle . --session <tmux-session> --json` before the final checkpoint so the bundle can be committed.\n"
        + f"- Before marking `/goal` complete, update STATUS.md/TASK_POOL as needed, checkpoint the final state, and run {finalize_command}. If finalization does not report `ok: true`, continue repairing.\n\n"
        + "Use Iteris project files first: PROJECT.md, ROADMAP.md, STATUS.md, memory/, tasks/, verification/, results/. "
        + "Start with `iteris tool context . --json`; use `iteris tool memory search . --query ... --json`, "
        + "`iteris tool memory add-fact . ...` for durable facts, `iteris tool task list . --json`, "
        + "`iteris tool task pool show . --json`, `iteris tool task pool select-ready . --json`, "
        + "`iteris tool frontier show . --json`, `iteris tool frontier refresh . --json`, `iteris tool frontier health . --json`, "
        + "`iteris tool agent explore . --focus ... --detach --json`, `iteris tool agent execute . --task-id ... --mode ... --detach --json`, "
        + "`iteris tool agent runs . --json`, `iteris tool agent inspect . --run-id ... --json`, "
        + "`iteris tool theorem search . --query ... --json`, "
        + "`iteris tool theorem fetch . --arxiv-id ... --json`, "
        + "`iteris tool verify submit . --backend agent --mode fact ... --json`, "
        + "`iteris tool verify submit . --backend agent --mode goal_success ... --json`, `iteris tool verify status . --json`, "
        + "`iteris tool goal finalize . --target-artifact ... --json`, "
        + "`iteris tool logs bundle . --session ... --json`, "
        + "`iteris tool memory promote-fact . --fact-id ... --verification ... --json`, "
        + "and `iteris tool git status .` before making claims."
    )


def build_goal_file_reference_prompt(prompt_file: str = ".iteris/goal_prompt.txt") -> str:
    return (
        f"/goal Read `{prompt_file}` first, then execute the complete Iteris goal contract stored there. "
        "The prompt file is authoritative; keep working until its terminal artifact passes the required verification."
    )
