# RSI-Harness CPU task package

This package follows Harbor schema `1.4` as accepted by the pinned
[RSI-Harness compiler](https://github.com/OpenRSI-Foundation/OpenRSI-Index/blob/486eec2b93296c514238f242f172703c3736ccc1/RSI-Harness/src/rsi_harness/task/compiler.py).
The compiler explicitly supports `environment.gpus = 0`; verifier GPU count is
also zero. The fixed test entry writes `/logs/verifier/reward.json` only after
valid measurements, following the
[upstream evaluator](https://github.com/OpenRSI-Foundation/OpenRSI-AI-Infra-Bench/blob/949f201b7854be14b884d2ddcd3d3f63a590361c/tasks/cosyvoice3-dual-mode/tests/engine.py).

`environment/Dockerfile` installs the starting code and the stdlib RSIforme
package under `/opt/rsiforme`. No package installation or network access is
needed inside Work. The generated `archive-task.toml` selects only `solution.py`,
pins the trusted evaluator's SHA-256, optimizes `reward`, and requires the
`quality_passed=1` gate. It does not put the trusted evaluator in the Work image.
`solution/solve.sh` supplies the hand-written optimized oracle.
`tests/test.sh` launches the fixed evaluator.
It measures the trusted original implementation and then the submitted candidate
on the same machine. Both raw reports and logs are retained under the verifier
log directory. Correctness failures do not publish a valid reward.

The immutable research inputs are the task files, benchmark seed and data
generation, reference answers, and evaluator. The local CPU example uses the
same evaluator; `sync_assets.py --check` verifies byte equality of duplicated
files needed by the container build/test contexts, the generated archive config,
and the bundled RSIforme source. Run `sync_assets.py` again after source changes;
do not edit generated copies in `environment/rsiforme`.

```bash
python tasks/jsonl-aggregate/sync_assets.py --check
# Pinned new-run --dry-run requires a configured cluster profile:
rsi-harness run "$PWD/tasks/jsonl-aggregate" --agent oracle --cluster /absolute/path/to/site.toml --dry-run
# With the local container runtime and the pinned Harness installed:
rsi-harness run "$PWD/tasks/jsonl-aggregate" --agent oracle
```

The task package has been statically checked and its evaluator exercised in local
subprocesses. **A complete container Work/Judge run remains unverified** until a
machine with the supported runtime executes it. The current repository bootstrap
does not imply that Docker, Slurm, or GPU jobs were run.

The image uses a public Python tag for convenient smoke setup. Before comparing
production results, pin the resolved digest, record the host environment, and
establish the baseline on that target system. This CPU package carries no claim
about A100 performance. In an Agent run, follow `instruction.md` to initialize
`/workspace/research/archive` and submit through the installed RSIforme wrapper.
The controller must provide the actual Harness run ID; it is distinct from an
evaluator's UUID. The native `rsi-submit` interface is the underlying evaluation
request and by itself does not add permanent candidate archives.

The supplied `--agent oracle` checks the execution and Judge path with a known
solution. It does not run the archive wrapper or demonstrate autonomous research.
Initialize a separate archive for a new task version, image, or hardware context.
