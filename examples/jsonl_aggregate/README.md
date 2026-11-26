# CPU 集成任务：JSONL 分组聚合

这个任务让候选保存、独立执行、正确性门槛、测量、恢复与复测都有真实输入输出。三个候选由项目作者预先编写，**不代表 Agent 自动发现了算法，也不代表 GPU 任务已跑通**。

## 合同

`solution.py` 提供 `aggregate(records)`。每条输入是 `{"group": str, "value": int}`；返回按 group 的 Python 字符串顺序排列的列表，每项含 `group`、精确整数 `count` 和精确整数 `sum`。空输入返回空列表；负数、空 group、Unicode group 和超出浮点精确范围的整数都属于输入域。

| 候选 | 作用 |
| --- | --- |
| `fixtures/baseline/solution.py` | 对每个 group 重扫输入，作为可验证起点 |
| `fixtures/incorrect/solution.py` | 故意丢弃负值，检查快速但错误的候选会被拒绝 |
| `fixtures/optimized/solution.py` | 单次扫描累积，再排序，作为可恢复的正确改进 |

## 直接执行

从仓库根目录运行：

```bash
python examples/jsonl_aggregate/evaluate.py examples/jsonl_aggregate/fixtures/baseline runs/example-baseline
python examples/jsonl_aggregate/evaluate.py examples/jsonl_aggregate/fixtures/optimized runs/example-optimized
```

返回报告与新建 UUID 证据目录中的 `report.json` 相同。每轮目录独立保存；`stdout`、`stderr` 和错误输出差异都可追溯。错误候选返回非零退出码，报告保留 `status=completed, valid=false`，与评价基础设施错误 `status=infra_error` 分开。

评价器在父进程建立预期答案并计时。每次调用启动新的 Python 子进程，在临时目录加载已复制的候选字节。先检查边界输入，再用固定种子生成 40,000 行、128 个 group 的输入；预热一次，测量三次并取耗时中位数。目标 `rows_per_second` **包含进程启动、JSONL 读取、函数执行与结果写出**。数据生成和父进程的答案校验不计入测量时间。

这是共享 CPU 上的端到端冒烟测量；它检验流程，不提供稳定的生产性能基线。报告保留三个样本的时间范围、Python 与平台版本及源码/评价器摘要。实际 GPU 任务应另立目标硬件基线、固定软件和数据，再做足够次数的复测。

临时工作目录、独立进程与超时不构成不可信代码的安全沙箱。这里运行项目自带的可信候选；正式 Agent 任务的隔离由 RSI-Harness 的 Work/Judge 执行层负责。
