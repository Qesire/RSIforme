# RSIforme

把真实程序优化组织成可恢复、可测量、可交付的研究循环。RSIforme 的持久控制器组织基线、profiling、编码 worker、历史反馈、预算、固定评价与确认；候选档案保存精确源码和证据。生产任务的 Work/Judge、容器与 Slurm 调度接入固定版本的 **RSI-Harness**。

工程设计参考 ALE 的候选历史和版本发布、NVIDIA Sol-Engine 的目标合同与完整复测，以及 RSI-Harness 的独立评价。SoL-Pi、ERNIE Evolve 提供研究线组织的参考。具体来源、版本和公开边界见 [业界流程](docs/INDUSTRY_WORKFLOWS.md)，实际对齐程度见 [参考工程验收](docs/REFERENCE_PARITY.md)。

## 当前验证到哪里

| 范围 | 已有证据与当前状态 |
| --- | --- |
| v0.2 持久控制器、本地评价与交付 | 两轮 scripted worker 工程演练完成；第一轮改进、第二轮无效，恢复较早的有效候选并新做确认，导出后独立校验和复查成功。worker 提案来自预写 fixture，模型调用为 0。见 [实施记录](docs/IMPLEMENTATION_RECORD.md)。 |
| v0.2 精确核心提交的 CPU CI | `419abfcc5c752477b848b94d8a42ca8f07e01682` 的 [Actions 45925333216](https://github.com/Qesire/RSIforme/actions/runs/45925333216) 已在 Python 3.11、3.12 全部通过：各 81 项通过、4 项原生依赖测试明确 skip；资产检查、CPU demo、持续控制器和 portable reproduction 均通过。 |
| v0.1 原生 Work/Judge | 提交 `5ac7e8b2af6697777650af5abee7ee02df2da0c1` 的 [Actions 45922452679](https://github.com/Qesire/RSIforme/actions/runs/45922452679) 已实际通过 Docker Work/Judge、原生提交、故障恢复与确认。候选同样为预写。 |
| v0.2 原生 Work/Judge | 同一核心提交的 [Actions 45925333312](https://github.com/Qesire/RSIforme/actions/runs/45925333312) 已通过：4 项真实 compiler 测试、原生 oracle 与 scripted 包装器，以及执行证明、追加证据、晚到 verifier 产物和独立发布校验。真实故障注入是丢失客户端返回；客户端等待超时另有单元测试。 |
| 两轮真实模型 worker | **授权已落实，实际运行在客户端初始化阶段失败，G3/P0 仍待验证。** 会话 `research-784a05ab0f1544fa` 已完成 baseline/profile；首轮 Codex worker 因只读文件系统退出，未产生可采纳提案或新候选评价。原 900 秒期限现已到期，未启动第二轮。实际记录、已确认范围与验收条件见 [待验证记录](docs/MODEL_VALIDATION_PENDING.md)。 |
| A100 / Slurm / 生产推理收益 | 尚未执行相应目标任务；CPU 流程结果不证明这些能力。 |

v0.2 已补上持续研究的控制工程，尚不能声称完整达到参考系统的研究能力。真实模型消费反馈并继续研究的 P0 门槛仍然开放。

## 先运行本地闭环

在 POSIX 系统、Python 3.11+ 环境中，从仓库根目录运行。安装需要已有 `setuptools>=68`；核心运行时没有第三方 Python 依赖。

```bash
python -m pip install -e . --no-build-isolation
python -m rsiforme demo --store runs/demo
python -B examples/jsonl_aggregate/run_research.py --store runs/controller-demo
python -m unittest discover -s tests -v
```

Demo 实际执行 JSONL 聚合任务：归档三个预置实现，独立计时并检查正确性，让错误实现留下失败证据，重新打开档案，恢复最好候选，完成另一轮确认评价并生成发布记录。再次运行时使用新的 `--store` 路径，原记录会保留。

`run_research.py` 进一步实际运行 cProfile、持续控制器、两轮历史交接、错误候选拒绝、恢复确认与可搬移发布包；控制器在不同 OS 进程中重新打开状态。它使用明确标注的 scripted worker，不发送模型请求。两种演练的候选均预先编写，检验的是工程交接。CPU 计时包含进程启动、JSONL 读取、聚合和输出，其口径见 [示例合同](examples/jsonl_aggregate/README.md)。

| 运行产物 | 用途 |
| --- | --- |
| `task.json` | 固定任务、目标、环境及评价器身份 |
| `candidates/<id>/` | 精确源码、文件摘要、父代与假设 |
| `evaluations/`、`evidence/`、`attachments/` | 各次评价、封存的原始证据及后续补存附件 |
| `submissions/` | 原生 Harness 提交意图及 run/round 关联 |
| `research/` | 冻结计划、profile、trial 阶段、worker 日志、handoff 和收尾状态 |
| `STATUS.json`、`JOURNAL.md` | 可由原始记录重新生成的研究视图 |
| `releases/`、`CURRENT.json` | 通过确认评价的发布身份 |
| `REPORT.md`、`demo_result.json` | Demo 的实际执行摘要 |
| `research-result.json` | scripted 控制器演练的真实记录与显式的 `model_calls=0` |

## 已实现的能力

| 能力 | 当前范围 |
| --- | --- |
| 候选归档与恢复 | 显式文件范围、内容核查、父代、lane、假设；恢复到新目录 |
| 固定任务上下文 | 任务或环境变化时使用新的档案；本地评价器和声明的依赖按 SHA-256 冻结 |
| 持久研究控制 | 有效基线、实际 profile、结构化 worker、历史 handoff、trial/评价/walltime 预算、超时与显式恢复 |
| 父代与方向 | 默认 best-valid-search 父代，固定 lanes 顺序轮转，也可显式指定；worker 在当前线内依据历史提出修改 |
| 评价与选择 | 保留失败，使用同次评价的指标；确认评价不进入搜索排名 |
| 发布与交付 | 新执行确认绑定同一候选和 search；已有发布要求改善。导出精确代码、选定原始证据、独立校验和本地复查程序 |
| Harness 接口 | 原生提交包装、未知提交恢复、按准确轮次导入结果、命令生成 |
| CPU 任务包 | 本地示例及 Harness 格式的 `tasks/jsonl-aggregate` |
| 研究指令 | `program.md`、冻结 program、任务/假设/复现模板与标准化提案协议 |

自动新建研究线、合并获胜分支和改写研究器策略尚未实现。选择一个最小单控制器是当前实现范围，不代表其已经覆盖 ALE、Sol-Engine 等参考系统的全部能力。控制器使用方法见 [RESEARCH_LOOP](docs/RESEARCH_LOOP.md)。

## 接入自己的任务

先编辑 [configs/task.example.toml](configs/task.example.toml)，填写任务版本、评价器摘要、真实环境、指标和允许归档的文件。它是 **RSIforme schema 1**；Harness 的 `task.toml` 是另一份配置。模型、数据和大型缓存使用固定引用。

```bash
python -m rsiforme init --store runs/my-task --task configs/task.example.toml
python -m rsiforme capture --store runs/my-task --workspace /path/to/workspace --lane baseline --hypothesis "建立固定负载的基线"
```

`capture` 输出候选 `id`。本地可信评价器提供 `evaluate_candidate(snapshot_dir, output_dir)`，返回状态、有效性、指标和独立执行 ID；`evaluate` 冻结其代码和声明的依赖，在受超时约束的独立进程运行，再封存证据。完整实现可参考示例的 `evaluate.py`。

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
| `research-init` / `research-prepare` | 冻结研究计划与基线 profile / 建立父代工作区和 handoff |
| `research-run` / `research-status` | 持续执行或恢复 / 查看阶段、历史和预算 |
| `research-resolve` | 核查后接受或放弃未知 worker 输出；`--abandon-evaluation` 放弃未知评价并保留预算消耗 |
| `research-finalize` | 恢复选中源码，完成新的 confirmation 并尝试晋升 |
| `export` / `verify-release` | 导出已晋升结果 / 校验可搬移发布包 |
| `report` | 从保存的记录生成 STATUS 和 JOURNAL |
| `harness-command` | 生成原生运行或恢复命令，不执行作业 |

各命令的完整参数使用 `python -m rsiforme <命令> --help` 查看。原生 `best_rewards` 可能来自多个候选；适配器按 `best_round` 找到该轮完整指标。提交结果不确定时保留 pending 状态，再从同一 run 的历史恢复，避免重复消耗评价预算。

下一步按 [路线图](docs/ROADMAP.md)，处理客户端运行时初始化问题。本次会话已于 2026-11-27 05:44:31.525519 UTC（北京时间 13:44:31）到期；尚未使用的 worker 数量不构成可恢复额度。未来新实验须另记范围、预算及环境，保留本次失败记录，不能用新目录隐式重置旧会话。相同材料的传输许可已经落实。实际入口及无模型预检命令见 [研究循环](docs/RESEARCH_LOOP.md#公开-jsonl-任务的有限验收入口)。目标 GPU 任务另建硬件基线。开发时阅读 [系统架构](docs/ARCHITECTURE.md) 与 [研究执行指令](program.md)。
