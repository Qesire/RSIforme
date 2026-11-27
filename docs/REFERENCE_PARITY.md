# RSIforme 与真实研究系统的工程对照

更新日期：2026-11-27。原始审计基线为 `fdfb98d415c2a19becd6ab7e213a0348081c71ed`（v0.1）；v0.2 冻结核心提交为 `419abfcc5c752477b848b94d8a42ca8f07e01682`。该精确提交的 [CPU CI](https://github.com/Qesire/RSIforme/actions/runs/45925333216) 和 [原生 CI](https://github.com/Qesire/RSIforme/actions/runs/45925333312) 均已通过；真实模型两轮仍待验证。

## 结论与对齐范围

**v0.2 已实现持久、受预算约束的研究控制器和可复查交付，并完成 scripted worker 的工程闭环；真实模型研究的 P0 验收仍未完成。** 公开 JSONL 任务的限定传输与最多两轮 Codex worker 已获明确授权。新会话完成 baseline/profile 后，首轮 worker 在客户端运行时初始化阶段因只读文件系统退出，未产生可采纳模型提案或新候选评价。当前失败记录、已确认范围及早先审批拒绝的历史见 [MODEL_VALIDATION_PENDING](MODEL_VALIDATION_PENDING.md)。

当前验收目标仍是：**一个持久控制器、一个真实编码 worker、一个固定评价通道，可以有预算地完成多轮研究，并在中断后恢复，最后交付经过新一次完整确认的代码。** 选择最小单控制器是本项目的实现范围，不能靠收窄描述就宣称已达到全部参考水平。GPU 模型任务、Slurm、并行多线、自动新建研究线和跨线组合各自需要对应机制与实际运行证据。

## v0.2 当前证据与未关闭项

本轮实际 scripted 控制器运行是 `research-0166851c9e43482a`：基线与 cProfile 后，两轮 worker 分别产生 `improved`、`invalid`，第二轮 handoff 已包含第一轮真实评价；控制器恢复第一轮候选，独立确认并导出，portable verification 与额外一次 reproduction 均通过。提案来自预写 fixture，模型调用为 0。精确核心提交的 CPU CI 又在两种 Python 环境重跑工程门禁，每个环境 81 项通过、4 项原生依赖测试 skip，并通过持续控制器和独立复查。完整身份与数值见 [实施记录](IMPLEMENTATION_RECORD.md) 和 [控制器证据摘要](evidence/controller-v2.json)。

| 项目 | 已实现及已有工程证据 | 当前仍需证明的内容 |
| --- | --- | --- |
| G1：持久会话与预算 | `ResearchController` 保存阶段、固定预算和停止原因；准备、worker、评价前核查额度，为确认保留一次评价；未知派发经显式放弃后仍计费。scripted 运行及故障测试覆盖重开状态和限制。 | 真实模型会话中的相同语义；原生剩余提交额度与外层预算的完整联调。外层没有新增一个会自动读取所有原生余额的资源调度器。 |
| G2：实测基线与诊断 | 有效 baseline、执行证明和封存证据是初始化条件；真实 cProfile 绑定源码/context，并进入 handoff。 | 模型是否依据这些观察提出有区分力的假设；不能从存在 profile 文件直接推断。 |
| G3：真实 worker 与反馈 | 结构化 argv、stdin、schema、日志、超时和历史交接已实现；scripted 第二轮确实读到第一轮评价。授权后的实际首轮保留了初始化失败、退出码和耗时。 | **授权已落实；当前运行初始化失败，两轮真实模型反馈证据仍缺，P0 开放。** scripted fixture 和一次失败启动不关闭此项。 |
| G4：失败与恢复 | worker 失败、超时、无效候选、准备中断、已完成评价重认、promotion 重认与未知评价的显式放弃已有工程测试；v0.2 原生运行已验证丢失客户端返回后恢复同一 round。 | 真实模型运行中的恢复证据；原生客户端等待 timeout 目前由单元 mock 验证，不能把 lost-response 注入称为真实 timeout 注入。 |
| G5：父代与方向 | 默认 `best-valid-search` 父代，尊重 maximize/minimize；固定 lanes 轮转，可显式选择父代/线。worker 按 handoff 历史提交本线假设。 | 真实模型消费反馈的行为；ALE 式自动 spawn 新研究线、合并 winning branches、自动更新方向策略**尚未实现**。不能概称完整智能方向管理。 |
| G6：确认与交付 | `finalize` 恢复选定源码并进行新执行确认；`export_release` 保存准确源码、冻结任务、选定 search/confirmation 和原始证据，独立校验与复查成功；v0.2 原生包装器也通过执行证明与 standalone 校验。 | 真实模型研究结果的确认。完整 research/profile/baseline/失败历史仍在归档及摘要，当前 export 不是包含所有研究历史的一键会话包。 |

原生验收有两个独立版本的证据：`5ac7e8b2af6697777650af5abee7ee02df2da0c1` 的 [Actions 45922452679](https://github.com/Qesire/RSIforme/actions/runs/45922452679) 验证 **v0.1 核心**；随后 v0.2 精确核心提交的 [Actions 45925333312](https://github.com/Qesire/RSIforme/actions/runs/45925333312) 验证真实 Docker Work/Judge、oracle、scripted 包装器、lost-response 恢复以及新增执行证明、追加封存证据和独立发布校验。两次均用预写候选，不含模型调用。详见 [原生验收记录](NATIVE_HARNESS_VALIDATION.md)。

对齐的是职责与可观察行为，不是复制参考项目的所有组件：

| 参考 | 可以核查的工程事实 | 对本项目的要求与边界 |
| --- | --- | --- |
| ALE / DECam | `climb.py` 有持久阶段、真实 Agent 启动、完整历史交接、独立 GPU 测量、失败恢复和最好程序更新；`NODES.md` 进一步连接真实档案处理与结果核验。 | 研究会话必须有可恢复执行状态；反馈实际进入下一代；发布版本可以追到代码与产物。其私有 launcher、站点和天文数据不能直接当通用依赖。[ALE-controller][ALE-nodes] |
| Sol-Engine / LingBot | 固定 GOAL、baseline、阶段计时、具体假设、局部试验、完整 Slurm 作业、JOURNAL/STATUS 与最终 REPORT 组成真实实验链。 | 本项目必须把任务口径、瓶颈证据、候选变化、完整结果和交付连接起来。公开 `search.py::plan_eval()` 仍抛出 `NotImplementedError`，不能把完整通用搜索驱动当成已开源模板。[SOL-goal][SOL-journal][SOL-search] |
| SoL-Pi | 论文描述从轨迹发现问题、展开独立研究支线、实现与复核、筛选与集成，并在冻结后进行未见任务评价。 | 方向要有证据来源，修改要接受固定评价；研究过程与最终保留的扩展是不同资产。不能由已公开扩展推定完整研究控制器可以直接复用。[SOLPI] |
| ERNIE Evolve | 官方说明区分选方向、选父代与假设、改代码、固定评价、复核；还报告了跨分支组合时解决状态更新冲突的实例。 | 可以在单控制器内落实这些职责，无须先建五个独立 Agent。公开说明和结果不能替代对其未公开控制器源码的核查。[ERNIE] |
| RSI-Harness | 固定版本提供 Work/Judge、提交、反馈、run/round、恢复与集群执行。 | 继续复用其执行内核；本项目补研究控制与候选身份。原生预算和恢复语义应被接入，不应另造一个相互冲突的调度器。[HARNESS][HARNESS-coordinator] |

## v0.1 已具备的基础：保留为审计起点

以下能力应保留，不需要因增加控制器而重写：

- `Archive.capture()` 冻结实际源码，绑定父代、lane、假设及任务上下文；`restore()` 可以恢复历史版本。
- `begin_submission()` 先保存提交意图，未知返回保持 pending；`bind_submission()` 和 `read_round_report()` 绑定同一个 run 中的明确 round，避免混用逐项最好值。
- `record_evaluation()` 区分执行状态、有效性与指标，并保存证据；`best()` 只在同一任务内选择有效搜索评价。
- `promote()` 要求同候选、同上下文的新一次确认。CPU 演练已经验证“最后工作区不是最好版本”时恢复早期版本的路径。

这些结论对应 [实施记录](IMPLEMENTATION_RECORD.md) 中 2026-11-26 的 47 项测试与预写 CPU 演练。随后新增的 v0.1 原生运行补上该旧提交的 Work/Judge 证据；真实 Agent 持续研究和目标 GPU 运行仍需各自证明。当前 [archive.py](../src/rsiforme/archive.py) 已有 v0.2 的证据封存和新执行确认强化，不应把其新行为追记为旧版实测。

## 六项缺口的原始验收合同

下表保留 v0.1 审计起点和 v0.2 验收要求，当前实现进度以上表为准。P0 项需要同时有机制和对应的运行证据；真实模型项不因 scripted worker 工程测试通过而关闭。每项关闭都需要“实现位置 + 运行 ID + 可定位证据”，不能仅填文件名或测试通过数量。

| 编号与优先级 | 参考项目中的动作 | v0.1 实际状态 | v0.2 必须实现的行为 | 关闭缺口需要的运行证据 |
| --- | --- | --- | --- | --- |
| **G1 · P0：持久、受预算约束的研究会话** | ALE 的 `agent_phase()` 保存阶段，`save()` 原子更新；Sol GOAL 给出有上限的迭代状态机。 | 档案操作持久化，但 CLI 不拥有研究会话；没有自动推进、预算准入或停止原因。Harness task 已有原生提交数与超时限制，不能说系统完全没有预算。 | 保存 session、当前阶段、当前 trial、下一动作、截止时间与试验/worker 调用限额。发起耗费资源的动作前登记，结束后记实际状态与耗时；失败尝试也留账。使用原生 Harness 时接入其剩余提交额度，并为确认留出资源。 | 一条启动命令实际推进多个阶段；session 状态与事件能解释每次调用及停止原因；达到试验数或截止时间后不再启动新工作。重启不能重置已经消耗的额度；未确认的外部执行不能被当作未发生。 |
| **G2 · P0：基线与诊断驱动任务进入** | LingBot 先读取基线与阶段计时，再将主要注意力放到 refiner attention；GOAL 明确热推理计时范围。 | 任务身份与评价器固定，但 baseline 只在演练代码中被指定；profile、瓶颈和方向没有进入通用可执行流程。 | 先用固定合同完整评价原始候选，再执行任务适用的诊断，保存命令、输出、计时口径与候选身份。建立至少一个可检验方向，说明哪个观察支持它、预期改善哪个阶段；诊断失败时有明确阻塞或降级说明。 | baseline 和 profile 都有真实执行记录；后续 handoff 指向具体输出，而非只有“已 profiling”标志。新候选与基线处于同一数据、评价器、环境和计时范围；修改目标或范围必须开启新的比较集合。 |
| **G3 · P0：真实 worker 调用与反馈交接** | ALE `handoff()` 给每代提供以前的代码、成绩、失败与运行输出；SoL-Pi 从轨迹提出并实施下一修改。 | `program.md` 提出读取历史，`write_views()` 可生成索引；没有调用编码 Agent 的执行入口，demo 使用预写候选。 | 用结构化 argv 启动真实 worker，约定工作目录、输入 bundle、结果交接与超时，保存调用身份、日志和产物。输入包含固定任务、可恢复父代、基线/诊断、已评价候选与失败索引、剩余预算；输出必须能关联修改、假设和父代。 | 至少两轮真实模型 worker 调用。第二轮输入包含第一轮真实评价；决策或修改指出消费了哪条反馈。工作区不提供已知 optimized/solution 作为发现答案。假 worker 可以测协议，但其记录必须标为模拟，不能关闭真实 Agent 验收。 |
| **G4 · P0：失败后继续与中断恢复** | ALE 区分 Agent 结束、产物同步和 GPU grading；基础设施错误不当作算法零分。LingBot 一次完整运行失败后修复并继续。 | 已区分 failed / infra_error，已支持手动核对未知提交，但没有控制器决定重试、修复、改方向或结束。 | 为 worker 失败、候选质量失败、评价环境错误和未知外部状态定义不同转移。已知失败可以在剩余预算内重试或进入下一假设；未知提交先核对同一 run。阶段状态、执行身份与证据在重启后仍可关联。 | 受控的 worker 超时/崩溃和评价失败留下可诊断证据，后续仍有明确决策；在有外部副作用的阶段中断再恢复，不重复登记同一次评价，不盲目再次提交。故障注入单列，不冒充模型自然发现的失败。 |
| **G5 · P0：基于证据选择父代与方向；组合为条件项** | ALE 允许从历史程序复制、结合或重写；ERNIE 的方向选择与 lane 内父代选择分开。 | `parent_id` 与 `lane` 是调用者写入的元数据；`best()` 能排序，但没有下一轮的父代策略、方向状态或真实反馈决策。 | 每轮保存“选择哪个父代、检验哪条方向、为何继续/转向/停止”，并实际从该父代建立工作区。单线顺序开展不同方向足够；如果自动决策尚依赖人指定方向，要如实标记。跨线组合需记录所有来源，并作为新候选重新完整评价。 | 记录中至少有一次依据测量的继续/转向/结束决策；父代代码与工作区初始摘要一致。选择早期最好候选时不被最新失败候选覆盖。声称具备组合能力时，还需一次真实组合、冲突处理记录与组合后的完整结果；只有 lane 标签不算。 |
| **G6 · P0：通用完整确认与可复现交付；目标后端为条件项** | Sol REPORT 给出最终配置、完整命令、已排除方向；ALE 把明确版本送到真实负载，再保存其输出与后续核验。 | 档案已有恢复和晋升原语，demo 走通确认；任意研究会话的自动收尾、完整交付包与真实后端链路未验证。 | 控制器在结束时选定候选，从档案恢复，执行完整确认，再生成包含代码、任务/配置身份、复现命令、基线/搜索/确认结果、运行 ID、失败和预算摘要的交付。确认失败保留旧发布；预算不足则明确未确认。 | 在恢复出的代码上产生新的确认执行 ID，与搜索评价分开；导出内容摘要等于被确认内容。另一干净目录可以按交付命令复现。原生 Harness 能力须另有真实 Work/Judge run/round；GPU/Slurm 能力须另有目标设备与真实作业记录。 |

### 预算与恢复需要达成的语义

G1/G4 不要求实现分布式事务系统，但要求下一次启动能回答：**什么已经完成、什么仍在运行、什么结果未知、哪些资源已经使用、接下来允许做什么。** 最小记录可以存 JSON 和事件文件，继续沿用现有原子写入与运行锁。

不能仅用“已完成候选数量”作为预算，因为 worker 超时、无有效产物和评价环境故障仍然耗费资源。可以先支持试验数、worker 次数、墙钟时限与单次超时；token/API 成本仅在后端确实返回时登记，不估造精确费用。已启动动作允许完成或按合同终止，但截止后不得悄悄重新启动一轮。确认预算耗尽时交付研究记录，发布状态保持未确认。

对于 native submit，已保存意图但没有明确 round 的状态仍应保持 pending。恢复先读取同一 run 的权威状态，确认关联后再登记；不能通过换一个 run ID 绕开未决提交。对于本地 worker/评价，也要分清可安全重放的只读动作与已经开始的外部执行。

### 方向与组合的最小范围

G2/G5 要求方向来自真实代码、诊断或先前结果，不要求每轮都写 GPU 微基准。检查必须回答当轮问题：纯数据结构修改可用复杂度、差分检查和完整计时；分布式通信修改通常需要局部正确性检查；kernel 选择则适合与真实 shape 一致的微基准。低成本结果通过后仍须执行完整任务。

v0.2 可以先由主 Agent 决策、控制器保存并执行，或让配置明确给出有限方向，再让 worker 在方向内提出假设。后一种能力应表述为“有预算的定向自动研究”，不能声称已自动发现研究方向。多方向可以顺序执行，不要求同时启动多个 worker。

组合不是性能数字相加。若本轮没有兼容的有效改进，可以记录“不组合”及原因；这不阻塞单线闭环。若声明实现了跨线组合，必须保存来源候选，处理接口/状态冲突，并对组合结果重新执行相同完整评价。无需为了单一标量目标先实现通用多目标 Pareto 引擎。

## 真实参考轨迹怎样约束验收

LingBot 的记录提供了一个可直接对照的实验链：[JOURNAL][SOL-journal] 中先通过阶段计时定位 refiner 的主要开销，再检验 cuDNN attention。微基准 job `4504430` 支持该方向；完整 job `4505022` 暴露在另一分辨率 shape 上的问题；修改 backend 优先级与布局后，用微基准 job `4505973` 复查，再由完整 job `4506351` 产生被保留的结果。`STATUS.json` 与 `REPORT.md` 记录最终取舍。[SOL-status][SOL-report]

迁移到本项目的是这条证据链：诊断指向瓶颈，修改有机制，局部检查能失败，完整任务也能否决局部结果，失败反馈用于下一步，最后记录被保留版本。只有一次成功计时无法证明这些职责都运行了。

这个案例还暴露了合同管理问题：后来的 GOAL 把优化目标固定为热推理，排除模型加载；早期加载重叠候选因此被丢弃。同时，GOAL 对数值漂移的解释与循环中遗留的 PSNR gate 文字并不完全一致。我们的任务应只有一份生效的验收合同；口径改变后重新建基线，旧成绩保留原范围。不能把排除加载时间当成代码加速，也不能把该案例的实现审计与视频有效性检查扩展成它没有测过的严格质量保证。[SOL-goal][SOL-status]

## 最小运行验收

建议分开提供两类记录，避免强迫真实 Agent 恰好产生一种失败，或把故障注入包装成自主发现。

### A. 真实模型研究会话

1. 在没有预写优化答案的独立工作区，固定原始代码、数据、评价器、环境与目标口径。完整建立 baseline，并运行适用的诊断。
2. 使用实际配置的模型/编码工具，完成至少两轮 worker 调用与完整评价。保存模型/工具身份、argv、超时、输入交接、日志、候选和评价 ID。
3. 后一轮明确消费前一轮结果；记录具体的继续、修订、换父代或停止决定。保存所有实际候选，包括不改善、无改动和失败。
4. 从档案恢复选中版本，执行新的完整确认，导出可复现交付与预算摘要。若没有改善，记录已排除方向与无改善结果，不能将一次研究成功执行等同于性能改善。

### B. 控制器故障演练

覆盖“worker 超时/失败后继续”“评价返回失败或环境错误”“提交/评价完成附近中断后恢复”“预算耗尽停止”“最后工作区比早期最好版本差时仍正确交付”。允许使用明确标注的模拟 worker 和故障注入；需要展示真实控制器状态转移，不以单元测试名称代替运行证据。

两类记录共同验证工程闭环。A 中的真实性能改善还需要任务规定的正确性与重复测量门槛；B 的模拟成绩不能用于宣传性能。

### 声明能力时采用的证据层级

| 已取得的证据 | 可以作出的结论 | 尚不能据此作出的结论 |
| --- | --- | --- |
| 源码、配置、命令生成 | 机制已实现或接口已对照 | 已在真实后端执行 |
| 单元/集成测试、模拟 worker | 相应边界与控制器转移已验证 | 模型会提出有效假设 |
| 预写 CPU 候选演练 | 档案、评价、恢复、确认的实际路径已执行 | 自动发现了优化 |
| 真实模型 + 本地 CPU 固定评价 | 单线研究闭环实际跑通 | 原生 Work/Judge、集群或 GPU 已跑通 |
| 原生 Harness 实际 oracle 与 worker run | 对应任务的 Work/Judge、wrapper 与 run/round 接入已验证 | A100 或 GB200 负载获得相同收益 |
| 目标 GPU / Slurm 的实际完整任务 | 该硬件、数据、模型与任务合同上的运行能力及测得结果 | 换硬件、换计时范围后的性能可直接继承 |

当前 v0.2 已有控制器实现、scripted 工程验证和精确提交的原生集成证据，但真实模型 G3/P0 仍开放，目标 GPU 任务也未运行。即使日后有限 CPU 真实模型验收通过，也不能扩写成已达到 Sol-Engine 的模型优化规模、ALE 的自动研究线管理或所有参考项目的全部能力。

## 本轮明确不作为 P0 的事项

暂不要求复刻全部天文流水线、SoL-Pi 的大规模环境池、固定数量的多 Agent 群、向量经验库、自动修改控制器自身、分布式消息队列、通用 Pareto 搜索或所有集群后端。已有 Harness 负责的 Work/Judge 与调度保持复用。只有真实运行暴露相应瓶颈，或后续明确声明这些能力时，再增加对应机制与验收。

## 固定一手来源与源码锚点

| 来源版本 | 最值得检查的源码/记录锚点 |
| --- | --- |
| ALE `572082bab9d18396b0f1ce607046baeed32dd159` | `climb.py::save`、`handoff`、`launch`、`agent_phase`、`grade_one`、`keep_best`；`NODES.md` 的 climb、conveyor、search-worker、confirm、candidate-db 节。[控制器][ALE-controller] · [节点与运行方法][ALE-nodes] |
| Sol-Engine `1006b6a219b48e5798eec89478e7d12c101de661` | LingBot `GOAL.md` 的 Context、metric、bounded search、Deliverable；`JOURNAL.md` 的 Iter 5；`STATUS.json` 的 frontier/停止状态；`REPORT.md` 的复现命令。[GOAL][SOL-goal] · [JOURNAL][SOL-journal] · [STATUS][SOL-status] · [REPORT][SOL-report] |
| 同一 Sol-Engine 版本 | `search/search.py::plan_eval` 的未实现 GPU 阶段，界定公开驱动边界。[search.py][SOL-search] |
| SoL-Pi arXiv `2609.20519v1` | §2.2 Broad-to-Deep Harness Search；§3.5 From Observation to a Retained Mechanism。论文版本固定，属于作者研究过程记录。[论文 v1][SOLPI] |
| ERNIE 官方 2026-11-09 说明 | “ERNIE Evolve”节的职责分工；NanoGPT Speedrun Track 3 的组合与状态冲突案例。官方文章没有不可变 commit，按文章日期标识，不能等同源码审计。[官方说明][ERNIE] |
| RSI-Harness `486eec2b93296c514238f242f172703c3736ccc1` | `runtime/coordinator.py` 的研究轮次及结果聚合；原生执行职责仍由 Harness 承担。[README][HARNESS] · [coordinator.py][HARNESS-coordinator] |

[ALE-controller]: https://github.com/huangzesen/decam-rsi-pipeline/blob/572082bab9d18396b0f1ce607046baeed32dd159/nodes/climb/decam-big-tno/harness/climb.py
[ALE-nodes]: https://github.com/huangzesen/decam-rsi-pipeline/blob/572082bab9d18396b0f1ce607046baeed32dd159/NODES.md
[SOL-goal]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt/GOAL.md
[SOL-journal]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt/JOURNAL.md
[SOL-status]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt/STATUS.json
[SOL-report]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt/REPORT.md
[SOL-search]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/search/search.py
[SOLPI]: https://arxiv.org/html/2609.20519v1
[ERNIE]: https://ernie.baidu.com/zh/research/posts/ernie-evolve/
[HARNESS]: https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/README.md
[HARNESS-coordinator]: https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/coordinator.py
