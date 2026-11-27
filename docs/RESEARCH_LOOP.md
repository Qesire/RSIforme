# 有预算、可恢复的研究循环

`ResearchController` 把已测基线、profiling、研究线、编码进程、冻结评价器和晋升记录接成一个持久流程。每轮都从已捕获的真实候选出发，把本轮结果写回归档，下一轮据此选择父代并接收历史。调度策略目前是显式的有限规则：研究线按顺序轮转，默认父代是同一冻结任务中的最佳有效搜索结果。

入口为 `src/rsiforme/research.py`。本地执行调用 `evaluate_local`；原生执行调用现有 `submit`，由 RSI-Harness 负责 Work 快照、独立 Judge 与其运行资源。控制器没有另建一套容器或 Slurm 调度器。

## 运行前的两个实测输入

先用 `init → capture → evaluate` 或原生 `submit/import-harness` 建立有效的 **search 基线评价**。该评价须为 `completed`、`valid=true`，具有执行身份与起止时间，其原始证据必须已封存且通过哈希校验。缺少执行证明的旧基线需要重新测量。

另准备实际 profiling 的 JSON 文件，至少包含以下字段：

| 字段 | 内容与用途 |
| --- | --- |
| `source_digest` | 被分析基线候选的精确源码摘要 |
| `context_fingerprint` | 与基线评价一致的冻结任务摘要 |
| `command` | 实际执行的分析命令；可保存为 argv 数组 |
| `findings` | 从实际输出得到的观察，例如耗时集中位置、重复调用或分配；同时记录测量范围与限制 |

可另外保留原始 profiler 输出的路径或摘要。`initialize` 核对两个身份字段，并要求命令与发现均非空。它不会替用户产生 profiling，也不会验证一段自由文本是否构成可靠的性能解释。写入的观察应能追溯到真实输出。

仓库的 JSONL 聚合示例另提供实际 cProfile 入口。将候选 ID 换成已测基线的真实 ID：

```bash
python -B examples/jsonl_aggregate/profile_baseline.py --store runs/my-task --candidate c-BASELINE_ID --output runs/baseline-profile.json
```

该脚本对固定 workload 的 `aggregate(records)` 函数做分析、核对结果并写入 profile JSON。其耗时包含 profiler 开销，分析范围也小于正式评分的完整输入输出流水线；handoff 同时提供两种观察，避免把热点函数的变化直接等同于端到端收益。

研究配置、基线 profile、任务 context 在初始化后冻结。改变目标、评价器、运行环境或研究计划，应建立对应的新归档或研究会话；直接改写冻结文件会被拒绝。当前一个归档只有一个研究会话。

## Codex worker 模板

`configs/research.codex.example.json` 是一个两条研究线、至多两次 trial 的本地模板。其 worker argv 为：

```json
["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--json", "--output-schema", "{schema}", "--output-last-message", "{proposal}", "-"]
```

本机只读检查观察到 `codex-cli 0.172.0-alpha.2`；该版本 `codex exec --help` 确认以上参数以及 `-` 从 stdin 读取提示的行为。控制器通过 argv 启动进程，工作目录为本轮恢复出的源码目录，将任务提示写入 stdin，将 stdout/stderr 留在 trial 目录。`--json` 的事件流保存在 stdout 日志；最终结构化提案由 `--output-last-message` 写入 `{proposal}`。

**本轮限定授权已落实，真实模型验收仍待完成。** 用户确认了经 `ab.chatgpt.com` 传输公开 JSONL 任务限定材料、最多两轮 Codex worker 的范围。会话 `research-784a05ab0f1544fa` 的 baseline 和 profile 已实测；`trial-0001` 在 in-process app-server 初始化时因只读文件系统退出，控制器保存为 `worker_failed`，没有可采纳提案或新候选评价。已消耗 1 次 worker、1 次 baseline 评价，失败尝试计入原预算。原 900 秒 walltime 已于 2026-11-27 05:44:31.525519 UTC（北京时间 13:44:31）到期；未使用的 1 次 worker 数量不构成可恢复额度。实际状态及授权前审批拒绝的历史记录见 [MODEL_VALIDATION_PENDING](MODEL_VALIDATION_PENDING.md)。下文是接口用法，不代表两轮模型反馈验收已经通过。

当前已确认 app-server 需要写入 `CODEX_HOME/installation_id`，只读环境无法满足该前置条件；没有启动第二轮 worker。无模型诊断与实际失败 trial 分别保存；源码依据和没有 syscall trace 的证据边界见 [运行时验收记录](MODEL_RUNTIME_VALIDATION.md)。

