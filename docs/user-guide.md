# Iteris User Guide

This document supplements GUIDE_INDEX with step-by-step operations for humans
and for `iteris monitor`.

## First-time setup

```bash
bash install.sh
iteris doctor
codex    # or: claude — complete login once
iteris monitor
```

Set a default executor if you prefer Claude Code:

```bash
export ITERIS_EXECUTOR=claude
```

## Create and run a single project

```bash
mkdir -p ./MyProblem && cd ./MyProblem
iteris new --source /path/to/problem.tex
iteris run
iteris dashboard    # optional: inspect live progress
iteris monitor      # ask what to do next
```

Optional: put reference PDFs in `references/` after `new`.

## Track and control a run

- `iteris status` — snapshot: run state, facts, tasks, git
- `iteris dashboard` — live streams and fact graph
- `iteris recover` — after a crash: reconcile dead sessions and orphaned tasks
- `iteris stop` — stop the worker session
- `iteris review` — bundle artifacts for human review
- `iteris report status/new/draft/build` — create a versioned LaTeX report from verified project evidence

Report workspaces live under `reports/`. The MVP layout is `iteris-report`,
a generic Apache-2.0 LaTeX report layout maintained by this project.

## Family closure: parallel sibling North-Star runs

Use `iteris family` when you need to **close several related original problems
in parallel** (siblings), with shared verified-fact pool and joint scheduling.
This is distinct from evolve (which generalizes one verified result).

```bash
iteris family init . --goal "Literal closure of Problems 2.6–2.8." \
  --sibling "id=2.6,path=prob-2-6,session=iteris-prob-26,target=results/prob-2-6/answer_verified.md"
iteris family status .
iteris family schedule --dry-run .
iteris family run .
iteris family export . --from prob-2-6 --fact-id 'fact:...' --usable-by 2.7,2.8
```

## Evolve: generalize across a project family

Use evolve when you have a **verified result** and want to explore many
generalization directions under a budget.

```bash
# In the root project (verified result):
iteris evolve init . --goal "push the theorem to the most general setting"
iteris evolve run
iteris evolve status
iteris dashboard    # Evolve tab on family roots

iteris evolve veto <direction-id>   # human veto during veto window
iteris evolve propose my-dir.md --rank 1 --approve
iteris evolve report
iteris evolve stop                  # stops supervisor; children keep running
```

Family state lives in `generalize/EVOLVE.json` and `memory/family/`. The evolve
supervisor seeds child projects and schedules parallel workers within
`--max-concurrent` and `--budget-hours`.

## When something looks stuck

1. `iteris status` — is the session alive?
2. `iteris recover` — dead session or orphaned agent run?
3. `iteris dashboard` — read recent agent output
4. `iteris monitor` — describe the situation; monitor reads live lookups

## Project-specific notes

Edit `<project>/docs/OPERATOR.md` for problem-specific context. Monitor reads
`.iteris/OPERATOR.md` (a synced copy) alongside `.iteris/INDEX.md`.
