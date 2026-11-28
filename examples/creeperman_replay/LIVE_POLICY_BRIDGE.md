# 将实际评价中的排序策略交给 Creeperman

`export_live.py` 输出 Creeperman `discovery_policy.LivePolicy` 可直接加载的
`creeperman-live-policy-v1` JSON。运行结果只改变发现前沿的任务选择，不写年度主表、
不重记历史 EED，也不启动模型或发送候选查询。

## 现在就能启用的先验

没有真实资料评价支持的在线设置使用明确的 `unvalidated_bootstrap_prior`：

```bash
PYTHONPATH=src python -B examples/creeperman_replay/export_live.py bootstrap \
  --output ../private-creeperman-live/policy.json \
  --reason 'Apply observable source and path priorities; online gain has not been measured'
```

默认顺序是已有本地原件、已审核成功路径的特征分数、来源组的已审核收益、静态优先级、
发现深度、首次观察顺序。默认每第 5 次选择进行来源家族探索，路径反馈衰减系数为 0.5。
这是一组可配置先验；数值不来自合成测试的“优胜”。可用 `--policy` 指定仅含
`schema_version: 1` 与 `order_by` 的策略 JSON，或设置 `--explore-every`、`--path-decay`。

在 Creeperman 仓库中加载已有发现前沿：

```bash
PYTHONPATH=src python -B -m creeperman.discovery_policy \
  --db ../private-creeperman-live/frontier.sqlite configure \
  --policy ../private-creeperman-live/policy.json
```

随后执行该前沿的原有 catalog/page 命令。前沿保存完整策略及其摘要，每次选择记录
实际特征和策略身份。加载本身不抓网页。配置文件的保存位置必须在 RSIforme 仓库外，
已有输出不会被覆盖。

## 从已完成评价导出

```bash
PYTHONPATH=src python -B examples/creeperman_replay/export_live.py evaluated \
  --store ../private-creeperman-research \
  --output ../private-creeperman-live/evaluated-policy.json
```

这条命令只读取已经 `promote` 的 release，不自行取 `best()`、重跑、确认或提升候选。
用 `--release-id` 可指定已有 release；`--profile` 可指定 `run.py` 实际生成的对应 profile，
默认读取 `profiles/<search-evaluation-id>.json`。尚未确认的搜索结果不能冒充已发布策略。

导出复用 RSIforme 核心 release 校验，并额外核对以下关系：

1. candidate 中的 `policy.json` 字节、source digest、冻结 task 与 release 一致；
2. search 与 confirmation 都有效，confirmation 是该 search 之后的独立执行；
3. 两次执行保留的原始报告与 evaluation 汇总相符，证据清单及文件摘要未变；
4. 从封存证据中读取 epoch，核对 B、A、模型、规则、材料、outcome 与预算的摘要，
   不使用可能被替换的外部 prepared 目录；
5. 保存的 profile 指向同一个 evaluation、candidate、context、epoch，且观察到的
   metrics 和 trace 与封存报告相同。

只有 `data_kind=recorded` 的既有 release 能导出 `validated_replay`。合成资料需要明确降级：

```bash
PYTHONPATH=src python -B examples/creeperman_replay/export_live.py evaluated \
  --store ../private-creeperman-fixture-run \
  --as-bootstrap-prior --reason 'Fixture checks wiring only; use this ordering as an unvalidated prior' \
  --output ../private-creeperman-live/fixture-prior.json
```

`validated_replay` 只表示原排序在所绑定固定 epoch 中实际接受过评价。它不等于在线改善，
也不表示改变了目标分布后仍优于其他策略。新的 live 特征、探索调度和路径衰减属于
`runtime_extension.status=unvalidated`；整包同时写入
`online_validation_status=not_evaluated` 与 `production_increment_claimed=false`。

