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

Creeperman 已实现 [discovery_epoch.py](https://github.com/Qesire/Creeperman/blob/main/src/creeperman/discovery_epoch.py)：
事前登记操作和预算，关联实际执行回执与逐项审核，计算冻结 B/A 的完整作用域投影，
再自动调用本仓库的 `prepare.py`；指定 `--evaluate-policy` 时继续调用 `run.py`。
导出器不联网，不修改生产结算账本，也不从汇总行推测历史 outcome。

`discovery_choices` 是选择记录，执行器回执描述实际操作，`discovery_batches` 则是
通过 `settle` 并绑定完整任务范围后的审核反馈。两种操作的完成条件不同：

| 事前声明的操作 | 完成所需证据 | 对重放与在线反馈的影响 |
|---|---|---|
| `discovery` | 回执明确绑定执行器 SHA、输入 SHA、scope 和完成状态；输出文件逐项验证 SHA | 可成为 `complete`，直接 `records=[]`，揭示真实子任务；不生成 LivePolicy 的零收益训练批次 |
| `evidence_review`（默认） | 声明后生成的完整 settle，与节点、scope、实际成本一致；保留逐项 qualified 记录与原件摘要 | 交由评价器对冻结 B/A 和跨动作重复扣除，计算年度净增量 |
| 失败、截断、只有 `parsed` 或范围尚未裁决完 | 保留实际成本与未完成原因 | `incomplete`；从未执行则为 `unobserved`，均不伪装成完整零结果 |

目录操作完成只说明该操作的范围已经完成，不说明其发现的所有网页均已审核。
发现动作与后继审核分别预约和扣除真实成本。重试、失败、分页、回放及原件操作的
开销必须由执行器纳入回执；导出器不能从缺失日志中恢复这些数字。

| epoch 所需事实 | 必须来自的实际记录 | 缺少时的处理 |
|---|---|---|
| action ID、来源、版本、原 URL、操作摘要、输入摘要及记录范围 | `discovery_nodes`、原件获取/提取收据与文件账本 | 不靠当前 URL 猜历史动作身份 |
| 动作选择前已知特征与资源上界 | `discovery_choices` 或执行前规划收据 | 不从结果倒填特征或成本上界 |
| 初始前沿与实际发现边 | 起始时快照、逐次新增节点与原始链接位置 | 不能把最终完整前沿提前交给策略 |
| 总请求、字节与耗时 | 包括重试、失败、分页、回放的完整请求记录 | 未知成本不填 0 |
| 任务范围完成性 | 事前声明的操作类型、`scope_id`、完成声明与相应执行收据 | 未完成设 `incomplete`；未观察设 `unobserved` |
| 每一条网站年度证据 | 已审核 decision 与其指向的原件/证据定位及摘要 | 外链、静态目录文件描述符只作候选 |
| 精确 B/A 成员关系 | 对全部 outcome 键 K 在同一冻结 B、A 上的完整投影收据 | 不能用命中数或抽样比例替代 |

初始化先要求 LivePolicy 已绑定完整累计 prior。epoch 固定此时的 A；本轮随后新增的
记录不会被倒灌进起始 A。B 使用审核清单绑定的完整年度文件，对全部 outcome 键 K
涉及的每个年度只流式扫描一次并核验摘要、顺序与数量；A 快照也重新核验摘要。
无需把完整 B 复制到 RSI 输入中，也不按 action 反复扫描基线。

输入、执行器、规则、模型和审核清单均保存实际文件身份。审核原件必须提供原始载体
或响应体 SHA-256；缺少该字段的历史 archive replay 记录需补全，不能拿重放界面的
哈希替代。修改已登记输入、审核记录或发现产物后，导出会拒绝继续。

### 可执行顺序与真实依赖

需要 Python 3.11+、两仓源码、已有的 Creeperman 前沿节点，以及执行机上的完整基线、
对应 audit、模型、规则文件和累计已审核 settlement。以下路径是执行机的私有路径，
不是仓库自带数据；原件读取如需 ISO 支持，另安装 Creeperman 声明的 `media` extra。
单纯导出与重放不需要网络或模型客户端。不要将基线、主机清单或原始运行目录提交到 Git。

在 Creeperman 仓库根目录，先导入完整 prior，再初始化新 epoch：

```bash
CREEPERMAN_RUN=/absolute/path/to/private-run
RSIFORME_ROOT=/absolute/path/to/RSIforme
EPOCH_ID=discovery-epoch-001

PYTHONPATH=src python -B -m creeperman.discovery_policy \
  --db "$CREEPERMAN_RUN/frontier.sqlite" import-settlement \
  --settlement "$CREEPERMAN_RUN/cumulative-reviewed" \
  --model "$CREEPERMAN_RUN/model.json" --prior

PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" init \
  --baseline "$CREEPERMAN_RUN/merge261121-3" \
  --audit "$CREEPERMAN_RUN/audit.json" \
  --model "$CREEPERMAN_RUN/model.json" \
  --rules "$CREEPERMAN_RUN/rules.md" \
  --budget "$CREEPERMAN_RUN/budget.json" --data-kind recorded
```

`budget.json` 包含事前确定的非负整数 `requests`、`bytes`、`wall_millis`。
基线目录名必须与 audit 的 `baseline_id` 一致。已有数据库若已绑定完整 prior 无需重复
导入；不要在导入本轮新奖励后再补录旧 prior。

`declare --plan` 读取私有 JSON；以下是本地映射操作的字段形状，节点和文件路径必须
换成实际观察到的值，成本上界按真实计划填写：

```json
{
  "action_id": "mapping-01",
  "node_id": "ACTUAL_OBSERVED_NODE_ID",
  "input_path": "/absolute/path/to/private-run/members.json",
  "operator_path": "/absolute/path/to/Creeperman/src/creeperman/media_mapping.py",
  "record_locator": "edition-01:map:selected",
  "capture_or_edition": "ACTUAL_EDITION_ID",
  "cost_bound": {"requests": 0, "bytes": 134217728, "wall_millis": 60000},
  "initial": true,
  "operation_kind": "discovery"
}
```

按“声明 → 执行 → 完成回执”运行。这里的 `--mapping-member` 取真实 members manifest
中的路径；示例格式为 `archive95-json`，PCPress LIS 应明确改为 `pcpress-lis`。

使用 `--scope-id` 前，输入 `members.json` 还必须包含下面的 `operation_scope`。
它应在 declare 之前写定；实际成员路径、顺序和模式必须与命令完全一致。
这样 action 的输入文件 SHA 同时绑定具体处理范围，不能少处理一份映射后沿用原完成声明。

```json
{
  "operation_scope": {
    "kind": "media-mapping-selected-members-v1",
    "scope_id": "edition-01:map:selected",
    "mapping_members": ["ACTUAL_MAPPING_MEMBER"],
    "mapping_format": "archive95-json",
    "mapping_root": "",
    "link_mode": "local-map"
  }
}
```

上例是原有 manifest 的附加字段，不能替换其 container、source 与 members。
完成只覆盖所选映射的分类和已取得且可绑定的 HTML；缺体、冲突和不支持行继续在 review 中。

```bash
PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" declare \
  --plan "$CREEPERMAN_RUN/mapping-plan.json"

PYTHONPATH=src python -B -m creeperman.media_mapping \
  --manifest "$CREEPERMAN_RUN/members.json" \
  --mapping-member ACTUAL_MAPPING_MEMBER \
  --mapping-format archive95-json --scope-id edition-01:map:selected \
  --max-input-bytes 134217728 --out "$CREEPERMAN_RUN/mapping-01"
```

如果操作实际发现后继任务，应先将真实节点和发现边登记到 Creeperman 前沿，再声明
子动作，最后才 finish 父动作。子计划使用 `initial:false`、`revealed_by:"mapping-01"`；
真正需要父动作产物时还应写 `requires:["mapping-01"]`。导出器检查真实发现路径，
并从已声明子动作形成 `reveals`。例如，有网站审核子任务时先执行：

```bash
PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" declare \
  --plan "$CREEPERMAN_RUN/review-plan.json"
```

所有本次实际发现的子动作声明后，封存父操作：

```bash
PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" finish \
  --action-id mapping-01 --execution-receipt "$CREEPERMAN_RUN/mapping-01/report.json"
```

没有后继任务时可直接 finish 父操作。初始前沿要在任何 outcome 登记之前声明完毕。
仅导出一个完整发现动作可得到直接零增量，但这不意味着其后继网站没有贡献。

上一步的网站年度审核子计划使用 `operation_kind:"evidence_review"`；先 declare，才运行
现有取证、逐项审核和 `settle`。以下变量取自实际计划、规则版本和执行回执；三项成本
须与回执的 `cost` 完全一致，不能使用容器累计计数替代本动作开销：

```bash
# 执行已声明范围的取证和逐项审核，保存 decisions 与实际执行回执，再结算。
PYTHONPATH=src python -B -m creeperman.cli settle \
  --baseline "$CREEPERMAN_RUN/merge261121-3" \
  --audit "$CREEPERMAN_RUN/audit.json" --model "$CREEPERMAN_RUN/model.json" \
  --decisions "$CREEPERMAN_RUN/review-decisions.jsonl" \
  --rule-version "$CP_RULE_VERSION" --out "$CREEPERMAN_RUN/reviewed-action"

PYTHONPATH=src python -B -m creeperman.discovery_policy \
  --db "$CREEPERMAN_RUN/frontier.sqlite" import-settlement \
  --settlement "$CREEPERMAN_RUN/reviewed-action" \
  --model "$CREEPERMAN_RUN/model.json" \
  --node-id "$CP_NODE_ID" --scope-id "$CP_SCOPE_ID" --complete \
  --requests "$CP_REQUESTS" --bytes "$CP_BYTES" --wall-millis "$CP_WALL_MILLIS"

PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" finish \
  --action-id "$CP_ACTION_ID" --batch-id "$CP_BATCH_ID" \
  --execution-receipt "$CREEPERMAN_RUN/review-execution.json"
```

`CP_BATCH_ID` 使用 import-settlement 实际返回的 `batch_id`；同一批次重复导入时返回
已有记录的 `id`。`--complete` 是执行者对明确范围的完整声明，不是让未决记录自动合格。
审核批次必须晚于该动作的 declare，且节点与 scope 相符。未完成动作不导入完整批次，
finish 时省略 `--batch-id` 并提供 `--reason`，保存实际失败成本；未执行动作保持 unobserved。

最后一条命令同时导出、冻结和评价：

```bash
PYTHONPATH=src python -B -m creeperman.discovery_epoch \
  --db "$CREEPERMAN_RUN/frontier.sqlite" --epoch-id "$EPOCH_ID" export \
  --out "$CREEPERMAN_RUN/epoch-export" --rsiforme-root "$RSIFORME_ROOT" \
  --evaluate-policy "$RSIFORME_ROOT/examples/creeperman_replay/policy.json"
```

输出含 `epoch.json`、`projection.json`、原始执行及封存 outcome 回执、`prepared/`；
启用评价时另有 `research/` 和 `evaluation.json`。省略 `--evaluate-policy` 仍执行 prepare，
但不评价。输出目录必须不存在。选择到 incomplete/unobserved 的评价没有有效排名分数；
导出成功不等于该 epoch 可排名。完整 epoch 的搜索评价也不自动生成已确认 release。

本接口要求未来执行从登记开始。现有 PCPress 原件读取和旧 metadata 运行没有这一
事前登记与完整 B/A 绑定，不能重写为本接口的真实 recorded 评价。可以保存原始成本与
产物作为研究证据，或在新 epoch 中明确把已有文件作为起始资产；缓存复放是另一次
执行，不会抹掉取得该缓存时的历史成本。

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

Creeperman 端另有自动生产导出接口的跨仓检查，在 Creeperman 根目录运行：

```bash
PYTHONPATH=src RSIFORME_ROOT=/absolute/path/to/RSIforme \
  python -m pytest -q tests/test_discovery_epoch.py
```

9 项合成合同测试真实执行 prepare 与 Archive/evaluate，覆盖冻结 prior、B/A 扣除、
精确 www/年度去重、事前范围与成本、产物变更拒绝，以及发现父动作完成后揭示审核
子动作。合成链验证接口可以消费已记录结果；当前尚未用完整生产 B/A 执行真实 epoch，
没有据此产生新的竞赛 EED。
