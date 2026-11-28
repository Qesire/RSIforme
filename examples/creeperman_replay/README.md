# Creeperman：用真实历史收据比较发现顺序

本适配把 Creeperman 已记录的资料动作接入 RSIforme。候选只修改 `policy.json` 的排序规则；可信评价器在相同历史材料、基线、已裁决账本和预算上重放动作，计算精确的净新增年度键。它不联网，不运行新爬虫，不写竞赛主表或生产结算账本。

**已实现有记录动作的策略比较、计划导出，以及活跃前沿可读取的策略包。** 新增 [export_live.py 运行合同](LIVE_POLICY_BRIDGE.md)区分未验证启动先验与已确认的固定 epoch 重放；新在线环境的收益不由重放结果推定。 原件恢复、逐项网站证据审核、完整基线求差、生产任务执行、跨 worker 唯一结算仍由 Creeperman 承担。没有历史 outcome 的新动作不能被本适配离线判成零收益。仓库测试使用显式 `synthetic_fixture`，不代表新增竞赛 EED，也不代表真实模型已经完成两轮改进。

## 先准备可信本地导出

输入是一个私有 JSON epoch。它应由执行端已有来源账本、逐项裁决结果和请求日志生成。不能拿“100 条、63.2063 EED”这样的汇总数编造逐项主机名。下面是字段合同，字符串中的 hash 必须来自实际文件或可信导出。

| 顶层字段 | 含义 |
|---|---|
| `schema_version` | 整数 `1` |
| `epoch_id` | 此次固定比较范围的 ID |
| `data_kind` | `recorded` 或明确的 `synthetic_fixture` |
| `source_bindings` | 原始完整基线、已裁决账本、规则与模型的 SHA-256，键为 `baseline_sha256`、`reviewed_sha256`、`rules_sha256`、`model_sha256` |
| `baseline_keys` | 全部已记录 outcome 的年度键全集 K 与完整基线 B 的交集，逐项 `{hostname, year}` |
| `reviewed_keys` | K 与冻结已裁决集合 A 的交集，逐项 `{hostname, year}` |
| `projection` | 覆盖 K 的完整成员关系查询收据，见下文 |
| `weights` | 来自实际冻结模型的最右侧 TLD 整数万分 EED；例如 0.1234 EED 表示为 1234，未匹配权重为零 |
| `budget` | 非负整数 `requests`、`bytes`、`wall_millis`，每个维度都是上限 |
| `initial_frontier` | 开始时已经观察到、可选择的 action ID 列表 |
| `actions` | 含逐动作元数据和可信 outcome 的列表 |

无需把 1.4 亿条完整基线复制进 JSON。可信导出端先计算 K，再对冻结的完整 B 和 A 求成员关系投影。`projection` 必须包含：

```json
{
  "key_universe_sha256": "<K 的规范化摘要>",
  "receipt_locator": "<完整投影操作的实际收据位置>",
  "receipt_sha256": "<该收据摘要>",
  "complete": true
}
```

K 包含所有已记录 outcome 的 `records` 中出现的确切 `(hostname, year)`。按 hostname、year 排序去重，构造 `[{"hostname": h, "year": y}, ...]`，用 `contract.digest()` 计算摘要。`prepare` 核对声明的覆盖范围恰好对应 K，且 B/A 投影是 K 的子集。**这项校验不独立证明成员关系正确，也不重新审查历史原件。** 原始文件 hash、逐项 qualified 裁决和完整投影收据构成可信导出端的责任，不能由候选策略提供。

### 动作身份、可见信息和发现边

每个 action 包含：

```json
{
  "id": "collection-index",
  "source_id": "publisher-family",
  "artifact_id": "edition-file",
  "locator": "<实际观察到的原件或记录位置>",
  "requires": [],
  "coverage": {
    "input_sha256": "<输入文件或真实输入描述符摘要>",
    "operator_revision": "<提取/获取操作实现的 SHA-256>",
    "record_locator": "<原件内记录范围>",
    "original_url": "<原 URL，或 null>",
    "capture_or_edition": "<捕获/版本身份>"
  },
  "features": {
    "year": 2000,
    "direct_evidence": true,
    "independent_family": true,
    "unprocessed": true
  },
  "cost_bound": {"requests": 3, "bytes": 2000000, "wall_millis": 60000},
  "outcome": {}
}
```