离线与在线当前共有的字段是 `unprocessed`、`direct_evidence`、`year`、`arm_yield`、
`arm_attempts`、`input_order`。线上还支持 `path_score`、`static_priority`、`depth`、
`local_available`，只能作为显式先验使用，不能添加到一个已评价策略后继续声称评价绑定。
线上没有可靠输入的 `request_bound`、`byte_bound`、`wall_bound` 和
`independent_family` 会明确拒绝，不能被改名、删去或默认为零后继续导出。

`provenance.source_policy_sha256` 对 `{schema_version, order_by}` 的 UTF-8、键排序、
紧凑 JSON 求 SHA-256，不含末尾换行。它与 Creeperman 对整个 live JSON 求出的
`policy_sha256` 是不同对象。`policy_id` 同时绑定源策略、运行设置与评价来源身份。
导出只携带身份及摘要，不复制私有候选主机、网页正文或完整 trace。

## 生产回执如何进入下一次 recorded epoch

在线 `discovery_choices` 是选择记录；catalog/page 请求日志是执行记录；
`discovery_batches` 是通过 Creeperman 的 `settle` 输出并绑定任务后接收的审核反馈。
三者需要关联，不能用其中一类代替全部动作 outcome。特别是“网页解析成功”不等于
“所有候选均已完成取证”，`settle` 产生空差集也不能单独证明任务已查尽。

| epoch 所需事实 | 必须来自的实际记录 | 缺少时的处理 |
|---|---|---|
| action ID、来源、版本、原 URL、操作摘要、输入摘要及记录范围 | `discovery_nodes`、原件获取/提取收据与文件账本 | 不靠当前 URL 猜历史动作身份 |
| 动作选择前已知特征与资源上界 | `discovery_choices` 或执行前规划收据 | 不从结果倒填特征或成本上界 |
| 初始前沿与实际发现边 | 起始时快照、逐次新增节点与原始链接位置 | 不能把最终完整前沿提前交给策略 |
| 总请求、字节与耗时 | 包括重试、失败、分页、回放的完整请求记录 | 未知成本不填 0 |
| 任务范围完成性 | 操作者绑定的 `scope_id`、完成声明与相应执行收据 | 未完成设 `incomplete`；未观察设 `unobserved` |
| 每一条网站年度证据 | 已审核 decision 与其指向的原件/证据定位及摘要 | 外链、静态目录文件描述符只作候选 |
| 精确 B/A 成员关系 | 对全部 outcome 键 K 在同一冻结 B、A 上的完整投影收据 | 不能用命中数或抽样比例替代 |

新的反馈导入先装载当前累积已审核集合为 prior，随后只结算还未计过的年度键。
Creeperman 新反馈要求显式完整任务范围与实测成本；HTTP 失败不产生零收益训练标签。
真实输出具备表中信息后，导出成现有 `prepare.py` 所定义的 epoch，再建立新任务比较。
已知正贡献可以保留在原账本，但不能将存在未决动作的批次伪装成完整可排名 epoch。

**当前没有把生产数据库任意行自动变成 recorded epoch 的转换器。** 这一限制保护
`initial_frontier`、前置特征、完成性和 B/A 投影不被自动编造。现有 `prepare.py` 会检查
输入结构、覆盖与摘要，历史证据及投影事实仍由可信导出端负责。

## 运行过的接合验证

```bash
PYTHONPATH=src CREEPERMAN_SOURCE_ROOT=/absolute/path/to/Creeperman \
  python -B -m unittest discover -s tests -p test_creeperman_live_export.py -v
```

测试真实调用 RSI 导出器、实际 Creeperman `LivePolicy` 与前沿任务表，确认排序文件能改变
已观察任务的选择，并留下策略摘要。同时检查 fresh confirmation、profile/原报告/资产
篡改拒绝、合成结果不能升级为真实 validated replay，以及缺失在线特征不能静默映射。
测试使用合成合同资料；包括 `recorded` 格式接受路径的样例也只是模拟可信导出端，
不构成历史原件、真实策略增益或竞赛贡献。
