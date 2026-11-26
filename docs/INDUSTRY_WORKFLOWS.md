# 从真实流程迁移的设计

本项目以公开源码和作者运行记录确定工程职责。下表区分已经观察到的流程、可迁移部分及公开材料边界；上游成绩属于作者的环境，本仓库没有复现其 GPU 搜索。

| 来源与固定版本 | 公开流程和证据 | RSIforme 采用的部分 | 边界 |
| --- | --- | --- | --- |
| [ALE / DECam][ale]，`572082b` | 研发控制、候选历史、最好程序进入档案扫描，结果再经独立核验 | 可恢复状态、永久程序档案、发布版本与负载绑定 | 私有 launcher、数据及部分轨迹未完整公开 |
| [NVIDIA Sol-Engine][nv]，`1006b6a` | LingBot 的 GOAL、JOURNAL、STATUS、REPORT 记录真实作业、失败和保留结果 | 目标合同、profiling、局部试验、全链复测和交付记录 | 公开 `plan_eval()` 仍需外层实现；完整原始输出未随树发布 |
| [RSI-Harness][harness]，`486eec2` | 持久 Work、提交、独立 Judge、反馈、恢复与集群后端 | 作为生产任务执行底座 | 不负责永久候选源码档案；需补齐版本恢复 |
| [SoL-Pi v1][solpi] | 从执行轨迹提出方向，在隔离支线内实现并由 reviewer 检查 | 一条方向对应一个有界循环，组合后再验收 | 公开仓库主要是最终扩展与运行接口 |
| [ERNIE Evolve][ernie]，2026-11-09 | Director、Selector、Executor、Evaluator、Reviewer 分工，历史进入经验库 | 方向选择、父代选择和评价职责分开 | 官方说明与结果资产不等于完整开放控制器 |

## 两条直接影响实现的经验

**先明确测量对象。**[LingBot 日志][journal]先分解真实阶段耗时，再用实际 attention 形状比较后端。局部最快的实现首次全链运行遇到不支持形状，补齐回退路径后重新完成评价。加载优化曾经有效，但目标切换为热推理后另行记录；旧成绩没有混入最终同口径比较。这对应我们的固定任务合同、完整结果和失败证据。

**候选、最好成绩和最终交付分别登记。**ALE 将最好程序发布给新负载，已领取任务继续使用自身程序版本；[SGLang-Omni PR #2524][pr]把研究产物整理为单项 ONNX 线程配置改动，经过准确版本复测和维护者审查后合并。我们的候选档案先支持恢复，再用独立复测形成发布记录。

## 采用时保留的证据边界

ALE r2 的当前任务向 Agent 开放标签和完整反馈；独立执行不自动意味着隐藏测试。Harness 的提交接口不接受自定义 candidate ID，轮次关联必须在外层完成。其逐指标 `best_rewards` 也不代表一个实际候选。学校 A100 的任务需重新测量基线，不能沿用 GB200 或 H100 的数值。首次集群接入步骤见 [Harness 接入文档](HARNESS_INTEGRATION.md)。

[ale]: https://github.com/huangzesen/decam-rsi-pipeline/tree/572082bab9d18396b0f1ce607046baeed32dd159
[nv]: https://github.com/NVlabs/Sana/tree/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt
[journal]: https://github.com/NVlabs/Sana/blob/1006b6a219b48e5798eec89478e7d12c101de661/models/lingbot_video/optimized/lingbot_src/agent_opt/JOURNAL.md
[harness]: https://github.com/OpenRSI-Foundation/OpenRSI-Index/tree/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness
[solpi]: https://arxiv.org/html/2609.20519v1
[ernie]: https://ernie.baidu.com/zh/research/posts/ernie-evolve/
[pr]: https://github.com/sgl-project/sglang-omni/pull/2524
