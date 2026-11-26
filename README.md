# RSIforme

把真实程序优化组织成可恢复、可测量、可交付的研究循环。RSIforme 保存候选源码、父代、假设和评价证据；生产任务的 Work/Judge、恢复与 Slurm 调度接入固定版本的 **RSI-Harness**。

首版沿用三类已落地的工程经验：ALE 的候选历史和版本发布，NVIDIA Sol-Engine 的目标合同、profiling 与完整复测，以及 RSI-Harness 的独立评价。SoL-Pi、ERNIE Evolve 提供后续研究线组织的参考。具体来源、版本和公开边界见 [业界流程](docs/INDUSTRY_WORKFLOWS.md)。

## 先运行本地闭环

在 POSIX 系统、Python 3.11+ 环境中，从仓库根目录运行。安装需要已有 `setuptools>=68`；核心运行时没有第三方 Python 依赖。

```bash
python -m pip install -e . --no-build-isolation
python -m rsiforme demo --store runs/demo
python -m unittest discover -s tests -v
```

Demo 实际执行 JSONL 聚合任务：归档三个预置实现，独立计时并检查正确性，让错误实现留下失败证据，重新打开档案，恢复最好候选，完成另一轮确认评价并生成发布记录。再次运行时使用新的 `--store` 路径，原记录会保留。

候选由项目预先编写；本例验证流程交接，不测量 Agent 自主发现能力。CPU 计时包含进程启动、JSONL 读取、聚合和输出，其口径见 [示例合同](examples/jsonl_aggregate/README.md)。

| 运行产物 | 用途 |
| --- | --- |
| `task.json` | 固定任务、目标、环境及评价器身份 |
| `candidates/<id>/` | 精确源码、文件摘要、父代与假设 |
| `evaluations/`、`evidence/` | 各次评价、原始输出和环境证据 |
| `submissions/` | 原生 Harness 提交意图及 run/round 关联 |
| `STATUS.json`、`JOURNAL.md` | 可由原始记录重新生成的研究视图 |
| `releases/`、`CURRENT.json` | 通过确认评价的发布身份 |
| `REPORT.md`、`demo_result.json` | Demo 的实际执行摘要 |

## 已实现的能力

| 能力 | 当前范围 |
| --- | --- |
| 候选归档与恢复 | 显式文件范围、内容核查、父代、lane、假设；恢复到新目录 |
| 固定任务上下文 | 任务或环境变化时使用新的档案；本地评价器按文件 SHA-256 绑定 |
| 评价与选择 | 保留失败，使用同次评价的指标；确认评价不进入搜索排名 |
| 发布 | 同一候选需要有效搜索评价和后续确认；已有发布要求确认指标进一步改善 |
| Harness 接口 | 原生提交包装、未知提交恢复、按准确轮次导入结果、命令生成 |
| CPU 任务包 | 本地示例及 Harness 格式的 `tasks/jsonl-aggregate` |
| 研究指令 | `program.md` 和任务/假设/复现模板；方向与父代由使用者或 Agent 选择 |

本仓库的验证范围见 [实施记录](docs/IMPLEMENTATION_RECORD.md)。当前执行环境没有完成容器 Work/Judge、Slurm/A100 或模型 Agent 的端到端运行；这些入口已经准备，站点和真实任务接入仍需实测。

## 接入自己的任务

先编辑 [configs/task.example.toml](configs/task.example.toml)，填写任务版本、评价器摘要、真实环境、指标和允许归档的文件。它是 **RSIforme schema 1**；Harness 的 `task.toml` 是另一份配置。模型、数据和大型缓存使用固定引用。

```bash
python -m rsiforme init --store runs/my-task --task configs/task.example.toml
python -m rsiforme capture --store runs/my-task --workspace /path/to/workspace --lane baseline --hypothesis "建立固定负载的基线"
```

`capture` 输出候选 `id`。本地可信评价器提供 `evaluate_candidate(snapshot_dir, output_dir)`，返回状态、有效性、指标和独立执行 ID；`evaluate` 先核对其 SHA-256，再运行并保存证据。完整实现可参考示例的 `evaluate.py`。

下面的 Python API 示例可以在仓库根目录直接运行；每次选择新的档案和恢复目录：

```python
from pathlib import Path
import platform
from rsiforme.archive import Archive, file_sha
from rsiforme.workflow import evaluate_local

evaluator = Path("examples/jsonl_aggregate/evaluate.py")
archive = Archive("runs/manual")
archive.initialize({
    "schema_version": 1, "task_id": "jsonl-manual-v1",
    "objective": "rows_per_second", "direction": "maximize",
    "source_paths": ["solution.py"],
    "context": {"task_revision": "jsonl-v1",
                "evaluator_revision": "sha256:" + file_sha(evaluator),
                "environment": platform.platform() + "; Python " + platform.python_version()},
})
candidate = archive.capture("examples/jsonl_aggregate/fixtures/baseline",
                            lane="baseline", hypothesis="建立完整执行链的基线")
search = evaluate_local(archive, candidate["id"], evaluator)
archive.restore(candidate["id"], "runs/manual-restored")
confirmation = evaluate_local(archive, candidate["id"], evaluator,
                              kind="confirmation", workspace="runs/manual-restored")
archive.promote(candidate["id"], search["id"], confirmation["id"])
archive.write_views()
```

API 会检查归档源码未改变；恢复目录须不存在。评价器在本地拥有调用者权限，独立子进程示例用于可信候选的集成测试。正式 Agent 工作负载使用 Harness 的执行隔离。

## 进入 Harness 与 Slurm

执行 `bash scripts/bootstrap_harness.sh` 可检出 `configs/upstreams.lock.json` 固定的上游并安装 Harness。锁定版本的 Harness 需要 Python 3.12；RSIforme 外层和示例 Work 镜像支持 Python 3.11。

站点绑定、Work 内安装、提交和恢复步骤见 [接入文档](docs/HARNESS_INTEGRATION.md)。`configs/slurm.example.toml` 使用必须填写的站点变量；先校验计划，再运行 oracle，最后接入实际编码 Agent。A100 需要自己的基线。

| 命令 | 操作 |
| --- | --- |
| `evaluate` | 对归档候选运行固定的本地评价器 |
| `submit` | 在 Work 内归档并调用一次原生 `rsi-submit` |
| `recover` | 核对同一 run 的历史后，补齐未知提交的 round 关联 |
| `import-harness` | 导入已绑定轮次的持久评价记录 |
| `best` / `restore` | 查看最好搜索结果 / 恢复精确候选 |
| `promote` | 绑定搜索评价和确认评价，更新发布记录 |
| `report` | 从保存的记录生成 STATUS 和 JOURNAL |
| `harness-command` | 生成原生运行或恢复命令，不执行作业 |

各命令的完整参数使用 `python -m rsiforme <命令> --help` 查看。原生 `best_rewards` 可能来自多个候选；适配器按 `best_round` 找到该轮完整指标。提交结果不确定时保留 pending 状态，再从同一 run 的历史恢复，避免重复消耗评价预算。

下一步按 [路线图](docs/ROADMAP.md) 完成一个真实任务和站点接入，再运行单条 Agent 研究线。开发时阅读 [系统架构](docs/ARCHITECTURE.md) 与 [研究执行指令](program.md)。
