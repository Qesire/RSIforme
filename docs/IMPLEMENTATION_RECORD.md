# RSIforme 实施记录

## 2026-11-27：v0.2 持久研究控制与可移交结果

本轮新增 `ResearchController`、本地冻结 evaluator 及依赖的独立进程执行、证据封存与追加附件、新执行确认约束和可搬移发布包。研究会话保存基线 profile、方向与父代、worker 提示/日志、每轮 handoff、预算、阶段与恢复决定。默认父代为有效 search 中按任务方向选择的最佳结果，研究线按固定配置顺序轮转。

这些机制已经有真实进程和 scripted worker 的工程验证。**真实模型两轮仍待验证，尚未达到完整参考研究系统的验收水平。** 实际 worker 曾有运行状态和 PID，后续轮询/继续操作被自动审批拒绝，理由是未明确授权向配置的 `ab.chatgpt.com` 入口传输本地上下文。没有取得可采纳结果；进程状态和空日志不足以断言是否曾发生网络请求。拒绝后没有通过重试或替代路径继续。详情见 [MODEL_VALIDATION_PENDING](MODEL_VALIDATION_PENDING.md)。scripted fixture 的 `model_calls=0` 与该未决模型运行分别记录。

### 实际控制器演练

从仓库根目录执行以下命令已完成，退出码为 0：

```bash
python -B examples/jsonl_aggregate/run_research.py --store runs/controller-validation-2026-11-27-final
```

脚本先建立完整 CPU 基线，再对同一源码做真实 cProfile。它创建两轮 trial，并用不同 OS 进程重新打开控制器：第一轮使用已知 optimized fixture，第二轮接收包括第一轮真实评价的 handoff，并使用已知 incorrect fixture。第二轮被固定正确性检查拒绝，控制器恢复第一轮源码完成新一次 confirmation，随后导出并独立校验、复查发布包。提案由脚本提供，`model_calls=0`、`autonomous_discovery=false`。

| 阶段 | 实际状态 | 中位耗时 | 吞吐量 |
| --- | --- | ---: | ---: |
| 原始基线 | 有效 search | 211.497 ms | 189,127.66 行/秒 |
| trial-0001：预写单遍聚合 | completed，`improved` | 73.491 ms | 544,285.56 行/秒 |
| trial-0002：预写负对照 | completed，`invalid` | 不排名 | 不排名 |
| 恢复 trial-0001 后的独立确认 | completed，有效并晋升 | 77.424 ms | 516,637.16 行/秒 |
| 交付目录外的额外复查 | completed，有效 | 76.260 ms | 524,520.35 行/秒 |

上述数值来自本次共享 CPU 的实测。正式计时包含新 Python 进程、JSONL 读取、聚合与输出；profile 只分析聚合函数且含 profiler 开销。它们验证预写候选与控制流程，不代表模型自主发现、GPU 收益或稳定的生产加速比。

| 身份 | 实际值 |
| --- | --- |
| research ID | `research-0166851c9e43482a` |
| 基线评价 | `e-3448895a1192446e` |
| 被选中候选 | `c-0845a0f2bbe645a5` |
| 搜索评价 | `e-eb0fcebb005447a4` |
| 被拒绝的第二轮评价 | `e-d54cea90b51048af` |
| 独立确认评价 | `e-9044dd5d65d44325` |
| 发布记录 | `r-36c2a09ac4d041e8` |
| 发布源码摘要 | `c91a3e68ad7bfd8b93a5da9d98795aa0d64603085ec817c6e61716192b06c0af` |
| 发布 manifest SHA-256 | `91f30c4843abf0ca378991b7508df4f5888661cabb21914d1e1a199de87edc02` |

本次演练预算是 2 trials、4 次正式评价、180 秒 walltime、15 秒 worker 超时；它与供真实 Codex 使用的 900/180 秒模板分开配置。归档最终记录 4 次评价，包含 baseline、两轮 search 和新 confirmation。发布后的独立 reproduction **另外实际执行一次**，保存在新输出目录，不回写原研究预算。发布目录的 59 个被清点文件通过独立校验；复查产生新的评价器执行 ID。

原始摘要在本轮归档的 `research-result.json`；可公开引用的提炼记录见 [controller-v2.json](evidence/controller-v2.json)。发布包保存选定候选及其 search/confirmation 证据；完整 baseline、profile、失败历史和预算仍保存在研究归档与摘要，当前 export 不把整个研究会话打包进去。

### 故障与完整性验证

