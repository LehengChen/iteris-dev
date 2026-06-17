<h1 align="center">Iteris</h1>

<p align="center">
  <strong>Frenzymath · PKU @ AI4Math</strong><br>
  面向计算数学的 agentic research loops
</p>

<p align="center">
  <a href="README.md">English README</a>
  · <a href="https://frenzymath.com/blog/iteris/">Blog</a>
  · <a href="docs/user-guide.md">用户指南</a>
  · <a href="#快速开始">快速开始</a>
  · <a href="#权限与数据边界">权限与数据边界</a>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.0-blue">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-green">
</p>

Iteris 是一个面向研究工作的 goal-driven agent workspace toolkit。它把题目材料、长期事实、任务池、验证记录、运行日志和最终结果组织在项目本地目录中，让研究者可以监督长期运行的 agentic research loop。

它围绕三个常用入口组织：

| 入口 | 用途 |
| --- | --- |
| `iteris monitor` | 交互式安装、项目引导、恢复运行和下一步规划。 |
| `iteris run` | 在项目工作区内运行 Codex 或 Claude Code research loop。 |
| `iteris dashboard` | 本地 Web UI，用于查看日志、事实、产物和 evolve family 状态。 |

## 快速开始

从源码 checkout 做完整本地安装：

```bash
bash install.sh
```

然后创建一个独立项目目录，并从 monitor 开始：

```bash
mkdir -p ./MyProblem
cd ./MyProblem
iteris monitor
```

Monitor 可以引导环境检查、创建项目、恢复运行、打开 dashboard 和管理 evolve family。如果想手动执行：

```bash
iteris new --source /path/to/problem.tex
iteris run
iteris dashboard
```

## 环境要求

- Python 3.10+
- Git 和 ripgrep
- tmux，用于后台 detached runs；也可以使用 `iteris run --foreground`
- Node.js 20+ 和 npm，用于 `iteris dashboard`
- Codex (`codex`) 或 Claude Code (`claude`)

主要支持 Linux 和 macOS。安装后先运行一次 `codex` 或 `claude` 完成登录授权。

## 权限与数据边界

Iteris 会以较高权限启动项目 agent，使其能够在项目工作区内读写文件、运行工具并维护项目状态。Codex 启动时会使用 approval-bypass 模式，Claude Code 启动时会使用 `--dangerously-skip-permissions`。

请只在你愿意授权 agent CLI 访问的目录中使用 Iteris。不要在包含无关隐私数据、凭证或不希望 agent 读取的文件目录中运行。项目题目、参考资料和运行上下文可能会通过所选 agent CLI 发送给对应服务提供方。

## 项目结构

```text
MyProblem/
├── sources/              # 原始题目材料
├── references/           # 论文、笔记、PDF 和用户补充上下文
├── STATUS.md             # 当前项目阶段和状态摘要
├── tasks/
│   └── TASK_POOL.json    # agent loop 使用的任务池
├── memory/
│   ├── facts/            # 长期事实与验证状态
│   └── family/           # family root 上的 evolve 跨项目记忆
├── results/              # 最终结果文件
├── generalize/           # evolve 状态，例如 EVOLVE.json
├── docs/
│   └── OPERATOR.md       # 项目特定操作说明
└── .iteris/
    └── INDEX.md          # monitor 路由索引
```

项目创建后，可以把论文、笔记或 PDF 放入 `references/`。

## Dashboard

在 Iteris 项目目录中启动本地 dashboard：

```bash
iteris dashboard
```

首次使用时 dashboard 会安装 UI 依赖，必要时构建 React client，启动本地 loopback Fastify server，并在浏览器中打开日志视图。可以用 `--port` 指定端口，用 `--no-open` 禁止自动打开浏览器。

## Evolve Family

当一个项目已有 verified result 后，可以用 evolve 在预算内探索 generalization 方向：

```bash
iteris evolve init . --goal "push the result to the most general setting" \
  --budget-hours 72 --max-concurrent 2
iteris evolve run
iteris evolve status
iteris evolve veto <direction-id>
iteris evolve report
iteris evolve stop
```

在 family root 上运行 `iteris dashboard` 可以查看 evolve tree 和 direction pool。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `iteris monitor` | 交互式安装、项目帮助和监督入口。 |
| `iteris doctor` | 环境与项目健康检查。 |
| `iteris new --source ...` | 创建结构化 Iteris 项目。 |
| `iteris run` | 启动主 agent loop。 |
| `iteris status` | 查看项目状态。 |
| `iteris recover` | 恢复 dead session 或 orphaned work。 |
| `iteris dashboard` | 启动本地 Web UI。 |
| `iteris evolve ...` | 管理 generalization family。 |
| `iteris help all` | 完整命令指南。 |

更底层的 agent/operator 工具位于 `iteris tool ...`。

## 安装细节

安装脚本会检查系统工具；如果当前没有激活 Python 环境，会创建隔离的 Python venv 并安装 Iteris；还会尽力安装支持的 agent CLI。如果当前 Node 版本太旧，脚本可能通过 nvm 安装 Node 22 以支持 dashboard。可以用 `ITERIS_SKIP_SYSTEM_DEPS=1`、`ITERIS_SKIP_NODE_20=1` 或 `ITERIS_SKIP_CLAUDE=1` 跳过对应步骤。

如果默认想使用 Claude Code：

```bash
export ITERIS_EXECUTOR=claude
```

## 文档

- [用户指南](docs/user-guide.md)
- `iteris help monitor`
- 项目本地的 `docs/OPERATOR.md` 和 `.iteris/INDEX.md`

## 开发

```bash
python -m pip install -e ".[dev]"
pytest
bash -n install.sh scripts/deploy.sh

cd src/iteris/ui/server && npm ci && npx tsc --noEmit
cd ../client && npm ci && npm run build
```

发布包应从干净 checkout 构建：

```bash
python -m build
twine check dist/*
```

## 引用

如果你在研究中使用 Iteris，请引用：

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

## 许可证

Apache-2.0。见 [LICENSE](LICENSE)。
