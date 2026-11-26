"""Independent CPU integration evaluator; stdlib only, not a security sandbox.

The parent owns timing, expected answers, and the report. Every candidate call
runs in a new Python process in a disposable directory. The objective includes
process startup, JSONL parsing, aggregation, and output serialization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

PROTOCOL_VERSION = "jsonl-aggregate-v1"
BENCHMARK_ROWS = 40000
BENCHMARK_GROUPS = 128
REPEATS = 3
CALL_TIMEOUT_SECONDS = 5.0

# The candidate is imported only by this child, never into the evaluator parent.
WORKER_SOURCE = '''\
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("candidate_solution", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open(sys.argv[2], encoding="utf-8") as stream:
    records = [json.loads(line) for line in stream if line.strip()]
result = module.aggregate(records)
Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
'''


def reference(records):
    """Fixed oracle over valid string-group / integer-value records."""
    groups = {}
    for row in records:
        entry = groups.setdefault(row["group"], {"group": row["group"], "count": 0, "sum": 0})
        entry["count"] += 1
        entry["sum"] += row["value"]
    return [groups[group] for group in sorted(groups)]


def correctness_cases():
    return [
        ("empty", []),
        ("singleton", [{"group": "a", "value": 7}]),
        ("negative", [{"group": "a", "value": -11}, {"group": "a", "value": 5}]),
        ("order_unicode", [
            {"group": "雪", "value": 3}, {"group": "", "value": 0},
            {"group": "b", "value": -2}, {"group": "a", "value": 4},
            {"group": "雪", "value": -1}, {"group": "b", "value": 2},
        ]),
        ("integer_precision", [
            {"group": "big", "value": 2**60 + 3},
            {"group": "big", "value": -(2**60)},
        ]),
    ]


def benchmark_records():
    rng = random.Random(20261126)
    return [
        {"group": f"g{rng.randrange(BENCHMARK_GROUPS):03d}", "value": rng.randrange(-1000, 1001)}
        for _ in range(BENCHMARK_ROWS)
    ]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_new(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


class CandidateFailure(Exception):
    pass


def _run_case(candidate, worker, root, name, records, evidence):
    case_dir = root / name
    case_dir.mkdir()
    source = case_dir / "rows.jsonl"
    source.write_text("".join(_canonical(row) + "\n" for row in records), encoding="utf-8")
    answer = case_dir / "answer.json"
    expected = reference(records)
    argv = [sys.executable, "-I", "-B", str(worker), str(candidate), str(source), str(answer)]
    started = time.perf_counter()
    proc = subprocess.Popen(argv, cwd=case_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=CALL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        timed_out = True
    seconds = time.perf_counter() - started
    (evidence / f"{name}.stdout.txt").write_bytes(stdout)
    (evidence / f"{name}.stderr.txt").write_bytes(stderr)
    if timed_out:
        raise CandidateFailure(f"{name}: exceeded {CALL_TIMEOUT_SECONDS:g}s timeout")
    exit_code = proc.returncode
    if exit_code:
        raise CandidateFailure(f"{name}: child exited {exit_code}; see stderr")
    try:
        if answer.stat().st_size > 1024 * 1024:
            raise CandidateFailure(f"{name}: output exceeds 1 MiB")
        actual = json.loads(answer.read_text(encoding="utf-8"))
        if _canonical(actual) != _canonical(expected):
            _write_new(evidence / f"{name}.mismatch.json", {"expected": expected, "actual": actual})
            raise CandidateFailure(f"{name}: result differs from fixed oracle")
    except (OSError, ValueError, TypeError) as exc:
        raise CandidateFailure(f"{name}: invalid output: {exc}") from exc
    return {"case": name, "rows": len(records), "seconds": seconds, "valid": True}


def evaluate_candidate(snapshot_dir: Path, output_dir: Path) -> dict:
    """Measure snapshot_dir/solution.py and retain one new evidence directory.

    status=completed means execution reached a trustworthy judgement; valid=False
    is a candidate failure. status=infra_error means evaluator setup failed.
    The returned report is also saved with exclusive creation as report.json.
    """
    snapshot_dir, output_dir = Path(snapshot_dir).resolve(), Path(output_dir).resolve()
    eval_run_id = str(uuid.uuid4())
    evidence = output_dir / eval_run_id
    evidence.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    report = {
        "schema_version": 1, "protocol": PROTOCOL_VERSION,
        "eval_run_id": eval_run_id, "status": "completed", "valid": False,
        "metrics": {}, "cases": [], "evidence_dir": str(evidence),
        "measurement_scope": "fresh Python process + JSONL parsing + aggregate + JSON output",
        "environment": {
            "python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parameters": {"rows": BENCHMARK_ROWS, "groups": BENCHMARK_GROUPS, "repeats": REPEATS, "seed": 20261126},
    }
    try:
        solution = snapshot_dir / "solution.py"
        if solution.is_symlink() or not solution.is_file():
            raise CandidateFailure("snapshot must contain a regular solution.py file")
        source_bytes = solution.read_bytes()
        report["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        # Snapshot the submitted bytes before launching any candidate process.
        with tempfile.TemporaryDirectory(prefix="rsiforme-eval-") as temporary:
            root = Path(temporary)
            candidate = root / "solution.py"
            candidate.write_bytes(source_bytes)
            worker = root / "trusted_worker.py"
            worker.write_text(WORKER_SOURCE, encoding="utf-8")
            for name, records in correctness_cases():
                report["cases"].append(_run_case(candidate, worker, root, name, records, evidence))
            records = benchmark_records()
            _run_case(candidate, worker, root, "warmup", records, evidence)
            samples = []
            for repeat in range(REPEATS):
                case = _run_case(candidate, worker, root, f"measure-{repeat}", records, evidence)
                report["cases"].append(case)
                samples.append(case["seconds"])
            median = statistics.median(samples)
            if not math.isfinite(median) or median <= 0:
                raise RuntimeError("invalid wall-clock measurement")
            report["valid"] = True
            report["metrics"] = {
                "rows_per_second": BENCHMARK_ROWS / median,
                "median_seconds": median, "min_seconds": min(samples), "max_seconds": max(samples),
            }
    except CandidateFailure as exc:
        report["failure"] = str(exc)
    except (OSError, RuntimeError) as exc:
        report["status"] = "infra_error"
        report["failure"] = f"{type(exc).__name__}: {exc}"
    report["total_wall_seconds"] = time.perf_counter() - started
    _write_new(evidence / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = evaluate_candidate(args.snapshot, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["status"] == "completed" and report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