worker 使用执行环境已有的 CLI 授权、连接和默认模型配置；模板不覆盖模型，也不保存登录材料。开始实际研究前，应在该执行环境完成所需 CLI 登录与服务配置。CLI 存在且参数可解析，只证明调用入口可用；是否完成真实模型轮次，需要查看本轮进程日志、提案、源码变更和后续评测记录。

控制器支持四个 argv 占位符：`{workspace}`、`{handoff}`、`{schema}`、`{proposal}`。自定义编码进程也可以使用相同协议。`proposal.schema.json` 声明五个字段：

| 字段 | 含义 |
| --- | --- |
| `decision` | `evaluate`：捕获修改并送固定评测；`stop`：结束本 trial |
| `hypothesis` | 这次修改要检验的具体假设 |
| `change_summary` | 已经发生的源码变化 |
| `evidence_used` | 支持该假设的实际 profile 发现或历史记录引用，必须非空 |
| `next_direction` | 后续可检验方向；它作为记录保留，不会自动重写研究线配置 |

这些输入输出定义了一个可接入真实编码 Agent 的工作流。模板本身不构成 Agent 自动发现改进、完整原生 Work/Judge 成功或 GPU 性能收益的证据；实际完成的运行应单独记录。

## 公开 JSONL 任务的有限验收入口

在仓库根目录执行本地预检：

```bash
PYTHONPATH=src python -B examples/jsonl_aggregate/run_model_research.py --preflight-only
```

该模式只检查 CLI 版本、登录状态和状态目录元数据，不启动 app-server、worker 或正式评价，不创建研究归档，也不提交模型 prompt。当前只读状态目录不能通过这个检查。返回 `0` 仅表示这些本地条件通过，不证明 app-server 启动、网络连接或模型可用；返回 `2` 表示预检或参数检查失败。

在正常可写的 Codex 环境，另行记录新实验的范围、预算与环境后，可使用独立的新归档：

```bash
PYTHONPATH=src python -B examples/jsonl_aggregate/run_model_research.py --store ../rsiforme-model-run
```

`--store` 必须是仓库及其祖先目录之外、尚不存在的路径。入口不覆盖或恢复旧归档；新目录不将本次失败尝试清零，也不使已过期的 900 秒预算重新生效。相同材料的传输许可已经落实，新实验的预算仍须单独记录。

执行路径按配置建立 baseline/profile、最多两轮 worker、固定评价与独立确认，然后导出并校验交付。worker 失败时立即保留记录并停止，不自动再调用下一轮。可加 `--reproduce` 请求一次交付包独立复查；成功执行后，在四次研究评价之外单列。如果中断且无法确认评价是否启动，保存 attempt 和 unknown，不能按 0 次处理。该选项不能与 `--preflight-only` 同用。

完整执行返回 `3` 表示实验未完成或运行/验证失败；返回 `0` 对应 `completed_pending_evidence_review`。即使返回 `0`，G3 仍需人工核对真实模型提案、第二轮消费的反馈、源码变更与确认记录，入口不会自动关闭它。**当前没有执行新 campaign，也没有实测此入口完整的两轮模型与交付路径。**

## CLI：初始化、执行、确认和交付

下例假设已安装本项目，`runs/my-task` 已有基线评价，`runs/baseline-profile.json` 包含其真实分析记录。将 `e-BASELINE_ID` 替换为实际基线评价 ID：

```bash
python -m rsiforme research-init --store runs/my-task --program configs/research.codex.example.json --baseline-evaluation e-BASELINE_ID --profile runs/baseline-profile.json
python -m rsiforme research-run --store runs/my-task
python -m rsiforme research-status --store runs/my-task
python -m rsiforme research-finalize --store runs/my-task
python -m rsiforme export --store runs/my-task --to deliveries/release-001
python -m rsiforme verify-release --directory deliveries/release-001
```

`research-run` 继续保存状态中的未完成 trial，然后在预算内准备新 trial。它不会把 worker 写出的“更快”当成测量；评价器接受候选后，才比较该次评价与选定父代的目标值，并记录 `improved` 或 `not_improved`。无效结果和基础设施错误分别记录，源码未变时记录 `unchanged_source` 并复用父代评价，不花费一次新的正式评测。

如果先检查交给 Agent 的材料，可以单独 prepare。`--lane` 与 `--parent` 均可省略；指定父代时需要该候选已有有效搜索评价：