本轮工程测试覆盖 worker 超时/退出失败后继续、无效源码、准备阶段中断、已经完成的评价重新关联、未知 worker 避免重复启动、放弃未知评价后保留额度、确认恢复和 promotion 幂等恢复。证据测试覆盖封存文件被改写、缺失/新增文件、晚到附件和旧记录缺少完整性证明。交付测试搬移发布目录并删除原档案、原源码与原 evaluator，再用标准库校验和真实执行证明交付不依赖原路径。[控制器测试](../tests/test_research.py) · [档案测试](../tests/test_archive.py) · [交付测试](../tests/test_delivery.py)

portable verifier 已补齐独立核对 `execution.confirmation_of` 和实际执行先后的校验，修补后的 delivery suite 6 项全部通过。随后冻结核心提交 **`419abfcc5c752477b848b94d8a42ca8f07e01682`**，其 [CPU CI 45925333216](https://github.com/Qesire/RSIforme/actions/runs/45925333216) 已完整通过：

| 环境 | 测试门禁 | 其他实际执行 |
| --- | --- | --- |
| Python 3.11，job `140265204341` | 85 项，81 通过、4 项原生依赖明确 skip；14.870 秒 | canonical 资产核查、CPU demo、持久研究演练、portable reproduction 均成功 |
| Python 3.12，job `140265204551` | 85 项，81 通过、4 项原生依赖明确 skip；16.460 秒 | canonical 资产核查、CPU demo、持久研究演练、portable reproduction 均成功 |

这些是精确核心提交的真实 job 日志结果。被跳过的 4 项需要实际原生依赖；随后同一提交的 [native run 45925333312](https://github.com/Qesire/RSIforme/actions/runs/45925333312)、job `140265204418` 实际运行了 4 项 compiler 测试，0.299 秒，全部通过。原生 oracle 和 scripted 包装器也全部成功，`execution_provenance_verified`、`evidence_append_only_verified`、`late_verifier_evidence_verified`、`standalone_verified` 均为 true。原生证据详见 [NATIVE_HARNESS_VALIDATION](NATIVE_HARNESS_VALIDATION.md)。

原生故障演练是在收到真实提交结果后丢失客户端返回，再恢复同一 round；它不是在真实作业中注入客户端等待 timeout。后者当前是单元 mock 验证。CPU 和原生验收中的研究 worker 均为明确标注的 scripted fixture，不包含模型 API 调用。

### 版本与后端证据

| 范围 | 状态 |
| --- | --- |
| 本轮 v0.2 scripted 控制器 + CPU + portable release | 已实际执行，见上述研究 ID 与证据摘要；无模型调用 |
| v0.1 原生 Docker Work/Judge | 已通过：提交 `5ac7e8b2af6697777650af5abee7ee02df2da0c1`，Actions [45922452679](https://github.com/Qesire/RSIforme/actions/runs/45922452679)；完整 artifact 与逐轮结果见 [原生验收](NATIVE_HARNESS_VALIDATION.md) |
| v0.2 精确核心提交的 CPU CI | `419abfcc5c752477b848b94d8a42ca8f07e01682`，[45925333216](https://github.com/Qesire/RSIforme/actions/runs/45925333216) 已通过，见上表 |
| v0.2 精确核心提交的原生 CI | 同一核心提交的 [45925333312](https://github.com/Qesire/RSIforme/actions/runs/45925333312) 已通过，真实依赖、oracle、scripted 包装器及新增证据/交付校验均成功 |
| 两轮真实模型 | 自动审批阻断，G3/P0 保持待验证；不重试或替代 |
| A100、Slurm 与目标推理任务 | 未运行，需目标环境基线与完整作业记录 |

本轮没有实现自动 spawn 研究线、合并 winning branches、自更新控制器或证明研究能力递归提升。固定 lanes、best-valid 父代和 worker 的结构化提案协议是已经实现的明确范围。下一步见 [ROADMAP](ROADMAP.md)。

## 2026-11-26：v0.1 历史记录

2026-11-26 · 从空仓库建立的第一版

### v0.1 当日完成

已经实现可运行的外层候选管理：固定任务上下文、完整文件快照、父代与研究线、提交意图、run/round 关联、独立评价、同轮指标选择、恢复和确认后发布。源码位于 `src/rsiforme`，核心使用 Python 标准库。

RSI-Harness 适配器对照固定提交 `486eec2b93296c514238f242f172703c3736ccc1` 实现。它包装原生客户端，不重写 Work/Judge 或 Slurm 调度。`completed` 和质量通过分别判断；本项目的 CPU task 用 `quality_passed=1` 表达质量门槛。原生反馈不明确时保留 pending，不自动重复提交。

容器任务 `tasks/jsonl-aggregate` 已包含 Work 镜像所需的 RSIforme 包、原始程序、任务合同、已知参考解和固定 Judge。生成资产由 `sync_assets.py` 对照 canonical 源文件检查。Slurm 配置字段来自固定版本的上游模型；真实站点参数留给实际环境绑定。

### v0.1 当日 CPU 闭环

命令从仓库根目录执行，退出码均为 0：

```bash
python3 -m pip install -e . --no-build-isolation
python3 -m rsiforme demo --store runs/validation-2026-11-26
python3 -m unittest discover -s tests -v
python3 tasks/jsonl-aggregate/sync_assets.py --check
bash -n scripts/bootstrap_harness.sh
```

本地测试：**47 项通过，8.426 秒**。覆盖源码/上下文身份、恢复、错误候选、超时与崩溃、持久证据、未知提交恢复、重复轮次、同轮指标、移动日志后的身份核对、已导入 score 不一致、确认后退步，以及 packaged Judge 的实际 reward 门槛。Harness 协议测试中的响应与持久文件是明确标记的合成样例。

CPU 示例按顺序执行 baseline、optimized、incorrect 三份预写程序，随后重新打开档案，恢复有效搜索结果最好的候选，在恢复出的代码上进行第四次评价并发布。每个候选先经过固定正确性检查；通过后测量 40,000 行、128 组数据，预热一次，三次重复取中位数。计时包含新 Python 进程、JSONL 读取、聚合与结果写出，不包含数据生成和父进程的答案检查。

| 实现 / 阶段 | 正确性 | 中位耗时 | 吞吐量 |
| --- | --- | ---: | ---: |
| baseline：每组重新扫描全部记录 | 通过 | 221.023 ms | 180,976.89 行/秒 |
| optimized：单次扫描累积 | 通过 | 88.449 ms | 452,237.40 行/秒 |
| incorrect：丢弃负数的负对照 | 拒绝 | 不排名 | 不排名 |
| 恢复 optimized 后的独立确认 | 通过 | 87.921 ms | 454,956.24 行/秒 |

本次搜索测量的吞吐比为 **2.4989×**。这是预写算法在当前 CPU 小型任务上的结果，没有测量 Agent 自主发现能力，也不代表模型推理或 GPU 收益。原始数值、三次重复、环境、评价器摘要、源码身份和四次评价保存在 [cpu-smoke.json](evidence/cpu-smoke.json)。该文件仅将目录位置改成可移植的相对路径，数值未改写。

关键身份：

| 对象 | ID |
| --- | --- |
| 最后捕获但无效的候选 | `c-a561d4ce38624d49` |
| 恢复和发布的候选 | `c-55c1d3cd4e604398` |
| 搜索评价 | `e-df6771325e794e8a` |
| 确认评价 | `e-44f022c1babc43f0` |
| 发布记录 | `r-763ba390919b40cf` |
| 发布源码摘要 | `c91a3e68ad7bfd8b93a5da9d98795aa0d64603085ec817c6e61716192b06c0af` |

本轮本地环境是 Linux / CPython 3.12.14。运行时原始目录还保留每个子进程的 stdout/stderr 和错误答案对照；完整 run 不纳入源码仓库。GitHub CI 配置会在 Python 3.11 和 3.12 重新执行测试与 CPU 示例；本地结果不代替远端 CI 状态。

### v0.1 当日尚未完成的运行验证

2026-11-26 当时的本地环境没有 Docker、Apptainer、Slurm，也未提供目标集群连接，因此当天没有执行完整原生 Work/Judge、GPU 任务、A100 基线校准或模型 Agent 会话。`harness-command` 只验证命令生成，没有把它称作实际 Harness dry-run。后续在 2026-11-27 完成的 v0.1 Docker 原生运行另记于上面的版本证据表，未追改当天结论。

当天计划的下一项验收是实际 CPU oracle、Work 内归档持久化、run ID 传入、原生 round 关联和中断恢复，再接入真实 Agent 与目标推理任务。当前进展与仍待验证项以本页 v0.2 部分及 [ROADMAP](ROADMAP.md) 为准。

### v0.1 当日取舍

采用一个执行底座和一个控制器，优先补齐永久候选档案与交付身份。候选先完整保存，后续有真实大仓库成本证据再增加增量存储。测试针对实际会破坏研究结论的边界；评价与发布保留原始指标，避免混合不同候选的最好值。

来源与工程职责映射见 [INDUSTRY_WORKFLOWS](INDUSTRY_WORKFLOWS.md)，原生接口和站点绑定见 [HARNESS_INTEGRATION](HARNESS_INTEGRATION.md)。
