<h1 align="center">Iteris</h1>

<p align="center">
  <strong>Frenzymath · PKU @ AI4Math</strong><br>
  Agentic research loops for computational mathematics
</p>

<p align="center">
  <a href="README.zh-CN.md">中文说明</a>
  · <a href="https://frenzymath.com/blog/iteris/">Blog</a>
  · <a href="docs/user-guide.md">User guide</a>
  · <a href="#quick-start">Quick start</a>
  · <a href="#security-model">Security model</a>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.0-blue">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-green">
</p>

Iteris is a goal-driven research agent workspace toolkit. It keeps source
materials, durable facts, task pools, verification records, live logs, and final
artifacts in one project-local layout so researchers can supervise long-running
agentic research loops.

It is built around three everyday surfaces:

| Surface | Purpose |
| --- | --- |
| `iteris monitor` | Interactive setup, project guidance, recovery, and next-step planning. |
| `iteris run` | The main Codex or Claude Code research loop inside a project workspace. |
| `iteris dashboard` | Local web UI for logs, facts, artifacts, and evolve-family state. |

## Quick Start

For a full local install from a checkout:

```bash
bash install.sh
```

Then create a separate project directory and start the monitor:

```bash
mkdir -p ./MyProblem
cd ./MyProblem
iteris monitor
```

Monitor can guide setup checks, project creation, recovery, dashboard use, and
evolve-family decisions. If you prefer manual commands:

```bash
iteris new --source /path/to/problem.tex
iteris run
iteris dashboard
```

## Requirements

- Python 3.10+
- Git and ripgrep
- tmux for detached runs, or `iteris run --foreground`
- Node.js 20+ and npm for `iteris dashboard`
- Codex (`codex`) or Claude Code (`claude`)

Linux and macOS are the primary supported platforms. Run `codex` or `claude`
once after installation to complete login.

## Security Model

Iteris launches project agents with broad workspace permissions so they can
read, write, run tools, and manage project-local state. Codex launches use an
approval-bypass mode, and Claude Code launches use
`--dangerously-skip-permissions`.

Use Iteris only in workspaces where that access is acceptable. Do not run it in
directories containing unrelated private data, credentials, or files you do not
want an agent CLI to inspect. Project source files and references may be sent to
the configured agent provider through the selected CLI.

## Project Layout

```text
MyProblem/
├── sources/              # Primary problem statement
├── references/           # Papers, notes, PDFs, and user-provided context
├── STATUS.md             # Human-readable project phase and current state
├── tasks/
│   └── TASK_POOL.json    # Task frontier used by the agent loop
├── memory/
│   ├── facts/            # Durable facts and verification state
│   └── family/           # Evolve-family memory, on family roots
├── results/              # Final answer artifacts
├── generalize/           # Evolve state such as EVOLVE.json
├── docs/
│   └── OPERATOR.md       # Project-specific operator notes
└── .iteris/
    └── INDEX.md          # Monitor routing index
```

Optional papers, notes, or PDFs can be added to `references/` after project
creation.

## Dashboard

Run the local dashboard from an Iteris project:

```bash
iteris dashboard
```

The dashboard installs UI dependencies on first use, builds the React client
when needed, starts a loopback Fastify server, and opens the log view in your
browser. Use `--port` to choose a preferred port and `--no-open` to avoid
opening a browser.

## Evolve Families

After a project has a verified result, evolve can explore generalization
directions under a budget:

```bash
iteris evolve init . --goal "push the result to the most general setting" \
  --budget-hours 72 --max-concurrent 2
iteris evolve run
iteris evolve status
iteris evolve veto <direction-id>
iteris evolve report
iteris evolve stop
```

Use `iteris dashboard` on the family root to inspect the evolve tree and
direction pool.

## Common Commands

| Command | Use |
| --- | --- |
| `iteris monitor` | Interactive setup, project help, and supervision. |
| `iteris doctor` | Environment and project health checks. |
| `iteris new --source ...` | Create a structured Iteris project. |
| `iteris run` | Start the main agent loop. |
| `iteris status` | Summarize project state. |
| `iteris recover` | Reconcile dead sessions or orphaned work. |
| `iteris dashboard` | Launch the local web UI. |
| `iteris evolve ...` | Manage generalization families. |
| `iteris help all` | Full command guide. |

Lower-level commands used by agents and operators live under `iteris tool ...`.

## Install Details

The installer checks system tools, creates an isolated Python environment when
one is not already active, installs Iteris, and best-effort installs supported
agent CLIs. It may also install Node 22 through nvm when the active Node version
is too old for the dashboard. Set `ITERIS_SKIP_SYSTEM_DEPS=1`,
`ITERIS_SKIP_NODE_20=1`, or `ITERIS_SKIP_CLAUDE=1` to opt out of those steps.

To prefer Claude Code by default:

```bash
export ITERIS_EXECUTOR=claude
```

## Documentation

- [User guide](docs/user-guide.md)
- `iteris help monitor`
- Project-local `docs/OPERATOR.md` and `.iteris/INDEX.md`

## Development

```bash
python -m pip install -e ".[dev]"
pytest
bash -n install.sh scripts/deploy.sh

cd src/iteris/ui/server && npm ci && npx tsc --noEmit
cd ../client && npm ci && npm run build
```

Build release artifacts from a clean checkout:

```bash
python -m build
twine check dist/*
```

## Citation

If you use Iteris in research, please cite:

```bibtex
@misc{chen2026iterisagenticresearchloops,
      title={Iteris: Agentic Research Loops for Computational Mathematics},
      author={Leheng Chen and Zihao Liu and Wanyi He and Bin Dong},
      year={2026},
      eprint={2606.02484},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2606.02484},
}
```

## License

Apache-2.0. See [LICENSE](LICENSE).