```bash
python -m rsiforme research-prepare --store runs/my-task --lane algorithm --parent c-PARENT_ID
python -m rsiforme research-run --store runs/my-task --max-new-trials 0
```

`--max-new-trials` 只限制**本次调用新建**的 trial，不包含恢复的已有 trial。所以上述 `0` 会执行已准备的 trial，并在它完成后停止；直接 `research-run --max-new-trials 1` 则允许额外创建一个新 trial。

`research-finalize` 选择当前最佳有效 search，恢复其精确源码，另开一次绑定到该 search 的 confirmation，随后尝试 promotion。即使没有搜索改进，当前最佳也可能仍是基线。确认不通过质量门槛，或确认结果未优于已有发布时，会留下 `rejected` 记录。确认记录、晋升与导出分别存在；搜索榜首不直接成为发布。交付目录的独立校验与实际复查见 [DELIVERY.md](DELIVERY.md)。

## 选择策略与预算的准确含义

| 规则 | 当前实现 |
| --- | --- |
| 默认父代 | `Archive.best()`：只比较有效且完成的 search，以任务的 `maximize/minimize` 方向选择；相同目标值保留较早观察 |
| 显式父代 | `prepare(parent_id=...)` 选择指定候选，以该候选最近一次有效 search 作为本轮对照 |
| 默认研究线 | 依据已创建 trial 数在配置 `lanes` 中轮转；失败或停止的 trial 也计入顺序 |
| 显式研究线 | `prepare(lane_id=...)` 选择配置中已有的线；不创建新线 |
| `max_trials=2` | 最多创建两次 trial；worker 失败、被放弃或主动停止也占一个名额 |
| `max_evaluations=4` | 统计归档内全部评价，包含已有基线、无效结果及 confirmation；已派发但结果未知、经人工核查后放弃的评价仍记一次消耗。默认起点只有一次基线时，允许两次 search 加一次独立 confirmation |
| 保留确认名额 | 准备 trial、启动 worker 及正式派发 search 前，要求“已消耗评价数 + 本次潜在评价 + 一次确认”不超过总额 |
| `walltime_seconds=900` | 从 research 初始化时间开始计算，包含停止控制器和人工检查期间的时间；耗尽后不再启动新的 worker、search 或 confirmation |
| `worker_timeout_seconds=180` | 每个编码进程的实际超时上限，并与剩余 walltime 取较小值；超时先终止进程组，必要时强制结束 |

本地 search 与 confirmation 把剩余 walltime 传给 `evaluate_local`，由独立进程超时约束评价执行。原生提交把剩余时间用于客户端等待；等待超时保留 pending，不重发，也不能据此断言远端 Judge 没有运行。Work 暂停时，本地计时未必继续推进；远端计算仍由 Harness task deadline 约束。900 秒不是包含全部复制、刷新和清理、或能硬终止远端 Judge 的全局截止时刻。模型 token 费用也不在这个计数器中。状态输出包含已记录评价、放弃的未知派发、总消耗、预留数与剩余 walltime。

同一归档内其他进程追加的正式评价也计入 `max_evaluations`。一个已经跑完 baseline、两个搜索候选及一次 confirmation 的演示归档已经用完模板的四次预算；新的研究应从独立测量的基线和相应配置开始。

## 每轮 handoff 与保留材料

`prepare` 恢复父代后，在工作区写入 `handoff.json` 和 `proposal.schema.json`。handoff 包含冻结任务、当前研究线、预算、父代源码清单与评价、基线评价、绑定过身份的 profile、此前全部 trial/candidate/evaluation 记录、允许修改的 `source_paths` 以及归档只读路径。历史包含失败与未改进结果，因此下一次 worker 能看到已经验证或排除过的方向。

状态与原始材料位于归档的 `research/` 下：

| 路径 | 作用 |
| --- | --- |
| `config.json`、`profile.json` | 冻结研究计划和基线分析 |
| `trials/trial-NNNN/state.json` | 持久阶段、父代、研究线、进程身份、候选和评价关联 |
| `trials/trial-NNNN/work/` | 恢复出的父代、handoff、schema、worker 提案与修改 |
| `trials/trial-NNNN/worker-prompt.txt` | 实际写入 worker stdin 的提示 |
| `trials/trial-NNNN/worker.stdout.log`、`worker.stderr.log` | 原始进程输出 |
| `finalization.json`、`confirmed-source/` | 独立确认的状态与被恢复的精确候选 |
| `STATUS.json` | 可重建的状态视图 |

