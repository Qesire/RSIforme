"""Thin workflow glue. Scheduling and Work/Judge remain RSI-Harness's job."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

from .archive import Archive, ArchiveError, atomic_json, file_sha, now, _relative_path, load_json
from .harness import parse_submit_output, read_best_report, read_round_report, read_run_identity


_LOCAL_DRIVER = r'''
import importlib.util
import json
from pathlib import Path
import sys
import traceback
import uuid

entry, source, output, destination = map(Path, sys.argv[1:])
sys.path.insert(0, str(entry.parent))
try:
    spec = importlib.util.spec_from_file_location("rsiforme_frozen_evaluator", entry)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module.evaluate_candidate(source, output)
    if not isinstance(result, dict):
        raise TypeError("evaluate_candidate must return a dictionary")
except Exception as exc:
    traceback.print_exc()
    result = {"status": "infra_error", "valid": False, "metrics": {},
              "eval_run_id": "local-" + uuid.uuid4().hex,
              "error": type(exc).__name__ + ": " + str(exc)}
destination.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
'''


def evaluator_assets(archive: Archive, evaluator: Path, destination: Path) -> dict:
    """Freeze declared evaluator code/data dependencies before loading any code."""
    revision = "sha256:" + file_sha(evaluator)
    if archive.config["task"]["context"]["evaluator_revision"] != revision:
        raise ArchiveError("Evaluator bytes differ from the frozen task context")
    assets = dict(archive.config["task"].get("evaluator_assets", {}))
    if evaluator.name in assets and assets[evaluator.name].removeprefix("sha256:") != revision[7:]:
        raise ArchiveError("Evaluator entry disagrees with declared assets")
    assets[evaluator.name] = revision[7:]
    destination.mkdir(parents=True, exist_ok=False)
    normalized = {}
    for name, digest in assets.items():
        name = _relative_path(name)
        source = evaluator.parent / name
        if source.is_symlink() or not source.resolve().is_relative_to(evaluator.parent):
            raise ArchiveError("Evaluator assets must be ordinary files within its declared root")
        digest = digest.removeprefix("sha256:")
        if file_sha(source) != digest:
            raise ArchiveError(f"Evaluator dependency changed: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if file_sha(target) != digest:
            raise ArchiveError(f"Evaluator dependency changed during copy: {name}")
        normalized[name] = digest
    return normalized


def confirmation_target(archive: Archive, candidate_id: str, requested: str | None = None) -> str:
    records = [r for r in archive.evaluations() if r["candidate_id"] == candidate_id
               and r["kind"] == "search" and r["valid"] and r["status"] == "completed"]
    if requested is not None:
        records = [r for r in records if r["id"] == requested]
    if not records:
        raise ArchiveError("Confirmation requires an existing valid search evaluation of this candidate")
    selected = records[-1]
    if not selected.get("execution"):
        raise ArchiveError("Legacy search has no execution provenance; run a fresh search before confirmation")
    return selected["id"]


def evaluate_local(archive: Archive, candidate_id: str, evaluator: str | Path,
                   *, kind: str = "search", workspace: str | Path | None = None,
                   execution_id: str | None = None, confirmation_of: str | None = None,
                   timeout_seconds: float | None = None) -> dict:
    """Run a frozen, trusted evaluator against archived source.

    The evaluator controls its own candidate isolation. This wrapper is for
    local contract testing, not execution of untrusted code on a shared host.
    """
    evaluator = Path(evaluator).expanduser().resolve()
    revision = "sha256:" + file_sha(evaluator)
    timeout = timeout_seconds if timeout_seconds is not None else archive.config["task"].get("evaluation_timeout_seconds", 120)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ArchiveError("Local evaluator timeout must be finite and positive")
    target = confirmation_target(archive, candidate_id, confirmation_of) if kind == "confirmation" else None
    source = archive.source(candidate_id)
    manifest = archive.candidate(candidate_id)
    if workspace is not None:
        if not archive.matches_workspace(candidate_id, workspace):
            raise ArchiveError("Evaluation workspace differs from the captured candidate")
        source = Path(workspace).resolve()
    with tempfile.TemporaryDirectory(prefix="rsiforme-local-evidence-") as temporary:
        output = Path(temporary)
        frozen = output / "frozen-evaluator"
        assets = evaluator_assets(archive, evaluator, frozen)
        driver = output / "trusted_driver.py"
        driver.write_text(_LOCAL_DRIVER, encoding="utf-8")
        result_path = output / "driver-result.json"
        driver_stdout = output / "driver.stdout.log"
        driver_stderr = output / "driver.stderr.log"
        started_at = now()
        timed_out = False
        with driver_stdout.open("wb") as stdout, driver_stderr.open("wb") as stderr:
            process = subprocess.Popen([sys.executable, "-I", "-B", str(driver), str(frozen / evaluator.name),
                                        str(source), str(output), str(result_path)],
                                       cwd=frozen, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if timed_out or process.returncode != 0 or not result_path.is_file():
            result = {"status": "infra_error", "valid": False, "metrics": {},
                      "eval_run_id": "local-" + uuid.uuid4().hex,
                      "error": "evaluator timeout" if timed_out else f"evaluator process exited {process.returncode}"}
            atomic_json(result_path, result)
        else:
            result = load_json(result_path)
        finished_at = now()
        if "sha256:" + file_sha(evaluator) != revision:
            raise ArchiveError("Evaluator changed during execution; result was not accepted")
        archive.source(candidate_id)  # Recheck the immutable bytes after evaluation.
        if workspace is not None and not archive.matches_workspace(candidate_id, workspace):
            raise ArchiveError("Evaluation workspace changed during execution")
        result.update(source_digest=manifest["source_digest"],
                      context_fingerprint=manifest["context_fingerprint"])
        result["execution"] = {"id": execution_id or "local-" + uuid.uuid4().hex,
                               "started_at": started_at, "finished_at": finished_at,
                               "confirmation_of": target}
        evidence_dir = Path(result.pop("evidence_dir", str(output))).resolve()
        if not evidence_dir.is_relative_to(output):
            raise ArchiveError("Evaluator evidence must be inside its supplied output directory")
        evidence_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(frozen, evidence_dir / "evaluator")
        for path in (driver_stdout, driver_stderr, result_path):
            if path.parent != evidence_dir:
                shutil.copyfile(path, evidence_dir / path.name)
        # The loading copy is not itself an output artifact if the evaluator returns output root.
        shutil.rmtree(frozen)
        result["reproduction"] = {"adapter": "local-python", "entrypoint": "evaluator/" + evaluator.name,
                                  "callable": "evaluate_candidate", "assets": assets,
                                  "python": platform.python_version(), "timeout_seconds": timeout}
        atomic_json(evidence_dir / "rsiforme-binding.json", {
            "candidate_id": candidate_id, "source_digest": manifest["source_digest"],
            "context_fingerprint": manifest["context_fingerprint"], "kind": kind,
        })
        return archive.record_evaluation(candidate_id, result, kind=kind, evidence=evidence_dir)


def _quality_gates(task: dict, metrics: dict) -> tuple[bool, list[str]]:
    gates = task.get("harness", {}).get("gates")
    if not isinstance(gates, list) or not gates:
        raise ArchiveError("Harness import needs explicit task.harness.gates; completed alone is not quality acceptance")
    failures = []
    for gate in gates:
        if not isinstance(gate, dict) or not isinstance(gate.get("metric"), str):
            raise ArchiveError("Each Harness gate needs a metric name")
        constraints = [key for key in ("min", "max", "equals") if key in gate]
        if not constraints or set(gate) - {"metric", "min", "max", "equals"}:
            raise ArchiveError("Harness gates support metric and min/max/equals constraints")
        for key in constraints:
            limit = gate[key]
            if isinstance(limit, bool) or not isinstance(limit, (float, int)) or not math.isfinite(limit):
                raise ArchiveError("Harness gate limits must be finite numbers")
        name = gate["metric"]
        if name not in metrics:
            failures.append(f"missing gate metric {name}")
            continue
        value = metrics[name]
        if any((key == "min" and value < gate[key]) or (key == "max" and value > gate[key])
               or (key == "equals" and value != gate[key]) for key in constraints):
            failures.append(f"gate failed: {name}={value}")
    return not failures, failures


def normalize_harness_result(archive: Archive, binding: dict, report: dict) -> dict:
    candidate = archive.candidate(binding["candidate_id"])
    native_status = report["status"]
    metrics = report["metrics"]
    if native_status == "completed":
        status = "completed"
        valid, reasons = _quality_gates(archive.config["task"], metrics)
        score = report.get("score")
        if score is None or not math.isfinite(score):
            valid = False
            reasons.append("no finite native score")
        objective = archive.config["task"]["objective"]
        if objective not in metrics:
            valid = False
            reasons.append(f"missing objective metric {objective}")
    else:
        status = "infra_error" if native_status == "infrastructure_error" else "failed"
        valid, reasons = False, [report.get("error") or native_status]
    return {"status": status, "valid": valid, "metrics": metrics,
            "source_digest": candidate["source_digest"],
            "context_fingerprint": candidate["context_fingerprint"],
            "harness_run_id": binding["harness_run_id"], "round_id": binding["round_id"],
            "native_status": native_status, "native_score": report.get("score"),
            "acceptance_failures": reasons,
            "execution": {"id": f"harness/{binding['harness_run_id']}/{binding['round_id']}",
                          "started_at": binding["created_at"], "finished_at": binding["bound_at"],
                          "confirmation_of": binding.get("confirmation_of")},
            "reproduction": {"adapter": "rsi-harness", "harness_run_id": binding["harness_run_id"],
                              "round_id": binding["round_id"]}}


def submit(archive: Archive, workspace: str | Path, *, harness_run_id: str,
           candidate_id: str | None = None, lane: str = "main", hypothesis: str | None = None,
           parent_id: str | None = None, kind: str = "search", confirmation_of: str | None = None,
           timeout_seconds: float | None = None) -> dict:
    """Invoke the native client once; a wait timeout never cancels the remote Judge."""
    if timeout_seconds is not None and (not isinstance(timeout_seconds, (int, float))
                                       or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                                       or timeout_seconds <= 0):
        raise ArchiveError("Native client wait timeout must be finite and positive")
    _quality_gates(archive.config["task"], {})  # Validate policy before spending an evaluation.
    if shutil.which("rsi-submit") is None:
        raise ArchiveError("rsi-submit is unavailable; run this command inside Harness Work")
    if candidate_id is None:
        if kind == "confirmation":
            raise ArchiveError("Confirmation must select an existing --candidate")
        candidate_id = archive.capture(workspace, lane=lane, hypothesis=hypothesis or "",
                                       parent_id=parent_id)["id"]
    if not archive.matches_workspace(candidate_id, workspace):
        raise ArchiveError("Work source differs from the selected candidate; restore it before submitting")
    intent = archive.begin_submission(candidate_id, harness_run_id, kind=kind, confirmation_of=confirmation_of)
    # If killed here or if the response is unclear, this intent remains pending.
    # Do not infer from a local exit code that the server did not accept the request.
    try:
        wait_options = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
        process = subprocess.run(["rsi-submit"], cwd=workspace, text=True, capture_output=True,
                                 check=False, **wait_options)
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired may carry bytes even when text=True. Keep partial
        # diagnostics, but never interpret a client timeout as a failed Judge.
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        transcript = stdout + ("\n[client stderr]\n" + stderr if stderr else "")
        transcript += "\n[rsiforme] Client wait timed out; the remote Judge may still be running.\n"
        attempt_path = archive.root / "evidence" / (intent["id"] + "-attempt.txt")
        attempt_path.write_text(transcript, encoding="utf-8")
        archive.write_views()
        raise ArchiveError(f"Native client wait timed out after {timeout_seconds}s; submission outcome is unknown; "
                           f"intent {intent['id']} remains pending. The remote Judge may still be running. "
                           "Inspect this run's history before recover; do not resubmit.") from exc
    except BaseException:
        archive.write_views()
        raise
    transcript = process.stdout + ("\n[client stderr]\n" + process.stderr if process.stderr else "")
    attempt_path = archive.root / "evidence" / (intent["id"] + "-attempt.txt")
    attempt_path.write_text(transcript, encoding="utf-8")
    try:
        parsed = parse_submit_output(process.stdout)
    except ValueError as exc:
        archive.write_views()
        raise ArchiveError(f"Submission outcome is unknown; intent {intent['id']} remains pending. "
                           f"Inspect this run's history before recover. {exc}") from exc
    if not archive.matches_workspace(candidate_id, workspace):
        archive.write_views()
        raise ArchiveError(f"Work source changed during submit; intent {intent['id']} remains pending for review")
    binding = archive.bind_submission(intent["id"], parsed["round_id"],
                                      note="Complete native rsi-submit response", feedback=transcript)
    result = normalize_harness_result(archive, binding, parsed)
    with tempfile.TemporaryDirectory(prefix="rsiforme-submit-evidence-") as temporary:
        evidence = Path(temporary)
        (evidence / "submit.txt").write_text(transcript, encoding="utf-8")
        atomic_json(evidence / "parsed-response.json", parsed)
        feedback_path = parsed.get("feedback_path")
        if feedback_path and Path(feedback_path).is_file():
            shutil.copyfile(feedback_path, evidence / "test_output.txt")
        evaluation = archive.record_evaluation(candidate_id, result, kind=kind, evidence=evidence)
    archive.write_views()
    return {"submission": binding, "evaluation": evaluation}


def import_harness(archive: Archive, run_dir: str | Path, harness_run_id: str,
                   *, round_id: str | None = None) -> dict:
    root = Path(run_dir).resolve()
    identity = read_run_identity(root)
    if identity["harness_run_id"] != harness_run_id:
        raise ArchiveError("Harness run ID disagrees with its durable run-plan.json")
    expected_task = archive.config["task"].get("harness", {}).get("task_id")
    if expected_task is not None and identity["task_id"] != expected_task:
        raise ArchiveError("Harness task ID disagrees with the frozen task context")
    report = read_round_report(root, round_id) if round_id else read_best_report(root)
    binding = archive.binding(harness_run_id, report["round_id"])
    verifier = root / "verifier" / report["round_id"]
    if verifier.exists() and (verifier.is_symlink() or not verifier.resolve().is_relative_to(root)):
        raise ArchiveError("Native verifier evidence must remain within the selected run")
    for previous in archive.evaluations():
        if (previous.get("harness_run_id"), previous.get("round_id")) == (harness_run_id, report["round_id"]):
            if (previous["metrics"] != report["metrics"] or previous["native_status"] != report["status"]
                    or previous.get("native_score") != report.get("score")):
                raise ArchiveError("Durable Harness report disagrees with the already imported response")
            archive.attach_evidence(previous["id"], Path(report["report_path"]).parent,
                                    label="harness-durable-round")
            if verifier.is_dir():
                archive.attach_evidence(previous["id"], verifier, label="harness-verifier-output")
            return {"already_recorded": True, "evaluation": previous}
    result = normalize_harness_result(archive, binding, report)
    evaluation = archive.record_evaluation(binding["candidate_id"], result, kind=binding["kind"],
                                          evidence=Path(report["report_path"]).parent)
    if verifier.is_dir():
        archive.attach_evidence(evaluation["id"], verifier, label="harness-verifier-output")
    archive.write_views()
    return {"already_recorded": False, "evaluation": evaluation}


def demo(store: str | Path, repository: str | Path) -> dict:
    """Execute prewritten fixtures to verify the full lifecycle on the local CPU."""
    archive = Archive(store)
    if archive.root.exists() and any(archive.root.iterdir()):
        raise ArchiveError("Demo needs an empty/new --store; existing research records are preserved")
    repository = Path(repository).resolve()
    example = repository / "examples" / "jsonl_aggregate"
    evaluator = example / "evaluate.py"
    task = {"schema_version": 1, "task_id": "jsonl-aggregate-cpu-v1",
            "objective": "rows_per_second", "direction": "maximize", "source_paths": ["solution.py"],
            "context": {"task_revision": "jsonl-aggregate-v1", "evaluator_revision": "sha256:" + file_sha(evaluator),
                        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                        "cpu_count": os.cpu_count(), "machine": platform.machine()},
                        "measurement_scope": "fresh Python + JSONL parse + aggregate + output"}}
    archive.initialize(task)
    parent = None
    records = []
    hypotheses = {
        "baseline": "Reference fixture: scan all rows once per group; establish a measured baseline.",
        "optimized": "Prewritten fixture: one pass per row removes repeated group scans while preserving exact integers.",
        "incorrect": "Negative control fixture: dropping negative values should fail the fixed correctness oracle.",
    }
    for name in ("baseline", "optimized", "incorrect"):
        candidate = archive.capture(example / "fixtures" / name, lane="cpu-smoke",
                                    hypothesis=hypotheses[name], parent_id=parent)
        result = evaluate_local(archive, candidate["id"], evaluator)
        records.append({"fixture": name, "candidate_id": candidate["id"],
                        "evaluation_id": result["id"], "valid": result["valid"],
                        "metrics": result["metrics"]})
        parent = candidate["id"]
    # Reopen state as a different process would. The last captured candidate is deliberately invalid.
    archive = Archive(store)
    best = archive.best()
    if best is None:
        raise ArchiveError("No valid candidate; inspect saved evidence")
    if records[-1]["valid"]:
        raise ArchiveError("Negative control unexpectedly passed; do not promote")
    restored = archive.restore(best["candidate"]["id"], archive.root / "restored-best")
    if not archive.matches_workspace(best["candidate"]["id"], restored):
        raise ArchiveError("Restored candidate differs from its archive")
    confirmation = evaluate_local(archive, best["candidate"]["id"], evaluator,
                                  kind="confirmation", workspace=restored,
                                  confirmation_of=best["evaluation"]["id"])
    release = archive.promote(best["candidate"]["id"], best["evaluation"]["id"], confirmation["id"])
    summary = archive.write_views()
    baseline_rate = records[0]["metrics"].get("rows_per_second")
    measured_rate = best["evaluation"]["metrics"]["rows_per_second"]
    result = {"kind": "executed_cpu_workflow_smoke", "automated_discovery": False,
              "store": str(archive.root), "candidates": records,
              "best_candidate_id": best["candidate"]["id"], "restored_workspace": str(restored),
              "confirmation_evaluation": confirmation["id"], "confirmation_metrics": confirmation["metrics"],
              "release": release, "search_speedup_vs_baseline": measured_rate / baseline_rate if baseline_rate else None,
              "candidate_count": summary["candidate_count"], "evaluation_count": summary["evaluation_count"]}
    atomic_json(archive.root / "demo_result.json", result)
    report = ["# CPU workflow smoke: actual execution", "",
              "Candidates were prewritten fixtures. This run verifies workflow behavior, not autonomous RSI or GPU performance.", "",
              "| Fixture | Correct | Rows/s | Candidate |", "| --- | --- | ---: | --- |"]
    for row in records:
        rate = row["metrics"].get("rows_per_second")
        report.append(f"| {row['fixture']} | {row['valid']} | {rate:.2f} | `{row['candidate_id']}` |" if rate is not None
                      else f"| {row['fixture']} | {row['valid']} | — | `{row['candidate_id']}` |")
    report += ["", f"Best candidate restored to `{restored}`.", "",
               f"Fresh confirmation: `{confirmation['id']}`; release: `{release['id']}`.", "",
               "See task.json for the frozen context; evaluations/ and evidence/ for complete measurements."]
    (archive.root / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return result
