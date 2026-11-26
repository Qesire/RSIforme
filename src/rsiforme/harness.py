"""Small, version-bound adapters for RSI-Harness; no scheduler of our own.

Contract source: OpenRSI-Index commit
486eec2b93296c514238f242f172703c3736ccc1. See docs/HARNESS_INTEGRATION.md.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


HARNESS_REVISION = "486eec2b93296c514238f242f172703c3736ccc1"
_MARKER = "--- rsi-harness submission ---"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STATUSES = {
    "completed",
    "submission_error",
    "verifier_error",
    "verifier_timeout",
    "infrastructure_error",
}


class HarnessContractError(ValueError):
    """An output cannot be bound to one trustworthy submission record."""


def _component(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise HarnessContractError(f"{label} must be a single identifier")
    return value


def _number(value: object, label: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessContractError(f"{label} must be numeric")
    if not math.isfinite(value):
        raise HarnessContractError(f"{label} must be finite")
    return value


def _json(text: str) -> Any:
    def invalid_constant(value: str) -> None:
        raise HarnessContractError(f"non-finite JSON constant: {value}")

    try:
        return json.loads(text, parse_constant=invalid_constant)
    except json.JSONDecodeError as exc:
        raise HarnessContractError(f"invalid Harness JSON: {exc}") from exc


def _metrics(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        raise HarnessContractError("metrics must be an object")
    for key, metric in value.items():
        if not isinstance(key, str):
            raise HarnessContractError("metric names must be strings")
        _number(metric, f"metric {key}")
    return dict(value)


def parse_submit_output(text: str) -> dict[str, Any]:
    """Parse one complete native ``rsi-submit`` response, never history.

    Missing, duplicate, truncated, or non-finite data raises instead of
    implying that a request failed or that it is safe to submit again.
    The returned ``round_id`` has no global identity without the caller's
    separately recorded Harness run ID.
    """
    lines = text.splitlines()
    marker_indices = [i for i, line in enumerate(lines) if line == _MARKER]
    if len(marker_indices) != 1:
        raise HarnessContractError("expected one submission block; response is ambiguous")
    marker = marker_indices[0]
    fields: dict[str, str] = {}
    recognized = {
        "round", "status", "reward", "score", "exit_code", "timed_out",
        "duration_seconds", "remaining", "error",
    }
    for line in lines[marker + 1:]:
        key, sep, value = line.partition(": ")
        if sep and key in recognized:
            if key in fields:
                raise HarnessContractError(f"duplicate submission field: {key}")
            fields[key] = value
    required = {"round", "status", "reward", "exit_code", "timed_out", "duration_seconds"}
    missing = sorted(required - fields.keys())
    if missing:
        raise HarnessContractError(f"incomplete submission response: {', '.join(missing)}")
    round_id = _component(fields["round"], "round")
    status = fields["status"]
    if status not in _STATUSES:
        raise HarnessContractError(f"unknown submission status: {status}")
    reward = _json(fields["reward"])
    metrics = {} if reward is None else _metrics(reward)
    score = _number(_json(fields.get("score", "null")), "score", nullable=True)
    exit_code = _json(fields["exit_code"])
    if exit_code is not None and (type(exit_code) is not int):
        raise HarnessContractError("exit_code must be an integer or null")
    timed_out = _json(fields["timed_out"])
    if type(timed_out) is not bool:
        raise HarnessContractError("timed_out must be boolean")
    duration = _number(_json(fields["duration_seconds"]), "duration_seconds", nullable=True)
    if duration is not None and duration < 0:
        raise HarnessContractError("duration_seconds must be non-negative")
    result: dict[str, Any] = {
        "round_id": round_id,
        "status": status,
        "score": score,
        "metrics": metrics,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": duration,
    }
    if "error" in fields:
        result["error"] = fields["error"]
    if "remaining" in fields:
        remaining = fields["remaining"]
        if remaining == "unlimited":
            result["remaining"] = None
        elif remaining.isdecimal():
            result["remaining"] = int(remaining)
        else:
            raise HarnessContractError("remaining must be a count or unlimited")
    # This is a path in Work, not a host path. Preserve its namespace.
    expected = f"/run/rsi-harness/feedback/{round_id}.log"
    references = [line.removeprefix("Full verifier output: ") for line in lines[:marker]
                  if line.startswith("Full verifier output: ")]
    if references:
        if references != [expected]:
            raise HarnessContractError("feedback path does not match submission round")
        result["feedback_path"] = expected
    return result


def _read_object(path: Path) -> dict[str, Any]:
    value = _json(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HarnessContractError(f"expected JSON object: {path}")
    return value


def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise HarnessContractError("Harness artifact points outside the selected run directory")
    return resolved


def read_run_identity(run_dir: str | Path) -> dict[str, str]:
    """Recover the run's recorded identity even after copying its log tree.

    Both pinned native backends record paths.root=<runtime-root>/<run_id>.
    No filesystem operation is performed on that original runtime path.
    """
    root = Path(run_dir).expanduser().resolve()
    path = _inside(root, root / "run-plan.json")
    plan = _read_object(path)
    paths, task = plan.get("paths"), plan.get("task")
    if not isinstance(paths, dict) or not isinstance(task, dict):
        raise HarnessContractError("run-plan is missing paths or task")
    recorded_root = paths.get("root")
    if not isinstance(recorded_root, str) or not Path(recorded_root).is_absolute():
        raise HarnessContractError("run-plan.paths.root must be an absolute native runtime path")
    harness_run_id = _component(Path(recorded_root).name, "recorded Harness run ID")
    task_id = _component(task.get("task_id"), "recorded task ID")
    return {"harness_run_id": harness_run_id, "task_id": task_id, "run_plan_path": str(path)}


def read_round_report(run_dir: str | Path, round_id: str) -> dict[str, Any]:
    """Read one durably recorded round, including a failed evaluation.

    ``run_dir`` is ``<logs>/runs/<run_id>/<task_id>``. The state file is
    the upstream durability boundary; a report written before a crash but
    absent from that state is deliberately not imported.
    """
    root = Path(run_dir).expanduser().resolve()
    round_id = _component(round_id, "round_id")
    report_path = _inside(root, root / "submissions" / round_id / "report.json")
    report = _read_object(report_path)
    if report.get("round") != round_id:
        raise HarnessContractError("report round disagrees with selected round")
    if report.get("status") not in _STATUSES:
        raise HarnessContractError("unknown round status")
    score = _number(report.get("score"), "report score", nullable=True)
    metrics = _metrics(report.get("metrics"))
    state = _read_object(_inside(root, root / "evolve_state.json"))
    submissions = state.get("submissions")
    if not isinstance(submissions, list) or not all(isinstance(item, dict) for item in submissions):
        raise HarnessContractError("evolve_state.submissions must be a list of objects")
    entries = [item for item in submissions if item.get("round") == round_id]
    if len(entries) != 1:
        raise HarnessContractError("selected report is not uniquely committed in evolve_state")
    entry = entries[0]
    if entry.get("status") != report["status"] or entry.get("score") != score or entry.get("rewards") != metrics:
        raise HarnessContractError("committed state and round report disagree")
    result = dict(report)
    result.update({
        "round_id": round_id,
        "metrics": metrics,
        "report_path": str(report_path),
        "test_output_path": str(_inside(root, report_path.parent / "test_output.txt")),
    })
    return result


def read_best_report(run_dir: str | Path) -> dict[str, Any]:
    """Resolve ``best_round`` to that exact round's metrics.

    ``best_rewards`` is deliberately never returned: the upstream summary
    can combine metrics from different candidate rounds.
    """
    root = Path(run_dir).expanduser().resolve()
    final_path = _inside(root, root / "final_result.json")
    final = _read_object(final_path)
    if final.get("best_round") is None:
        raise HarnessContractError("run has no scored best round")
    report = read_round_report(root, _component(final["best_round"], "best_round"))
    if report["status"] != "completed":
        raise HarnessContractError("selected round is not completed")
    score = _number(report.get("score"), "report score")
    if _number(final.get("best_score"), "best_score") != score:
        raise HarnessContractError("final result score disagrees with round report")
    report["final_result_path"] = str(final_path)
    return report


def construct_harness_command(
    task: str | Path | None,
    *,
    agent: str = "oracle",
    model: str | None = None,
    cluster: str | Path | None = None,
    dry_run: bool = True,
    run_id: str | None = None,
) -> list[str]:
    """Construct an argv, without running a shell, installing, or submitting.

    Resume uses its saved task/agent/model; supplying model with a run ID
    is an error. New local runs have no native dry-run in the pinned CLI.
    """
    if run_id is not None:
        _component(run_id, "run_id")
        if task is not None or model is not None or agent != "oracle":
            raise HarnessContractError("resume uses its recorded task/agent/model; omit overrides")
        argv = ["rsi-harness", "resume", run_id]
    else:
        if task is None:
            raise HarnessContractError("a task path is required for a new run")
        if dry_run and cluster is None:
            raise HarnessContractError("new-run --dry-run requires --cluster in the pinned Harness")
        if not agent or agent.startswith("-") or "\0" in agent:
            raise HarnessContractError("invalid agent name")
        argv = ["rsi-harness", "run", str(Path(task).expanduser().resolve()), "--agent", agent]
        if model is not None:
            if not model or model.startswith("-") or "\0" in model:
                raise HarnessContractError("invalid model name")
            argv.extend(["--model", model])
    if cluster is not None:
        value = str(cluster)
        if not value or value.startswith("-") or "\0" in value:
            raise HarnessContractError("invalid cluster/profile")
        argv.extend(["--cluster", value])
    if dry_run:
        argv.append("--dry-run")
    return argv
