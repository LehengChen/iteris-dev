# Iteris `family` 功能 — 测试交付说明

**面向：** 测试 / 合作 operator  
**版本：** feat/family-closure PR  
**预计耗时：** 30–45 分钟（不含长时间 agent run）

---

## 这版改了什么（人话）

以前并行推进一组相关 North-Star 题（比如 2.15–2.19），只能每题手工 `iteris run`，session 名乱、不知道谁在跑、verified 事实也不好互引。

这版在 Iteris core 加了 **`iteris family`**：

1. **建族**：一个 family 根目录 + N 个 sibling 子项目  
2. **共享池**：某 sibling 里 verified 的 fact 可以 export 给其它题引用  
3. **联合调度**：按 `max_concurrent` 自动补开各 sibling 的 `/goal` loop  
4. **跑 loop 时自动注入**：prompt 里会写「你是哪题的 sibling、共享池在哪、引用前要 re-verify」

和 **`iteris evolve`** 不是一回事：evolve 是从一个 verified 结果泛化出多个 direction；family 是多个 sibling **并行闭合原题**，必要时互相 cite。

---

## 测试前准备

```bash
cd /path/to/Iteris
bash scripts/deploy.sh          # 装最新 CLI
iteris --version
which tmux                      # family 调度依赖 tmux
```

确认 `iteris family --help` 能列出子命令：`new`, `init`, `export`, `pool`, `status`, `schedule`, `start`, `run`, `stop`。

---

## 测试 1：从零建族（smoke）

在临时目录执行（不要污染生产 family）：

```bash
mkdir -p /tmp/family-smoke/sources
cat > /tmp/family-smoke/sources/p1.md <<'EOF'
# Toy Problem A
Prove X.
EOF
cat > /tmp/family-smoke/sources/p2.md <<'EOF'
# Toy Problem B
Prove Y.
EOF
cat > /tmp/family-smoke/manifest.json <<'EOF'
{
  "goal": "Close toy problems A and B.",
  "schedule": { "max_concurrent": 2 },
  "siblings": [
    {
      "sibling_id": "A",
      "path": "child-a",
      "source": "/tmp/family-smoke/sources/p1.md",
      "north_star": "Prove X.",
      "target_artifact": "results/a/answer.md",
      "claim_prefix": "North-Star full closure of Problem A:",
      "session": "iteris-smoke-a"
    },
    {
      "sibling_id": "B",
      "path": "child-b",
      "source": "/tmp/family-smoke/sources/p2.md",
      "north_star": "Prove Y.",
      "target_artifact": "results/b/answer.md",
      "claim_prefix": "North-Star full closure of Problem B:",
      "session": "iteris-smoke-b"
    }
  ]
}
EOF

cd /tmp/family-smoke
iteris family new . --manifest manifest.json
```

**预期：**

- 生成 `.iteris/FAMILY.json`
- 生成 `child-a/`、`child-b/`，各有 `iteris.toml`、`.iteris/watchdog_goal.txt`、`.iteris/family.json`
- 生成 `memory/family/` 目录（共享池）

---

## 测试 2：`family status` 不报错

```bash
cd /tmp/family-smoke
iteris family status
iteris family status --json | python3 -m json.tool | head -30
```

**预期：**

- 不出现 `NameError: has_family_state`
- 表格显示 2 个 sibling，phase 为 `open`，session 为 `stopped`（尚未 schedule）

---

## 测试 3：调度 dry-run 与真启动

```bash
iteris family schedule --dry-run --json
iteris family schedule --json    # 可选：真启动 tmux（需 codex 环境）
iteris family status
```

**预期：**

- dry-run 返回将要启动的 sibling 及完整 `--goal` 文本
- 真 schedule 后（若环境允许）对应 `iteris-smoke-a/b` session live
- `max_concurrent=2` 时两个 open sibling 都会被拉起

停止：

```bash
iteris family stop
tmux kill-session -t iteris-smoke-a 2>/dev/null
tmux kill-session -t iteris-smoke-b 2>/dev/null
```

---

## 测试 4：共享池 export / search

在 `child-a` 里写入一条 verified fact（或用项目里已有 fact），然后：

```bash
cd /tmp/family-smoke
# 若 smoke 项目尚无 fact，可跳过；有 verified fact 时：
iteris family export . --from child-a --fact-id '<fact:id>'
iteris family pool
```

在 `child-b` 目录：

```bash
cd child-b
iteris tool memory search . --query "..." --json
```

**预期：**

- `family pool` 显示导出条目
- search 结果含 `scope: family`，并带 re-verify 提示

---

## 测试 5：prompt 注入

```bash
cd /tmp/family-smoke/child-b
python3 -c "
from pathlib import Path
from iteris.commands.goal.prompt import build_project_context_lines
print(''.join(build_project_context_lines(Path('.')))[:600])
"
```

**预期：** 输出含 `Family closure context`、`sibling \`B\``、共享池路径说明。

---

## 测试 6：登记已有 layout（`family init`）

若已有 sibling 目录（symlink 或子文件夹），可不 `new` 而 `init`：

```bash
iteris family init /path/to/existing-family \
  --goal "Family headline" \
  --sibling id=2.15,path=prob-2-15,session=iteris-prob-215 \
  --max-concurrent 5
```

**预期：** 写 `FAMILY.json` + 各 sibling 的 `.iteris/family.json`。

---

## 测试 7：自动化回归

```bash
cd /path/to/Iteris
python -m pytest tests/test_family_closure.py tests/test_family_state.py tests/test_family_memory.py -q
```

**预期：** 全部通过（当前 14 tests）。

---

## 可选：真实题组 smoke（Iteris-proj）

仓库外已有 operator 搭好的 family（若你环境里有）：

```bash
cd /AI4M/users/lhchen/projects/Iteris-proj/krylov-fp-2-15-19-family
iteris family status
# 五题全开时 max_concurrent=5，应显示 5 running
```

此目录 **不在本 PR 内**，仅作集成参考；测试同学没有该路径可跳过。

---

## 已知限制（测完请确认能接受）

| 项 | 说明 |
|----|------|
| `family align` | **未实现**（Round push / freeze 仍手工脚本） |
| promote 自动 export | **未实现**，需手动 `family export` |
| `closed` vs full closure | status 的 `closed` 是机械 `goal_success`；与 operator 定义的 Round 2 full closure 可能不一致 |
| `iteris run` 读 watchdog 文件 | schedule/start 会传 `--goal`；单独 `iteris run` 不自动读 `watchdog_goal.txt` |
| PDF 操作指南 | `docs/iteris-family-operator-guide.pdf` 随 PR 附带；改 tex 后跑 `docs/build-family-guide.sh` |

---

## 通过标准（建议）

- [ ] `iteris family new/init/status/schedule/pool/export/stop` 均可执行且无 Python traceback  
- [ ] `family status --json` 输出合法 JSON  
- [ ] pytest family 相关 14 项全绿  
- [ ] sibling run 的 prompt 含 family 上下文块  
- [ ] memory search 默认合并 family 池（`scope: family`）  
- [ ] 与 `iteris evolve` 命令、数据目录无冲突（同一项目不要混用两种 family 模型）

---

## 文档

- 合作者操作指南（PDF）：`docs/iteris-family-operator-guide.pdf`  
- Manifest 模板：`src/iteris/data/templates/family_manifest.example.json`

有问题在 PR 下留言或 @ 开发。
