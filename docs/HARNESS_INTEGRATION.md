# 接入 RSI-Harness

RSIforme 保存候选和研发证据；RSI-Harness 负责 Work、Judge、暂停/恢复与集群作业。当前适配器针对 [OpenRSI-Index `486eec2`](https://github.com/OpenRSI-Foundation/OpenRSI-Index/tree/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness)，接口升级时先更新合同测试。

本仓库已经执行本地候选闭环和适配器测试。此文的 Harness、镜像构建和 Slurm 命令是接入入口；尚未在目标站点执行，不能据此认定 A100 环境验收通过。

## 1. 取得固定版本

在安装了 Git、Python 和 uv 的主机上，从仓库根目录执行：

```bash
bash scripts/bootstrap_harness.sh
```

脚本读取 `configs/upstreams.lock.json`，检出固定的主仓库和子模块，并使用 Python 3.12 安装 Harness CLI。它会重装当前用户的 `rsi-harness` 工具，不改变已有上游工作区中的修改，也不提交任何计算作业。可以传入一个新的上游存放目录；已有目录必须恰好处于锁定版本且没有修改。

RSIforme 核心只依赖 Python 标准库。锁定版本的 Harness 自身要求 Python `>=3.12,<3.14` 并有独立依赖，因此两者的 Python 运行要求不要混用。[来源：上游 pyproject](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/pyproject.toml)

完整的真实推理任务示例位于：

```text
.upstream/OpenRSI-Index/rsi-tasks/sglang-omni/tasks/cosyvoice3-dual-mode
```

子模块固定到 `949f201b7854be14b884d2ddcd3d3f63a590361c`。它包含任务合同、环境、原始实现、独立评价器和校准入口。其原始参考值来自 H100；迁移到 A100 时需要按目标硬件重新校准基线，并冻结新的任务/环境身份。不能沿用 H100 数值解释 A100 加速比。[来源：任务合同](https://github.com/OpenRSI-Foundation/OpenRSI-AI-Infra-Bench/blob/949f201b7854be14b884d2ddcd3d3f63a590361c/tasks/cosyvoice3-dual-mode/instruction.md)

## 2. 配置站点和运行前验证

复制 `configs/slurm.example.toml` 到仓库外的站点配置目录，替换以下实际绑定。模板使用未设置的环境变量占位；上游加载器会拒绝缺少变量的配置。

| 绑定 | 必须从哪里确定 |
| --- | --- |
| `RSIFORME_SLURM_PARTITION`、`RSIFORME_SLURM_ACCOUNT` | 集群给当前用户开放的分区与计费项目 |
| `RSIFORME_SHARED_ROOT` | 控制节点、构建节点、执行节点共同可访问的绝对路径 |
| `RSIFORME_PODMAN_BINARY` | 计算节点实际可执行的 Podman 路径 |
| `RSIFORME_APPTAINER_BINARY` | 计算节点实际可执行的 Apptainer 路径 |
| `RSIFORME_NODE_TMP` | 站点允许使用、容量足够的节点临时目录 |
| `RSIFORME_TASK_WORKDIR` | 任务镜像实际的 `WORKDIR` |
| `RSIFORME_TASK_PYTHON` | 任务镜像内实际的 Python 路径，与控制节点 Python 不同 |

模板中的构建 CPU/RAM/临时空间是示例请求。Task 的 Work/Judge 资源来自 `task.toml`；站点 profile 的 `resources` 是下限及分配模型。`gpus_per_node = 1` 是单 GPU 起步值，多 GPU 任务需要填入实际可用数量。`constraint = "a100"` 只在站点确实定义该特征时有效。镜像、数据、Python、GPU 编号及计算精度都应记录在本次实验的环境身份中。

这些字段来自上游的 [Slurm 模板](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/cluster/slurm/profile.toml) 和 [严格配置模型](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/cluster/config.py)。本项目没有添加上游不存在的 `snapshot_strategy` 等配置项。

先解析资源和调度计划：

```bash
rsi-harness run /absolute/path/to/task \
  --agent oracle --cluster /absolute/path/to/site.toml --dry-run
```

这一命令只验证计划；之后仍需验证镜像构建、工作区保存、Judge、完整任务和恢复。在固定版本里，新运行的 `--dry-run` **要求 `--cluster`**。本地无集群新运行没有这个 dry-run 入口。

任务原始实现/参考解通过后，运行实际 Agent：

```bash
rsi-harness run /absolute/path/to/task \
  --agent codex --model MODEL --cluster /absolute/path/to/site.toml
```

`MODEL` 是操作者选择且 Agent 支持的模型，不是本项目预置的模型名。实际 Agent 运行需要其原生凭据配置。第一轮先给出有限的时间和提交预算；预算由任务和 Harness 原生参数控制。

中断后先检查恢复计划，再继续同一 run：

```bash
rsi-harness resume RUN_ID --cluster /absolute/path/to/site.toml --dry-run
rsi-harness resume RUN_ID --cluster /absolute/path/to/site.toml
```

`resume` 使用之前保存的任务和 Agent 配置；不能把新任务参数拼接到这个命令。上述入口与参数来自锁定版本 [CLI](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/cli.py)。

## 3. 在 Work 中保存候选，再提交

原生 `rsi-submit` 的职责是通知 Harness 冻结当前 Work 并运行 Judge。当前客户端只接受无参数提交、`--list` 和 `--help`，没有 `--candidate-id`。RSIforme 的关联在外层完成：

1. 保存候选代码、配置、父代与假设；写入持久的待提交记录。
2. 停止候选的服务/测量进程，确保代码和日志已经写完。
3. 调用原生 `rsi-submit`。Work 暂停，新的 Judge 完成测量后 Work 恢复。
4. 将返回的 `round: agent-N` 与提交前候选关联，保存原始输出。
5. 从同一 run 的该轮 `report.json` 和原始日志导入测量；检查任务自身的验收规则。

上述恢复步骤对应普通多轮模式。若任务启用一次性的 final Judge，Harness 会结束该次 Agent 执行；包装器可能来不及写回绑定。提交前档案与待提交记录仍应保留，再从宿主机的持久报告恢复关系。

以下身份分别保存：

| 字段 | 含义 |
| --- | --- |
| `candidate_id` | RSIforme 保存的精确源码候选 |
| `harness_run_id` | 启动 Harness 时产生的研究会话 ID |
| `round_id` | 该会话中的一次提交，如 `agent-2` |
| `eval_run_id` | 某些任务评价器额外产生的执行 ID |

原生 `rsi-submit` 返回文本没有 Harness run ID。它必须来自本次启动/运行记录，不能用评价器自己的 `run_id` 代替。RSIforme 提交包装器需要装进任务的 Work 可执行环境；首次接入需验证操作者记录的 run ID 如何传入该 Work。不能仅在控制节点安装本包就假定容器中也存在它。[来源：提交服务](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/submissions.py)、[原生客户端](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/integrations/submit_client.py)

核对过的 Work 启动链没有通用的 `RSI_RUN_ID` / `RSI_HARNESS_RUN_ID` 导出约定。首次运行显式提供 `--harness-run-id`，由控制器启动记录传递；不从 token、URL 或容器名猜测。宿主机的 `run-plan.json` 保留原始 `paths.root`，其 basename 在锁定的本地和集群后端都等于 run ID；`task.task_id` 同时保留任务身份。`read_run_identity()` 使用这些字段校验导入来源，因此复制日志目录后也不依赖新目录的名字。[来源：本地启动](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/production.py)、[集群计划](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/cluster/bluevela/adapter.py)

### 提交结果不确定时

客户端异常、终端丢失或响应截断，都不能证明服务器没有接受该轮。保留待提交记录，从 **同一 Harness run** 的 `rsi-submit --list` 和宿主机持久记录恢复关联，然后显式绑定。没有建立映射前不要自动重提。适配器拒绝多个 feedback block，防止把 history 整段当成单次返回。

当前首版对每个候选源树要求单写入者。归档完成后至提交期间，不得让另一个 worker 改写相同目录。并行研究线应有独立 Work/源码目录；靠候选 ID 不能解决并发写入同一树的问题。

### 候选范围与归档位置

任务允许修改的范围和候选档案的范围需要显式确定。只归档交付所需源码、配置与研究记录；权重、缓存和数据使用固定引用。档案目录不能包含在其自身的归档范围内。

例如锁定的 CosyVoice 任务只允许 `/workspace/sglang-omni` 和 `/workspace/research` 两个顶层项。不能随意增加 `/workspace/.rsiforme`。可把本项目工具和档案放在 `/workspace/research` 中，再显式归档候选源码及四个研究交付文件，排除工具/档案自身。`hypothesis.md`、`experiments.json`、`reproduction.json`、`source.diff` 仍需满足任务各自的规则。这个位置选择由该任务的 [scope 实现](https://github.com/OpenRSI-Foundation/OpenRSI-AI-Infra-Bench/blob/949f201b7854be14b884d2ddcd3d3f63a590361c/tasks/cosyvoice3-dual-mode/tests/common.py) 支持，其他任务需要重新核对。

原生 Work 文件持久化不等于控制器之外的永久保存。将包含档案的 Work 导出/同步到持久存储并验证哈希后，再清理运行。完整模型仓库很大时，后续可增量保存；首版以可核查的精确文件快照为准。

## 4. 读取评价与最好候选

适配器的文件入口是：

```text
<logs-root>/runs/<harness-run-id>/<task-id>/
  evolve_state.json
  run-plan.json
  final_result.json
  submissions/<round-id>/report.json
  submissions/<round-id>/test_output.txt
```

`read_best_report()` 从 `final_result.json.best_round` 定位同轮 report，取其中的 `metrics`。它同时核对该轮已记录在 `evolve_state.json`，并核对 score、状态、metrics，避免把中断时只写出一半的记录当作完成。

**不能使用 `final_result.json.best_rewards` 作为一个候选的指标向量。**锁定的上游分别选择每个 key 的最好值，可能拼接多个不同候选；例如一个候选最快，另一个候选质量最好。`read_round_report()` 可导入指定轮，包括失败记录。[来源：RunArtifactWriter](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/artifacts.py)

Native `completed` 表示评价过程完成，不保证任务质量约束通过。无效候选也可能得到已完成但 reward 为 0 的评价。应按冻结的任务规则区分“有结果”“达到验收”和“超过基线”；`infrastructure_error` 需要作为环境失败保留，不能改写为算法零分。

最后，将已绑定最好轮次的源码恢复到新的工作目录，按相同合同再测；通过后再发布候选。Harness 默认保留的最后 Work 不一定是最好版本，临时 Judge snapshot 也不是永久候选档案。

## 5. 本轮验证边界

`tests/test_harness.py` 使用明确标注的合成接口样例，覆盖同轮取值、原生错误状态、截断/歧义响应、状态持久化边界、路径越界及原生 CLI 组合。测试不调用模型、不调度 GPU，不声称复现上游推理任务。`configs/slurm.example.toml` 的字段已逐项对照公开源码，目标站点参数和环境仍待实际绑定。
