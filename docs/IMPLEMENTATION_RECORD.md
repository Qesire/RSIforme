# RSIforme v0.1 实施记录

2026-11-26 · 从空仓库建立的第一版

## 本轮完成

已经实现可运行的外层候选管理：固定任务上下文、完整文件快照、父代与研究线、提交意图、run/round 关联、独立评价、同轮指标选择、恢复和确认后发布。源码位于 `src/rsiforme`，核心使用 Python 标准库。

RSI-Harness 适配器对照固定提交 `486eec2b93296c514238f242f172703c3736ccc1` 实现。它包装原生客户端，不重写 Work/Judge 或 Slurm 调度。`completed` 和质量通过分别判断；本项目的 CPU task 用 `quality_passed=1` 表达质量门槛。原生反馈不明确时保留 pending，不自动重复提交。

容器任务 `tasks/jsonl-aggregate` 已包含 Work 镜像所需的 RSIforme 包、原始程序、任务合同、已知参考解和固定 Judge。生成资产由 `sync_assets.py` 对照 canonical 源文件检查。Slurm 配置字段来自固定版本的上游模型；真实站点参数留给实际环境绑定。

## 实际执行的 CPU 闭环

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

## 尚未完成的运行验证

当前环境没有 Docker、Apptainer、Slurm，也未提供目标集群连接。因此没有执行完整原生 Work/Judge、GPU 任务、A100 基线校准或模型 Agent 会话。`harness-command` 已验证能生成固定版本接受的参数，但它只输出命令；本轮没有将命令生成称为实际 Harness dry-run。

下一项验收是：在目标环境填入站点 profile，执行 CPU task 的 oracle 及一条真实 Agent 研究线，确认 Work 内归档持久化、run ID 传入、原生 round 关联和中断恢复。之后接入已有稳定基线的真实推理任务，并在目标 A100 上重新建立比较基线。方向自动选择、跨 lane 组合和研究器更新列在 [ROADMAP](ROADMAP.md)，当前没有冒充已实现能力。

## 本轮取舍

采用一个执行底座和一个控制器，优先补齐永久候选档案与交付身份。候选先完整保存，后续有真实大仓库成本证据再增加增量存储。测试针对实际会破坏研究结论的边界；评价与发布保留原始指标，避免混合不同候选的最好值。

来源与工程职责映射见 [INDUSTRY_WORKFLOWS](INDUSTRY_WORKFLOWS.md)，原生接口和站点绑定见 [HARNESS_INTEGRATION](HARNESS_INTEGRATION.md)。
