# 将晋升结果交付为独立目录

`CURRENT.json` 标识已经晋升的结果。`export_release` 把该结果的源码、任务契约和原始证据复制为可搬移的交付目录。它只接受已存在的 promotion，不会把搜索榜首自动视为发布，也不会重新评测或修改归档。

```python
from pathlib import Path
from rsiforme.archive import Archive
from rsiforme.delivery import export_release, verify_release

result = export_release(Archive("runs/my-task"), Path("deliveries/release-001"))
print(result["release_id"], result["manifest_sha256"])
print(verify_release("deliveries/release-001"))
```

CLI 入口为 `python -m rsiforme export --store runs/my-task --to deliveries/release-001` 和 `python -m rsiforme verify-release --directory deliveries/release-001`。

目标必须是归档之外尚不存在的目录。默认选择 `CURRENT.json` 指向的 promotion；传入 `release_id="r-..."` 可以导出历史晋升。导出先在临时目录完成复制和校验，再刷新文件、目录并原子改名。已有目标不会被覆盖。

## 交付内容

| 路径 | 内容 |
| --- | --- |
| `source/` | 晋升候选的完整捕获范围，保留目录结构、文件内容和可执行位 |
| `task.json` | 冻结的 RSIforme 任务契约及其 context fingerprint |
| `records/` | 候选、promotion、选定 search 和 confirmation 的原始 JSON；原生 Harness 记录另含对应 submission |
| `evidence/search/`、`evidence/confirmation/` | 两次选定评测各自封存的全部证据 |
| `records/attachments/`、`evidence/attachments/` | 后续补存的证据及其原始 manifest，不替换原评价 |
| `source.diff` | 存在父代时生成的差异；二进制文件等以 `source/` 的完整目标内容为准 |
| `provenance.json` | release、candidate、source、context、评价与证据的对应关系；可选的上游版本来自冻结任务的 `context.upstream_pins` |
| `manifest.json`、`verify.py` | 每个导出文件的 SHA256、大小、权限和完整目录清单，以及独立校验程序 |
| `REPRODUCE.md`、`reproduce.py` | 复查说明；拥有封存本地 evaluator 的发布额外附可运行的复查程序 |

导出调用 `Archive.verify_evidence`，拒绝缺失、被改写或没有原始封存清单的证据。原始 search 和 confirmation 必须属于同一候选与任务，类型不同、执行不同，且均为完成并有效的结果；独立 verifier 还核对实际执行先后与 `execution.confirmation_of` 指向的准确 search。旧记录没有相应证明时，不能通过事后导出补出它当时的完整性或因果关系。

当前发布包围绕选中候选保存 search、confirmation 及证据。完整研究计划、profile、baseline、其他失败候选和预算历史仍在 Archive 的 `research/` 等目录与运行摘要中；需要交接研究全过程时，应同时提供它们的归档或索引。

## 搬移后校验与实测

进入收到的目录后，原归档、原工作区与 RSIforme 安装均可不存在：

```bash
python3 verify.py
```

该命令只依赖 Python 标准库，验证全部文件哈希、源代码清单、冻结契约、选定评价和证据之间的绑定。新增、缺失、改写的文件都会导致失败。交付时保存导出返回的 `manifest_sha256`，可额外对照收到的 manifest 是否还是同一份。

本地 `evaluate_local` 记录包含封存的 evaluator、显式声明的 `task.evaluator_assets` 和 `reproduction` 描述。符合这一契约时，可以实际重新执行：

```bash
python3 reproduce.py ../release-recheck-001
```

输出目录必须是发布目录之外的新目录。程序先校验发布，复制源码作为新的工作区，再加载随 confirmation 封存的 evaluator，调用 `evaluate_candidate(snapshot_dir, output_dir)`。新的源码、原始运行证据和 `reproduction_result.json` 都保存在输出目录；原发布保持不变。返回有效且完成的评测时退出码为 0，评测报错或拒绝时为 1。使用冻结任务规定的 Python、依赖、数据和硬件，才能讨论测量可比性。

原生 RSI-Harness 评测保留任务引用、上游 pins、原始结果和证据。其复查仍需准备所引用的 Harness 任务与 Work/Judge 环境，然后恢复 `source/` 并进行新的 Judge 评测。导出器不会从一个运行目录名推测集群、数据或启动命令。

哈希校验回答“交付内容是否仍是封存的内容”；新的执行回答“此环境能否再次得到这个结果”。两者分别保留记录，新的复查不会改写原始 search、confirmation 或 promotion。

## 已验证的交付边界

`tests/test_delivery.py` 真实执行 Python 候选和带数据依赖的 evaluator：导出后搬移目录，删除原归档、原源码和原 evaluator，再以 `python -I -B` 独立校验并重新执行。测试还覆盖搜索榜首与晋升结果的区别、证据附件、原始证据损坏、交付后改写和已有目录保护。测试中的整数分值仅用于验证流程。