源码、正式评价、原始证据与 promotion 仍保存在 Archive 的对应目录。research 状态引用它们，不复制一套彼此可能失配的评分表。

## 单控制器与中断恢复

同一会话用非阻塞文件锁限制为一个活动控制器。worker 启动前写入 `worker_starting`，启动后保存 `worker_running`、PID 和 Linux 进程起始身份。控制器重启时，未决 worker 不会被静默重启；Linux 进程身份用于区分原进程和复用同一 PID 的新进程。

先检查状态、stdout/stderr、源码和提案。确认原 worker 已结束且其输出完整时，可接受该输出继续后续捕获与正式评测：

```bash
python -m rsiforme research-resolve --store runs/my-task --trial trial-0001 --note '已核对本 trial 的日志、proposal 和候选修改；原进程已结束' --accept-worker-output
python -m rsiforme research-run --store runs/my-task --max-new-trials 0
```

如果不接受残留输出，省略 `--accept-worker-output`，该 worker trial 标记为 `abandoned`。`--note` 应写本次真实核查依据。原进程仍在运行时，resolve 会拒绝处理。

评价阶段采用另一条恢复边界：`evaluating` 或 `confirming` 先查找已经落盘的对应执行，不因控制器重启而重新消耗一次评测。原生提交结果不明时，应先在同一个 Harness run 中核对 round，使用已有 `recover` 和 `import-harness` 关联结果，再让研究控制器收敛状态。

对于仍没有评价记录的 `evaluating` trial，只有核实已无活动任务、结果确实无法恢复后，才可显式放弃这次未知派发：

```bash
python -m rsiforme research-resolve --store runs/my-task --trial trial-0001 --note '填写实际核查依据与无活动任务的结论' --abandon-evaluation
```

对应 Python API 为：

```python
controller.resolve("trial-0001", note="填写本次实际检查过的进程、日志和结果位置，以及为何无法恢复结果",
                   abandon_unknown_evaluation=True)
```

它将 trial 标记为 `abandoned` 并保留一次预算消耗，不把未知执行算作从未发生，也不重跑原评价。若已有可匹配的落盘评价，则优先收敛到该结果；原生 run 仍有 pending submission 时，必须先完成其恢复。最终 confirmation 结果未知时继续保持未决，不能通过新 confirmation 覆盖旧身份。

## 原生 Harness 后端的接入范围

将程序中的 backend 改为真实已准备环境，例如：

```json
{
  "type": "harness",
  "workspace": "/actual/allowed/workspace",
  "harness_run_id": "ACTUAL_EXISTING_RUN_ID"
}
```

该配置需要在初始化之前完成，并提供实际 Work 的可访问路径与正确 run ID。控制器必须能在对应 Work 环境调用原生 `rsi-submit`；它不会从外部目录自动启动 Work，也不会从只有 round 的反馈中推导 run ID。

编码 worker 先在 trial 工作区提出修改。捕获候选后，控制器把冻结任务 `source_paths` 中的内容安装到指定 Work：范围内旧文件或目录会由已归档的候选版本替换，范围外内容维持原工作环境。源文件清单必须恰好位于原生任务允许修改与快照包含的范围，研究归档也必须位于持久化路径。完整 Work 镜像、数据、GPU、Judge 以及 Slurm 资源配置仍由现有 Harness 任务提供。

因此，本地研究循环可以先验收 worker、父代选择、证据反馈、确认与交付；原生接入还要在实际 Work 中验证 staging 路径、源码快照范围、run/round 绑定和持久化。对应环境与验收见 [HARNESS_INTEGRATION.md](HARNESS_INTEGRATION.md) 和 [NATIVE_HARNESS_VALIDATION.md](NATIVE_HARNESS_VALIDATION.md)。

## Python API

```python
import json
from pathlib import Path
from rsiforme.archive import Archive
from rsiforme.research import ResearchController

controller = ResearchController(Archive("runs/my-task"))
program = json.loads(Path("configs/research.codex.example.json").read_text())
controller.initialize(program, baseline_evaluation="e-BASELINE_ID", profile="runs/baseline-profile.json")
controller.run(max_new_trials=1)
state = controller.status()
```

其余接口为 `prepare(lane_id=None, parent_id=None)`、`resolve(trial_id, note=..., accept_worker_output=False, abandon_unknown_evaluation=False)` 和 `finalize()`。进程重启后，重新构造相同 Archive 与 ResearchController，然后读取状态并调用相应恢复接口；已经初始化的会话不能重复 initialize。
