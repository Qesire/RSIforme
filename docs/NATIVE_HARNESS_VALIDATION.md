# 原生 RSI-Harness 验收

本验收把 RSIforme 接到固定版本的真实 Work/Judge，验证候选源码能与原生提交和测量一一对应。执行入口是 GitHub Actions 的 **Native RSI-Harness integration**；一次成功的运行应保留 `verified.json`、完整原生日志和从 Work 导出的候选档案。只有工作流实际通过，才把对应提交标为原生集成已验证。

## 验收覆盖

| 阶段 | 实际执行 | 通过条件 |
| --- | --- | --- |
| 固定依赖 | 检出 OpenRSI-Index `486eec2b93296c514238f242f172703c3736ccc1`，安装原生 Python 包 | 检出的 SHA 与锁文件一致，实际导入该 checkout 的 compiler |
| 原生任务编译 | 调用原始 `HarborTaskCompiler`；用实际上游模型读取 Slurm 模板 | 正确解析 CPU 任务、资源、主指标和普通/单次最终 Judge 模式 |
| 原生 oracle | 未修改的 `rsi-harness run --agent oracle`，两个 Judge 轮次 | 原生状态完成、正确性通过、正 reward、实际测量源码等于已知参考解 |
| 候选归档与提交 | 上游验收脚本 Agent 依次运行 baseline、optimized 和 incorrect | 每次先归档，再触发原生 `rsi-submit`；错误候选被拒绝 |
| 丢失客户端返回 | 第三个提交由子进程调用原生服务，收到真实结果后以 86 退出，尚未绑定 round | pending 保留；查询同一原生 run 的提交历史，唯一确定新增 round，恢复绑定；没有重提 |
| 最好候选恢复 | 从档案恢复选中源码，覆盖已失败的当前实现 | 恢复源码身份不变，最后工作区不决定最好版本 |
| 独立确认与发布 | 第四个真实 Judge 轮次确认同一候选，再发布 | 搜索与确认轮次不同，源码/上下文相同，有 CURRENT 和发布记录 |
| 宿主机核验 | 导出 Work 档案，读取原生每轮报告和 Judge 自己记录的源码摘要 | 所有 candidate ↔ run/round ↔ source SHA 关系一致，无 pending |

该工作流用预写候选检验工程交接，原生 Docker、快照、网络策略、提交服务、Judge、reward 解析和持久报告都实际执行。Agent 发现能力和真实模型任务的收益需要各自的运行证据；这里没有模型 API 调用。

## 为什么有两个运行

第一个运行保留原始 oracle 路径，确认项目任务包能被真正的 Harness 运行。固定版本的 oracle 会执行 `solution/solve.sh`，随后自动提交指定次数；`--max-submissions 1` 则进入另一种最终 Judge 模式。因此这里使用两次提交，覆盖暂停 Work、独立 Judge、返回反馈并继续的普通路径。

第二个运行使用上游仓库自身的 [`tests/acceptance/scripted_cli.py`](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/tests/acceptance/scripted_cli.py)。它只把编码 Agent 的模型命令和安装替换为脚本，原始 CLI 和运行服务继续处理生命周期。RSIforme 在其 `prepare` 边界把 `request.run_plan.paths.root.name` 作为真实 Harness run ID 传入 Work，同时记录实际镜像和任务摘要。这里没有额外发明 `--run-id` 参数。

脚本结束后，适配器用请求中已有的 `container_id` 导出 `/workspace/research/archive`，随后仍由原生控制器清理运行资源。宿主机校验读取 `run-plan.json` 确认运行身份，再把每轮 Judge 的 `candidate/<eval_uuid>/report.json.source_sha256` 与提交前档案中的 `solution.py` 摘要核对。这样，验证覆盖了归档内容与实际被测内容的连接。

丢失返回属于明确注入的客户端进程故障，incorrect 属于算法负对照，两者分别记录。恢复先读取提交前后原生 history 的 round 集合，只在恰好新增一个 round 时绑定。它不根据候选顺序猜轮号，也不重复触发 Judge。保留的 `fault.json` 与前后 history 能检查这一过程。

## 本地与 CI 入口

要求 Linux、可工作的 Docker、Python 3.12 或 3.13，以及该主机上的原生 Harness 网络管理权限。CPU 任务不需要 GPU 或 Apptainer。固定版本支持 root 控制器管理主机 iptables，或 Docker Engine 26+ 的非 root 控制器；当前工作流使用托管 Ubuntu runner 的 sudo 路径。[上游要求](https://github.com/OpenRSI-Foundation/OpenRSI-Index/tree/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness#requirements)

工作流安装两个独立包的实际源码，保存 `pip freeze` 和 `docker info`。它设置 `RSIFORME_REQUIRE_NATIVE=1`，所以缺失原生依赖会失败。平常仅使用标准库的单元测试环境可以明确跳过这些依赖测试，跳过记录不计入原生验收。

原生 oracle 命令的核心是：

```bash
rsi-harness run /absolute/path/to/tasks/jsonl-aggregate \
  --agent oracle --max-submissions 2 --timeout 420 --primary-reward reward \
  --data-root /absolute/path/to/runtime/oracle \
  --logs-root /absolute/path/to/evidence/oracle/logs --verbose

python scripts/verify_harness_run.py verify --mode oracle \
  --output /absolute/path/to/evidence/oracle
```

原生包装器运行：

```bash
python scripts/verify_harness_run.py scripted \
  --upstream /absolute/path/to/OpenRSI-Index \
  --task /absolute/path/to/tasks/jsonl-aggregate \
  --data-root /absolute/path/to/runtime/scripted \
  --output /absolute/path/to/evidence/scripted

python scripts/verify_harness_run.py verify --mode scripted \
  --output /absolute/path/to/evidence/scripted
```

这些命令在符合上游要求的主机执行。`verify` 发现实际运行目录、读取持久文件并要求原生证据存在；它不会生成替代报告来凑通过结果。完整 sudo 调用和临时目录隔离方式见 `.github/workflows/harness-integration.yml`。

## 证据和结果解释

Actions artifact 名称为 `native-harness-cpu-<workflow-run-id>`，保留期为 14 天。包含两次运行的 console、run-plan、每轮原生 report、Judge 原始产物、导出的源码档案以及校验后生成的 `verified.json`。失败也上传已有日志，便于根据第一处真实错误修复。包含服务 token 和租约的 runtime 目录不纳入上传。

`verified.json` 保留每轮原始 reward、quality、状态、源码/环境身份。CPU 噪声会改变测得的加速比，因此验收要求正确结果和正的实测 reward，不把某个固定加速倍数当成通用门槛。错误候选必须被拒绝。

这项验收仍没有覆盖 Slurm/A100、长时间研究、中断后的原生 Agent 会话恢复、多站点并发或自更新研究策略。那些能力需要对应任务、资源和故障场景的真实验收。当前 native job 的实际状态以该提交的 Actions 结果为准，不能用工作流文件存在替代运行通过。

## 关键上游依据

- [原生 oracle 语义与成功条件](https://github.com/OpenRSI-Foundation/OpenRSI-Index/tree/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness#validate-a-tasks-judge-with-the-oracle)
- [原始 Agent 验收脚本](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/tests/acceptance/scripted_cli.py)
- [真实 CLI 和参数](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/cli.py)
- [生产运行服务及原生 RunPlan](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/production.py)
- [原生持久报告与同轮指标](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/runtime/artifacts.py)
