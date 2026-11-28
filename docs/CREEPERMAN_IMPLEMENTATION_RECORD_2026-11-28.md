# Creeperman 领域适配的实现与验证

2026-11-28。基于 RSIforme `639153b470dfee52a2c837b458529f7b426ead90` 与 Creeperman `6831270f3f79ad80635bb2a7cb2f71c08fa8cb8d`。本次接合已有评价与控制器，不改变 RSI 核心、原生 Harness 或竞赛证据规则。

## 实现

新增 `examples/creeperman_replay`：

- `prepare.py` 把执行机的逐项动作、审核记录、完整成本、基线/已裁决集合的作用域投影冻结为 epoch。规则、模型、原件及投影收据身份由可信导出端提供；本工具不伪装成原件裁判。
- `contract.py` 实现可观察特征的有界排序、发现边、必要依赖、多维预算及全局确切主机年度去重。反馈桶按来源、年代线索和操作版本区分。
- `evaluate.py` 由 RSIforme 独立执行，依据冻结 outcome 计算整数 `net_eed_units`。不接受候选自报贡献；未完成/未知结果没有有效排名分数。
- `run.py` 使用实际 `Archive.capture`、`evaluate_local` 并保存真实 replay profile，能接入 `ResearchController.initialize` 和 `prepare`。
- `plan.py` 仅导出当前已观察前沿的计划，不访问网络、不写生产账本。`policy.json` 是待比较的先验排序，未宣称优于 FIFO；`research.example.json` 是现有 worker 协议模板，没有自动启动模型。

私有输入和运行产物不进入候选源码或公共仓库。候选仅为 `policy.json`。跨 epoch 原始分数不混排；confirmation 仍是同一任务的新执行确认，跨来源验证需要另一组冻结任务。

## 验证结果

| 验证 | 实际结果 | 说明 |
|---|---|---|
| `PYTHONPATH=src python -m unittest discover -s tests -p test_creeperman_replay.py -v` | 25 项通过 | 明确的合成合同样例；真实 Archive/evaluator/controller 初始化与 handoff 接口 |
| `PYTHONPATH=src python -m unittest discover -s tests -v` | 共 118 项，114 项通过、4 项跳过；exit 0 | 最终完整回归用时 18.411 秒；跳过为原有可选原生依赖测试 |
| `PYTHONPATH=src python -m rsiforme demo --store <新运行目录>` | exit 0 | 三个预写候选：两个有效、一个错误被拒绝；恢复最好候选、新执行确认和发布，共四次评价 |
| 模型 worker / 新候选档案查询 / 真实材料评价 | 均未运行 | 没有新竞赛贡献，不改变 Creeperman 的 155 项、100.9875 EED |

新增回归覆盖：同主机跨年及 www 区分、B 与旧 A 同时扣除、跨动作重复、源年份线索不改变裁决年份、完整零结果与未完成/未知区别、所有预算成本、发现 OR 边与 AND 必要依赖、发现环、依赖阻塞、反馈按年/操作分桶、投影覆盖和资产身份、禁止 oracle 字段、任务 context 变化以及真实控制器 handoff。

独立审查发现初版 FIFO 用完整动作表的存储顺序作为排位：当完整表为 `[A,C,B]`、初始前沿为 `[A,B]`、A 发现 C 时，会错误选择 A,C,B。已改为按初始前沿及首次真实发现时序分配位置，重复发现不改位置；两个精确回归分别验证 A,B,C 和 A,B,C,D。

第一次完整回归另暴露了本地获取上游文件时额外末尾换行造成的旧资产摘要不一致。已按上游 Git blob SHA 恢复原始文件字节，未修改原有固定资产或放宽断言；随后完整回归与 CPU demo 均通过。最终代码回归状态如上表。

## 第一阶段交付范围（历史记录，现状见后文追加）

第一阶段交付了可运行的 **已记录批次策略重放与计划导出** 接口。当时尚未接入活跃 Creeperman 前沿与生产收据自动导出；这两部分已分别由后续两节补齐。跨 worker 原子唯一结算和真实模型自主采集仍不在已验证范围内。历史重放中可能取得的分数不重复写入生产 EED。