`features` 和 `cost_bound` 必须来自选择该动作以前已经可用的资料，不能根据后来的 outcome 倒填。`features.year` 是排序线索，可以为 null；它不决定目标证据年份。`source_id` 表示稳定的采集/出版家族，`artifact_id` 区分文件或版本。

`coverage` 保留输入、操作、记录位置、原 URL 和捕获/版本。相同 payload 在不同原 URL 或捕获下，可能解析出不同的相对链接，不能仅凭 payload hash 合并为一次提取。

两类边各有含义：

- `outcome.reveals` 是实际发现边。A 或 B 任意一个目录发现 C，C 就进入可见前沿；不要求两个目录都先完成。发现图允许循环，已完成的 action 不重复执行。
- `requires` 是真正的执行先决条件，必须全部完成；例如下载原件后才能解析该文件。依赖图不能有环。

初始前沿中的动作不应有未完成依赖。非初始动作必须能沿已记录的发现边到达；它只在实际重放到相应发现步骤后可选。

### Outcome 是评价器资产

完整已记录的动作 outcome：

```json
{
  "state": "complete",
  "receipt_locator": "<原始执行收据>",
  "receipt_sha256": "<收据摘要>",
  "cost": {"requests": 2, "bytes": 1500000, "wall_millis": 45000},
  "records": [
    {
      "hostname": "<实际确切主机>",
      "year": 2000,
      "evidence_id": "<裁决记录 ID>",
      "evidence_locator": "<原件内证据位置>",
      "evidence_sha256": "<证据摘要>",
      "adjudication": "qualified"
    }
  ],
  "reveals": ["<本次实际发现的后继 action ID>"]
}
```

记录必须使用确切小写 hostname，保留 `www.` 和子域名差别；年份为 1996—2001。重复记录、跨动作重复见证、基线 B 和已有 A 都不会重复得分。相同 hostname 在不同年度分别计算。

`cost` 记录实际动作的完整成本，包括失败尝试、分页和重试。它不能超过执行前的 `cost_bound`。调度先预留上界，执行记录读出后按实际成本扣除，释放未用额度。

- 完整查询确实没有适格记录：`state=complete`、`records=[]`，可得到有效的零增量。
- 查询在 503、截断等处未完成：`state=incomplete`，保留上述字段并增加 `reason`。选中后计入已发生的成本，终止为未完成评价，不提供可排名的 `net_eed_units`。
- 从未观察过结果：`{"state":"unobserved","reason":"..."}`。选中后终止为未知，成本也不能假定已知，不当作零命中。

完整与不完整不能由“接口返回空”单独决定，应沿用 Creeperman 的作用域与完成性判断。

## 冻结、评价与导出计划

在 RSIforme 仓库根目录运行。示例路径中的 `../private-creeperman-*` 是仓库外的新私有目录，不能提交到 Git。

```bash
PYTHONPATH=src python -B examples/creeperman_replay/prepare.py \
  --input ../private-creeperman-input/epoch.json \
  --output ../private-creeperman-prepared

PYTHONPATH=src python -B examples/creeperman_replay/run.py \
  --prepared ../private-creeperman-prepared \
  --policy examples/creeperman_replay/policy.json \
  --store ../private-creeperman-research

PYTHONPATH=src python -B examples/creeperman_replay/plan.py \
  --prepared ../private-creeperman-prepared \
  --policy examples/creeperman_replay/policy.json \
  --output ../private-creeperman-plan.json
```

`prepare` 生成 `task.json` 及 `evaluator/{evaluate.py,contract.py,epoch.json}`。冻结 epoch 分别绑定 B、A、模型、规则、资料元数据、outcome 和预算的摘要；任务还绑定可信评价器与资产字节。改变材料或生产账本快照，要准备新的 epoch/task，不能沿用旧归档比较。

