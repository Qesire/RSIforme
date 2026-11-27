# 真实模型验收：授权后客户端初始化阻断

记录日期：2026-11-27。机器可读记录与原始文件哈希见 [model-runtime-2026-11-27.json](evidence/model-runtime-2026-11-27.json)。

**用户已明确授权这次限定验收；第一个真实 Codex worker 已启动，但客户端在初始化阶段因只读文件系统退出。** 本轮完成了 1 次基线评价，取得 0 个可采纳的模型提案，新增候选、确认、晋升与发布均为 0。第二个 worker 未发起，原预算没有重置。[参考工程验收](REFERENCE_PARITY.md) 中的 G3/P0 仍保持开放。

此前的授权阻断及验收条件保留在 [MODEL_VALIDATION_PENDING.md](MODEL_VALIDATION_PENDING.md)。本记录对应用户明确同意后建立的新归档，当前阻断来自客户端运行环境。

## 授权范围与归档身份

用户在本会话明确回答“是的”，同意既有 Codex 客户端向当前配置的 `ab.chatgpt.com` 传输本公开 JSONL 任务的限定源码、指令、profile，以及本轮候选和评价反馈。授权最多 2 轮 worker、4 次正式评价，评价预算包含基线与确认。一轮 worker 内部可能包含多次模型请求；本文没有把 worker 数量换算成 HTTP 请求数量。

| 项目 | 本轮记录 |
| --- | --- |
| 控制器提交 | `5a7a2a5995adf5c8cdd2b051e7615ef6325c7336` |
| Research | `research-784a05ab0f1544fa` |
| Task | `jsonl-model-research-v1` |
| Trial | `trial-0001`，`algorithm` lane |
| 基线候选 | `c-374739f1cef845a3` |
| 基线评价 | `e-3a4251a0105240d5` |
| 基线执行 | `local-76724657358f443ca760732a5e70558c` |
| Codex 客户端 | `codex-cli 0.172.0-alpha.2` |
| 原始归档标签 | `rsiforme-model-validation-20261127-0528` |

工作目录位于仓库之外。冻结的指令仅允许读取工作目录内的 `solution.py`、`handoff.json`、`proposal.schema.json` 和 worker 自建局部检查文件，禁止读取归档 evaluator/reference/oracle、其他 fixture、其他项目或账户材料，并禁止联网检索现成答案。这是输入范围与行为约束；它不构成对恶意代码的独立读取沙箱证明。原始指令、handoff 和 schema 均已计算哈希，公开记录没有复制认证或私有配置。

## 实际发生的事件

| 时间（UTC） | 事件与结果 |
| --- | --- |
| 05:29:07.442291—05:29:08.960192 | 基线在本地新进程中完成固定评价，`completed`、`valid=true`。 |
| 05:29:31.525519 | 创建研究归档，冻结程序、profile、任务与预算。 |
| 05:29:46.732866 | `trial-0001` 启动真实 `codex exec` worker。 |
| 05:29:55.585041 | 控制器记录 `worker_failed`；worker 用时 `8.848140` 秒，退出码 `1`。 |
| 05:33:19 附近 | 无 prompt 的 app-server 启动诊断仍产生只读文件系统错误；证据位于 `client-preflight/startup.*`。 |
| 05:46:09.883364 | 新启动入口的 `--preflight-only` 提前识别只读状态目录，以退出码 `2` 停止；没有建立新 campaign。 |

真实 worker 的错误为：

```text
Error: failed to initialize in-process app-server client: Read-only file system (os error 30)
```

其 argv 使用现有公开模板：

```json
["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--json", "--output-schema", "{schema}", "--output-last-message", "{proposal}", "-"]
```

