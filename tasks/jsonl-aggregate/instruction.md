# Exact grouped aggregation

Improve `/workspace/solution.py` while preserving the following contract.

`aggregate(records)` receives a list of dictionaries with a string `group` and an
integer `value`. Return one dictionary per group, sorted in ascending Python
string order, with exactly `group`, integer `count`, and exact integer `sum`.
Empty input must return `[]`. Negative values, empty strings, Unicode group names,
and integers beyond the exact range of floating point are valid inputs.

Only `solution.py` may affect the submitted implementation. You may keep research
notes under `/workspace/research`. Do not modify the evaluator, system software,
or baseline. The candidate must not change files or create background processes.

The public evaluator checks exact outputs on edge cases and deterministic data,
then measures three fresh-process executions of a 40,000-row, 128-group input.
Its objective is rows per second, including Python startup, JSONL parsing,
aggregation, and output serialization. The Judge remeasures the original baseline
on the same machine and returns `reward = candidate_rows_per_second /
baseline_rows_per_second`. A correct baseline therefore has reward near 1; CPU
noise can change the measured value. Incorrect output receives no valid reward.

State the bottleneck, expected effect, source change, and reproduction command in
`research/hypothesis.md`. RSIforme is installed in this Work image. Initialize its
per-run archive before changing the starting solution:

```bash
python -m rsiforme init --store /workspace/research/archive --task /opt/rsiforme/archive-task.toml
```

The outer controller supplies the **actual Harness run ID**. Store it in the
shell variable `HARNESS_RUN_ID`; do not invent a value or substitute the evaluator
UUID. Submit each candidate through the archive wrapper:

```bash
python -m rsiforme submit --store /workspace/research/archive --workspace /workspace --harness-run-id "$HARNESS_RUN_ID" --lane single-pass --hypothesis "Accumulate exact counts and sums in one pass, then sort group keys"
```

The wrapper saves the selected source before invoking the underlying native
`rsi-submit`, then binds the returned round ID to that candidate. `research` notes
and archives remain outside the captured source selection (`solution.py`). The
Judge publishes `quality_passed=1` only after exact correctness checks pass;
RSIforme requires that gate alongside the measured reward.

For a confirmation run, first restore the selected candidate into the Work
workspace, then submit with `--candidate CANDIDATE_ID --kind confirmation`. The
wrapper verifies that the live source still matches that archived candidate.
Use the controller's supplied run ID for this submission as well. An uncertain
submit result must be reconciled against the existing Harness history before a
new submit; do not automatically repeat it.

The supplied oracle `solution/solve.sh` is an execution-path check. It installs
the known solution for Harness's final Judge and does not exercise the archive
wrapper. A full Agent run through the instructions above exercises that layer.

This task validates the research workflow. Its candidates and small workload do
not establish production speedups or recursive improvement of the researcher.