第一阶段要求执行机准备可信 recorded epoch（包括成功、完整零收益和真实未完成动作），以相同固定预算比较策略。当前导出器已将此要求实现为事前登记与回执关联。未观察动作只进入真实采集任务，完成且有证据后才进入下一批 recorded 资料。新原件与年度证据继续由 Creeperman 的现有流程处理。

- [适配器合同与命令](../examples/creeperman_replay/README.md)
- [两仓发现方法和下一批执行顺序](https://github.com/Qesire/Creeperman/blob/main/docs/CREEPERMAN_RSIFORME_DISCOVERY_LOOP.md)

## 追加：在线策略加载接合

新增 `examples/creeperman_replay/export_live.py`，现可输出 Creeperman 实际
`discovery_policy.LivePolicy` 接受的 JSON：

- `bootstrap` 显式导出尚未验证的先验；可以立即用于已有前沿排序，数据状态不会被标成赢得了真实评价。
- `evaluated` 读取既有 release，复用核心导出检查有效 search、独立新 confirmation、原候选与封存证据。
  再核对实际报告、epoch、保存的 profile 与全部评价摘要。它不新建模型会话、不自行提升候选。
- 合成 epoch 默认拒绝成为 `validated_replay`；显式 `--as-bootstrap-prior` 才能将其排序作为未验证先验导出。
- 排序本体、live 设置与研究来源分别保留摘要。已评价排序的名称与字段不被改写；缺少线上观测量的字段明确拒绝。
  新的探索调度、路径衰减与在线环境均保持未验证状态，不由离线分数推导生产 EED。

Focused 验证命令：

```bash
PYTHONPATH=src CREEPERMAN_SOURCE_ROOT=/absolute/path/to/Creeperman \
  python -B -m unittest discover -s tests -p test_creeperman_live_export.py -v
```

本次实际运行 10 项全部通过，用时 1.210 秒。其中一个跨仓接合测试在同一进程中真实执行
RSI 捕获/评价/独立确认→显式先验导出→Creeperman `Frontier.choose`，原来 2001 年任务在前，导出的年份顺序策略实际选择了
1996 年任务，并记录对应 policy 摘要。其余检查 profile/原报告/证据变更拒绝、fresh confirmation 门槛、
重复导出不覆盖文件、合成结果降级与缺失线上字段拒绝。所有测试输入都是合成合同；`recorded` 格式
接受路径也只是模拟可信导出端，未据此报告真实资料评价或竞赛贡献。

该阶段补齐了“计划未接入活跃前沿”的部分；下节继续补齐生产回执到 recorded epoch 的自动导出。
生产选择日志、请求成本、审核输出和完整任务范围必须关联；不能把 parsed 或空 settle 输出直接变成
完整零收益 outcome。接合合同和可执行命令写在
[LIVE_POLICY_BRIDGE.md](../examples/creeperman_replay/LIVE_POLICY_BRIDGE.md)。

最终合并验证：完整回归 128 项，124 项通过、4 项原有可选依赖跳过，17.150 秒；最终跨仓导出专项 10 项通过，1.378 秒。Creeperman 配套真实运行取得三个公开光盘条目元数据，未查询历史候选、未导入生产裁决。详见 Creeperman 的 `docs/LIVE_DISCOVERY_2026-11-28.md` 与公开运行汇总。

## 追加：事前登记的生产操作自动导出

本节接续 RSIforme `92a86fbc1ea0bb034d8fedfc6c363761b08d7466` 与 Creeperman
`d5613fbcf0ef68ccf600284f3551d5b25004f099` 的实现。新增
[Creeperman discovery_epoch](https://github.com/Qesire/Creeperman/blob/main/src/creeperman/discovery_epoch.py)，
实际调用现有 RSI prepare 与 Archive/evaluate，没有改写 RSI 核心或评价合同。

执行链已实现为：初始化冻结 B/A 与规则模型身份，事前 declare 动作及成本上界，
执行器生成实际回执，finish 绑定发现产物或完整逐项 settle，再 export 生成固定 epoch。
`export --evaluate-policy` 继续实际执行 `prepare.py` 和 `run.py`；并非只生成协议文档。

### 完成性与贡献的边界

`discovery` 和 `evidence_review` 是 Creeperman 私有执行计划中的两种操作，导出时沿用
既有 RSI action schema：

- 完整发现操作要求回执绑定事前 scope、输入和执行器 SHA，并核验实际输出产物。
  它可揭示后继动作，直接 `records=[]`，不向 LivePolicy 写零收益训练批次。
- 完整网站年度审核要求同节点、同 scope、declare 之后产生的 settle；实际回执成本
  与审核批次必须相同。保留每条 qualified 记录及原件/响应体 SHA，供冻结评价端扣重。
- HTTP 失败、截断、缺少审核与单独的 parsed 状态都不能成为完整零结果。执行过的
  未完成操作保存成本与原因；未执行操作保持 unobserved。

初始动作在任何 outcome 登记前冻结。后继动作有真实发现路径，先声明子动作再封存
父 outcome，必要依赖通过 requires 保留。原始执行回执不能在同 epoch 重复收费；
变更输入、回执、留存审核记录或发现产物会被拒绝。

初始化冻结当时的累计 A，避免本轮新记录随后进入 A 而被错误重复扣除。导出按全部
outcome 键 K 对 B 和此 A 计算投影；每个相关年度的完整 B 文件只流式核验一次，
不逐 action 扫描，也不把全基线载入内存。审核、模型、规则、输入与执行器保存实际
文件身份，投影文件与回执再由 prepare 固定为任务资产。

### 本轮实际验证

在 RSIforme 根目录，设置真实 Creeperman 源码路径后运行：

```bash
PYTHONPATH=src CREEPERMAN_SOURCE_ROOT=/absolute/path/to/Creeperman \
  python -B -m unittest discover -s tests -v

PYTHONPATH=src python -B -m rsiforme demo \
  --store ../private-creeperman-cpu-demo
```

| 验证 | 本轮实际结果 | 证据范围 |
|---|---|---|
| RSI 全套 unittest，包含实际 Creeperman 前沿加载 | 共 128 项，124 项通过、4 项跳过；18.553 秒，exit 0 | 跳过项均需另行安装固定 RSI-Harness；跨仓测试实际运行 |
| Creeperman epoch 专项 | 9 项通过；最后一次 0.86 秒 | 合成输入实际调用 prepare 与 Archive/evaluate |
| Creeperman epoch、policy、批量 rank 联合 | 19 项通过 | 校验桥接、原反馈语义和批量排序等价性 |
| CPU demo | exit 0；外层实耗 4.900 秒 | 三个预写候选、两个有效、一个错误被拒绝；恢复最佳并独立确认，共四次评价 |

epoch 专项包含完整的“发现父操作 → 实际 reveals 子操作 → 子操作完整 settle → RSI
评价”链，并验证父操作不训练为失败。另覆盖冻结 A、B/A 同时扣除、精确 www/年度
区别、跨动作重复、事前范围、成本不一致、输入与逐项证据篡改拒绝。运行原始日志
保存在仓库外的私有 work 目录，没有纳入候选或公共 Git。

这些都是显式合成合同验证，CPU demo 使用预写程序，没有模型调用。真实 PCPress
原件读取与映射属于另一条已经发生的工程运行；它们没有本接口要求的事前登记和
完整生产 B/A，不追认为 recorded epoch，不从中声明真实策略评价或新竞赛贡献。
未来执行从 `init → declare → 执行/审核 → finish → export` 开始，所需依赖、两种
完成分支及准确 CLI 顺序见 [LIVE_POLICY_BRIDGE](../examples/creeperman_replay/LIVE_POLICY_BRIDGE.md#可执行顺序与真实依赖)。
