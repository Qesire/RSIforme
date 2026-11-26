# Working in RSIforme

RSIforme is a thin research layer around the pinned RSI-Harness. Read README.md,
docs/ARCHITECTURE.md and program.md before changing the workflow.

- Keep task contracts, evaluator revisions and runtime context explicit. A changed
  objective or environment starts a new archive; previous results retain their scope.
- Save exact candidate source/config before evaluation. Preserve parent, lane,
  hypothesis and all evaluation evidence, including failures.
- Bind each Harness round to its captured candidate. `best_rewards` can combine
  several rounds; use the selected round's report instead.
- Keep candidate source separate from the evaluator. Local subprocess tests are
  integration checks, not a sandbox for adversarial code.
- Preserve an unknown submission as pending. Inspect the same run's history before
  recovering its round; do not automatically submit again.
- Promotion requires fresh confirmation of the same candidate and task context.
- Verify behavior with `PYTHONPATH=src python -m unittest discover -s tests -v` and
  run the CPU demo after changes to the full lifecycle. Do not invent GPU results.
- Do not commit credentials, private datasets, weights, cluster bindings or raw runs.
  Record useful measured evidence in docs/IMPLEMENTATION_RECORD.md instead.
