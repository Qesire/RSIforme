"""Measure the current aggregation implementation with cProfile before research."""

import argparse
import cProfile
import importlib.util
import io
from pathlib import Path
import pstats
import sys
import time

from rsiforme.archive import Archive, atomic_json, file_sha


def profile_candidate(archive: Archive, candidate_id: str, destination: Path) -> dict:
    candidate = archive.candidate(candidate_id)
    source = archive.source(candidate_id) / "solution.py"
    spec = importlib.util.spec_from_file_location("profiled_candidate", source)
    module = importlib.util.module_from_spec(spec)
    # Do not let importlib write __pycache__ into the immutable source archive.
    exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
    # Input generation is the same fixed workload as the evaluator; profiling
    # measures aggregate itself and is separate from full end-to-end scoring.
    evaluator_spec = importlib.util.spec_from_file_location("profile_workload", Path(__file__).with_name("evaluate.py"))
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    evaluator_spec.loader.exec_module(evaluator)
    rows = evaluator.benchmark_records()
    profile = cProfile.Profile()
    started = time.perf_counter()
    result = profile.runcall(module.aggregate, rows)
    elapsed = time.perf_counter() - started
    if result != evaluator.reference(rows):
        raise ValueError("Profiled source does not satisfy the task oracle")
    output = io.StringIO()
    stats = pstats.Stats(profile, stream=output).strip_dirs().sort_stats("tottime")
    stats.print_stats(15)
    functions = []
    for (filename, line, function), (primitive_calls, calls, own, cumulative, callers) in stats.stats.items():
        functions.append({"file": filename, "line": line, "function": function,
                          "calls": calls, "self_seconds": own, "cumulative_seconds": cumulative})
    functions.sort(key=lambda record: record["self_seconds"], reverse=True)
    report = {"kind": "actual_cprofile", "source_digest": candidate["source_digest"],
              "context_fingerprint": candidate["context_fingerprint"],
              "profiler_sha256": file_sha(Path(__file__)),
              "source_sha256": file_sha(source), "command": [sys.executable, *sys.argv],
              "scope": "aggregate(records) only; profiler overhead is included; not the scored full pipeline",
              "rows": len(rows), "groups": len(result), "elapsed_seconds": elapsed,
              "functions": functions, "profile_text": output.getvalue(),
              "findings": [f"Largest self-time: {functions[0]['function']} at {functions[0]['self_seconds']:.6f}s",
                           "Compare this aggregation profile with baseline end-to-end times in the handoff."]}
    atomic_json(destination, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = profile_candidate(Archive(args.store), args.candidate, args.output)
    print(report["profile_text"])
