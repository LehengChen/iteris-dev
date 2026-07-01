# Iteris Guide Index

schema_version: iteris.guide_index.v0

## What is Iteris

Iteris is a goal-driven research agent workspace. A human usually starts with
`iteris monitor`; the agent loop runs via `iteris run` or an evolve family via
`iteris evolve run`.

## Setup flow

1. `bash install.sh` then `iteris doctor`
2. Install at least one agent CLI: **Codex** (`codex`) or **Claude Code** (`claude`)
3. Run `codex` or `claude` once and complete login/authorization
4. Optional: `export ITERIS_EXECUTOR=claude` (default is codex)
5. Start: `iteris monitor`

## Human entry points

| Command | When to use |
|---------|-------------|
| `iteris monitor` | Primary human interaction entry: setup help, project creation, project state, recovery, evolve, next steps |
| `iteris dashboard` | Live logs, facts graph, evolve family tree |
| `iteris new --source …` | Create a project (monitor can guide this) |
| `iteris run` | Start the worker agent loop |
| `iteris evolve init/run/status/…` | Manage a family of generalization projects |
| `iteris doctor` | Environment and project health check |

## Project layout (pointers)

| Path | Meaning |
|------|---------|
| `sources/` | Primary problem statement |
| `references/` | Papers, notes, PDFs |
| `results/` | Terminal answer artifacts |
| `reports/` | Versioned LaTeX/report workspaces |
| `memory/facts/` | Durable verified facts |
| `tasks/TASK_POOL.json` | Task frontier |
| `STATUS.md` | Human-readable project phase |
| `generalize/EVOLVE.json` | Family root only: evolve state |
| `memory/family/` | Family root only: cross-project intelligence |
| `.iteris/INDEX.md` | Project routing index for monitor |
| `docs/OPERATOR.md` | Project-specific operator manual |

## Scene routing (monitor)

| User intent | Read first | Then lookup |
|-------------|------------|-------------|
| New / install / concepts | This GUIDE_INDEX | `doctor` |
| Discuss the problem | Project `.iteris/INDEX.md` | `read_status_md`, project OPERATOR |
| Track progress | Project INDEX | `status`, optionally `evolve_status` |
| LaTeX / report / paper writing | Project INDEX | `report_status` |
| Start or resume run | Project INDEX | `status`, suggest `recover` if needed |
| Evolve family | Project INDEX (role=family_root or family_child) | `evolve_status`, `read_evolve_json` |
| Infrastructure / layout | This GUIDE_INDEX | `doctor` |

## Conventions (do not misstate)

- `iteris stop` stops the **worker** only; `iteris evolve stop` stops the evolve supervisor
- Attach to worker: `iteris attach`; attach to evolve master: `iteris attach --evolve`
- Family-level scheduling uses the **direction pool** (`generalize/EVOLVE.json`), not "frontier"
- Descendants search family memory by default; re-verify locally before relying on it
- Worker session: `iteris-<project>`; evolve master: `iteris-evolve-<project>`
- Formal report work uses top-level `reports/`, not evolve stage reports under `artifacts/reports/`
- Report evidence files reference the fact graph with project-relative paths; portable mode hides internal evidence from rendered LaTeX without deleting it

## Document layers

1. **GUIDE_INDEX** (this file): global routing and conventions
2. **docs/user-guide.md** (repository): general Iteris operations
3. **`<project>/docs/OPERATOR.md`**: problem-specific notes
4. **`<project>/.iteris/INDEX.md`**: project identity and file pointers