`worker.stdout.log` 和 `work/proposal.json` 均为 0 字节，`solution.py` 与归档基线的 SHA-256 相同。第一个 trial 没有候选评价 ID，归档中只有基线候选和基线评价。官方 `exec` 源码会在 app-server 初始化之前打开最后消息输出文件，所以空 `proposal.json` 的存在不能证明模型曾完成生成。[初始化顺序来源](https://github.com/openai/codex/blob/3abf31016e5dce07567454292571151829135df0/codex-rs/exec/src/lib.rs)

空日志也不能证明从未发生网络请求。本记录将实际模型请求数量保留为未知，且不把初始化失败计作模型能力、算法质量或优化假设的失败。

## 已完成的基线与未消耗的预算

基线测量覆盖新 Python 进程、JSONL 解析、聚合与 JSON 输出。固定输入为 40,000 行、128 组，先 warmup，再测量 3 次并取中位数；输入生成和父进程 oracle 检查在计时之外。

| 指标 | 基线实测 |
| --- | --- |
| 中位耗时 | `205.312463 ms` |
| 最短／最长耗时 | `202.101921 / 212.084837 ms` |
| 吞吐量 | `194824.997059 rows/s` |
| 正确性用例 | empty、singleton、negative、order_unicode、integer_precision 均通过 |
| 环境 | CPython 3.12.14，x86_64，Linux-6.18.57-x86_64-with-glibc2.41 |

这是一份公开任务的 CPU 基线。没有模型改善值、加速比或性能晋升可报告。基线评价的归档 `kind` 为 `search`，其本轮角色为 baseline；它没有被伪装为 confirmation，也没有据此发布结果。基线 profile 是准备阶段的局部诊断，不额外记作一次正式候选评价。

| 预算／产物 | 上限或要求 | 实际状态 |
| --- | --- | --- |
| worker | 最多 2 轮 | 已启动 1 轮，失败 1 轮；第二轮停止未发起 |
| 正式评价 | 最多 4 次，含 baseline 与 confirmation | 消耗 1 次 baseline；其余 3 个数量名额未消耗，其中 1 个原留作 confirmation |
| 单 worker 超时 | 180 秒 | 本轮约 8.85 秒即退出 |
| 研究 walltime | 900 秒 | 原截止为 `2026-11-27T05:44:31.525519+00:00`，现已过期；未暂停或重置 |
| 可采纳模型 proposal | 需要真实生成与核验 | 0 |
| 新候选／候选评价 | 需要真实源码与固定评价 | 0 / 0 |
| 新确认／晋升／发布 | 需要独立确认后完成 | 0 / 0 / 0 |

持久 `research/STATUS.json` 中的 `875.932505` 秒是 worker 失败时的历史快照。walltime 从研究创建时持续流逝；未消耗的 worker／评价数量不等于持续有效的运行时间，也不会因改写文档、新建启动命令或客户端预检而恢复。机器可读记录另保存了生成摘要当时的剩余秒数与截止状态。本次 campaign 已过原截止，未消耗的 1 个 worker 数量名额不作为可恢复额度。

## app-server 启动诊断与同版本源码

执行方随后以 stdin DEVNULL/EOF、20 秒超时执行无 prompt 启动预检，并将官方 `sqlite_home`、`log_dir` 配置重定向到可写 scratch 目录。以下仅将实际 campaign 路径替换为占位符：

```json
["codex", "app-server", "-c", "sqlite_home=\"{campaign}/client-preflight/state\"", "-c", "log_dir=\"{campaign}/client-preflight/logs\"", "--listen", "stdio://"]
```

实际退出码为 `1`。`client-preflight/startup.stdout.log` 为 0 字节，`startup.stderr.log` 为 284 字节，后者包含：

```text
Error: Read-only file system (os error 30)
```

这项预检没有发起第二个研究 worker，也没有运行新的候选评价。公开摘要保存预检日志的哈希与错误摘录；命令结构、stdin、超时和退出码来自执行方记录。单独的跟踪尝试没有取得 syscall trace，也不被作为 app-server 的运行证据，因此下文保持源码推断的边界。

已将 `0.172.0-alpha.2` 对应到官方标签 `rust-v0.172.0-alpha.2`，固定提交为 `3abf31016e5dce07567454292571151829135df0`。[官方标签对象](https://api.github.com/repos/openai/codex/git/tags/5bebcbdd57f8479285f64833dbc359e3ec49efcb)

| 官方固定源码 | 直接相关的行为 |
| --- | --- |
| [runtime-agent-instance/src/installation_id.rs](https://github.com/openai/codex/blob/3abf31016e5dce07567454292571151829135df0/codex-rs/runtime-agent-instance/src/installation_id.rs) | 路径固定为 `$CODEX_HOME/installation_id`；以 `options.read(true).write(true).create(true);` 打开。已有有效 ID 也先要求可写打开；不存在或无效时还会写入和同步。 |
| [app-server/src/in_process.rs](https://github.com/openai/codex/blob/3abf31016e5dce07567454292571151829135df0/codex-rs/app-server/src/in_process.rs) | 进程内启动无条件调用 `resolve_installation_id(&args.config.codex_home).await?`。 |
| [app-server/src/lib.rs](https://github.com/openai/codex/blob/3abf31016e5dce07567454292571151829135df0/codex-rs/app-server/src/lib.rs) | 独立 app-server 在传输初始化前无条件调用 `resolve_installation_id(&config.codex_home).await?`。 |
| [exec/src/lib.rs](https://github.com/openai/codex/blob/3abf31016e5dce07567454292571151829135df0/codex-rs/exec/src/lib.rs) | 将初始化错误包装为实际日志中的前缀，并在启动客户端之前打开最后消息输出文件。 |

本地只读检查确认安装标识文件不存在；执行方确认 `CODEX_HOME` 为只读挂载。源码中的强制写入与该环境不兼容，且此路径不经过 `sqlite_home` 或 `log_dir`。这足以说明两项重定向不能满足本版本的启动要求。

**没有 syscall trace，不能把该路径写成此次唯一观测到的失败系统调用，也不能排除其他初始化阻塞。** 同版本已检查的调用链没有安装标识的独立路径配置。后续需要正常支持的客户端可写状态环境；本轮没有改 HOME／CODEX_HOME、迁移认证、替换端点、关闭审批或绕过沙箱来继续。

## 新启动入口的提前阻断验证

执行方于 `2026-11-27T05:46:09.883364+00:00` 实际运行 [run_model_research.py](../examples/jsonl_aggregate/run_model_research.py) 的 `--preflight-only`。返回 `ready=false`、`reason=codex_state_write_unavailable`，退出码为 `2`。

| 检查或副作用 | 实际观察 |
| --- | --- |
| 状态目录存在 | `true` |
| 安装标识存在 | `false` |
| 文件系统只读 | `true` |
| 所需路径可写 | `false` |
| CLI 版本 | `0.172.0-alpha.2`，版本命令退出码 `0` |
| 登录状态命令 | 退出码 `0`；账户信息不进入公开摘要 |
| worker／正式评价／模型 prompt | `0 / 0 / 0` |
| 新 campaign／原预算变更 | 均未发生 |

这次入口预检只检查 CLI 版本、登录状态和状态路径元数据，没有启动 `exec` 或 app-server。它与前述 `client-preflight/startup.*` 的 app-server 启动诊断是两项不同观察。预检成功时也只代表这些本地前提通过，不保证 app-server、网络、模型或完整研究路径可运行。

该观察已补入机器可读记录的 `launcher_preflight`。完整模型执行路径尚未实测，本轮没有因此追加新 campaign、第二 worker 或评价，也没有改变已到期 campaign 的预算。

## 证据完整性与公开范围

截至摘要生成，12 项只读完整性检查均通过：基线 24 个证据文件的大小与 SHA-256、证据清单摘要、冻结程序摘要、profile 摘要、外层和工作区 handoff 摘要、未改变的候选源码，以及候选／评价／trial 数量、空 proposal 和不存在 finalization。检查读取既有文件，没有追加模型调用或评价。

| 证据对象 | 摘要 |
| --- | --- |
| `solution.py` SHA-256 | `0452f8007e8bfd17c8c0f30af46e3c0d4586998630ac1ecb2ece8fd7d8b9e04e` |
| evaluator SHA-256 | `5d2940551543b459220997053329147a5421820c22f5810dc29400d920bf5cea` |
| 基线完整证据清单摘要 | `93d8c9b1450e24cdbad58e7c59919a65a7a3bf806f8d099b62d5cbcccdaf42b5` |
| handoff SHA-256 | `99c9b420737e726304660cb9eb480a2a6223fea413090974aa777b7b5966ac79` |
| profile SHA-256 | `5b865686bfe1bf3056259e6715ecdb4b9136ac2d314703f9d97a45434b0ce00e` |

[机器可读记录](evidence/model-runtime-2026-11-27.json) 给出受控原始文件的相对路径、字节数和 SHA-256，便于持有原始归档的人核对。公开文件只包含审核后的元数据、错误摘录与哈希；没有复制原始 raw run、认证资料或私有客户端配置。哈希索引说明一致性，不能在缺少原始文件时独立重放完整运行。

## 仍待完成的验收

G3/P0 需要两轮真实模型工作、第二轮实质消费第一轮固定评价反馈，以及同一选中候选的 fresh confirmation。本轮尚未到达这些阶段。后续工作应先满足正常客户端运行环境的无 prompt 预检。本次 campaign 已过原 walltime 截止，不能以未消耗的数量名额继续恢复；任何新 campaign、追加额度或变更环境都须保留本次失败记录及原预算身份，不能把这次 worker 消耗清零后改称原运行成功。