`run` 通过真实 `Archive.capture` 和 `evaluate_local` 运行一个排序候选，输出实际 evaluation ID、指标和证据位置。有效结果同时生成绑定该候选与任务的实际 replay profile，可直接交给 `ResearchController.initialize`。多个策略可在同一未变的 prepared/task 下向同一 archive 添加评价。

`plan` 只导出当前 `initial_frontier` 中的实际已观察任务，按成本上界预约。它不预先使用后继 outcome，不发 HTTP 请求。其 `execution_contract` 明确要求另由 Creeperman 执行器核对当前处理进度、配额、访问条件和生产账本。`plan` 本身不入队；在线加载由新增 `export_live.py` 与 Creeperman `LivePolicy` 完成。它们也不替代年度提交。

## 候选能改变什么

`policy.json` 仅接受 `schema_version` 与 `order_by`。每个排序项含白名单 `field` 和 `asc/desc`。可用字段：

| 字段 | 信息来源 |
|---|---|
| `input_order` | 初始按 `initial_frontier` 的实际观察顺序，后继按首次 `reveals` 的发现顺序追加；重复发现不重排，可作 FIFO 基线 |
| `year` | 已知的来源年代线索；null 在数值上作 9999，不赋予证据年份 |
| `direct_evidence`、`independent_family`、`unprocessed` | 选择动作前的已知资料属性 |
| `request_bound`、`byte_bound`、`wall_bound` | 执行前的资源上界 |
| `arm_attempts` | 本次重放中该“来源家族、年代线索、操作版本”组合已经完成的动作数 |
| `arm_yield` | 同一组合已取得的净 EED 万分单位 / max(1, 已发生请求数) |

`arm` 的键是 `(source_id, features.year, coverage.operator_revision)`。例如 2000 年 DMOZ 的增量不会用于抬高已饱和的 2001 年 DMOZ；不同解析操作也分别保存反馈。`arm_yield` 仅用已完成前缀的真实反馈，不能读取未来结果。计划模式没有发生新执行，其反馈从零开始。它是可解释的排序信号，不是统计置信下界、未见来源成功率或成熟 bandit 实现。默认 policy 是一个待比较的先验，不声称已经优于 FIFO。

前沿仍有任务却因必要依赖未满足而无法执行时，返回 `blocked_dependencies` 和未完成评价，不将它报告为来源耗尽。

候选不能填写 action ID 列表、证据判定、EED、权重或 oracle 字段；不能自行生成新 hostname、解析器或网络请求。真实下一阶段新增 parser 时，需另建任务合同。

## 接入既有研究控制器

`run` 输出实际基线评价 ID 与 profile 路径。将其填入：

```bash
python -m rsiforme research-init \
  --store ../private-creeperman-research \
  --program examples/creeperman_replay/research.example.json \
  --baseline-evaluation ACTUAL_BASELINE_EVALUATION_ID \
  --profile ACTUAL_PROFILE_PATH
```

模板沿用现有 Codex worker 协议，只准修改 `policy.json`，不会自动启动模型。真实模型会话仍要满足执行环境与实际材料传输范围；本次适配测试没有调用模型。研究预算是模板值，不是已批准运行记录。

研究 objective 为固定预算下 `net_eed_units` 最大化，1 EED = 10000 单位。它是固定材料的重放分数，**不会计入生产增量**。无效/未完成评价仅保留 `observed_net_eed_units` 等诊断，不参与 best 排名。

当前 confirmation 是同一评价器、同一 epoch 的新执行。它验证复现，不自动构成未见来源验证。跨来源泛化需另冻结以来源家族/版本分组的新任务，再比较原策略和新策略；生产增量只由 Creeperman 对当前 B∪A 的唯一结算决定。

## 验证范围

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_creeperman_replay.py -v
```

测试覆盖重复贡献、同主机跨年与 www 区分、B∪A 扣除、失败成本、未知结果、发现 OR 边与必要依赖、预算、多版本操作身份、冻结资产变更、oracle 字段拒绝及真实 RSIforme 评价/控制器准备接口。全部数据是合成合同样例；没有据此报告任何竞赛收益。
