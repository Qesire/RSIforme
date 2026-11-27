"""Execute the budgeted controller and portable delivery with a scripted worker.

Candidate timings are real. Candidate proposals are prewritten protocol fixtures;
this program does not call an LLM and does not establish autonomous discovery.
"""

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

from rsiforme.archive import Archive, atomic_json, file_sha
from rsiforme.delivery import export_release, verify_release
from rsiforme.research import ResearchController
from rsiforme.workflow import evaluate_local
from profile_baseline import profile_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=Path)
    args = parser.parse_args()
    example = Path(__file__).resolve().parent
    repository = example.parents[1]
    archive = Archive(args.store)
    if archive.root.exists() and any(archive.root.iterdir()):
        raise ValueError("Use a new store; existing research evidence is preserved")
    evaluator = example / "evaluate.py"
    archive.initialize({"schema_version": 1, "task_id": "jsonl-controller-v2", "objective": "rows_per_second",
                        "direction": "maximize", "source_paths": ["solution.py"], "evaluation_timeout_seconds": 30,
                        "context": {"task_revision": "jsonl-aggregate-v1",
                                    "evaluator_revision": "sha256:" + file_sha(evaluator),
                                    "environment": {"python": platform.python_version(), "platform": platform.platform()},
                                    "worker_kind": "scripted fixture; no model calls"}})
    candidate = archive.capture(example / "fixtures/baseline", lane="baseline", hypothesis="Measure original implementation before controlled protocol changes")
    baseline = evaluate_local(archive, candidate["id"], evaluator)
    profile = archive.root / "baseline-profile.json"
    profile_candidate(archive, candidate["id"], profile)
    program = {
        "schema_version": 1, "backend": {"type": "local"},
        "worker": {"argv": [sys.executable, "-I", "-B", str(example / "scripted_worker.py"),
                            "--fixtures", str(example / "fixtures")]},
        "budgets": {"max_trials": 2, "max_evaluations": 4, "walltime_seconds": 180, "worker_timeout_seconds": 15},
        "lanes": [{"id": "algorithm", "goal": "Execute known optimized protocol fixture"},
                  {"id": "negative-control", "goal": "Verify rejection and restoration after a known incorrect change"}],
        "instructions": "This is a scripted engineering validation, not model research.",
    }
    controller = ResearchController(archive)
    controller.initialize(program, baseline_evaluation=baseline["id"], profile=profile)
    controller.prepare()
    # New OS processes reopen the prepared state and then consume the first result.
    for name, limit in (("first", "0"), ("second", "1")):
        command = [sys.executable, "-m", "rsiforme", "research-run", "--store", str(archive.root), "--max-new-trials", limit]
        process = subprocess.run(command, cwd=repository, text=True, capture_output=True, check=False)
        (archive.root / f"controller-{name}.stdout.json").write_text(process.stdout)
        (archive.root / f"controller-{name}.stderr.txt").write_text(process.stderr)
        if process.returncode:
            raise RuntimeError(f"Controller process {name} failed: {process.stderr}")
    controller = ResearchController(Archive(archive.root))
    trials = controller.trials()
    if len(trials) != 2 or trials[1].get("decision") != "invalid":
        raise RuntimeError("Scripted negative control did not produce the expected measured rejection")
    final = controller.finalize()
    if final["phase"] != "promoted":
        raise RuntimeError(f"Final confirmation did not pass: {final}")
    release_dir = archive.root.with_name(archive.root.name + "-release")
    export = export_release(archive, release_dir)
    verify = subprocess.run([sys.executable, "-I", "-B", str(release_dir / "verify.py")],
                            cwd=release_dir, text=True, capture_output=True, check=True)
    output = archive.root.with_name(archive.root.name + "-reproduced")
    reproduction = subprocess.run([sys.executable, "-I", "-B", str(release_dir / "reproduce.py"), str(output)],
                                  cwd=release_dir, text=True, capture_output=True, check=True)
    verify_release(release_dir)
    summary = {"kind": "actual_controller_scripted_worker_validation", "model_calls": 0,
               "autonomous_discovery": False, "baseline_evaluation": baseline["id"],
               "research_id": controller.config["id"], "trials": trials,
               "budget": controller.status()["budget"], "finalization": final, "export": export,
               "portable_verify_stdout": verify.stdout, "portable_reproduce_stdout": reproduction.stdout,
               "evaluations": archive.evaluations()}
    atomic_json(archive.root / "research-result.json", summary)
    print(json.dumps({"research_id": summary["research_id"], "model_calls": 0,
                      "trials": [{k: t.get(k) for k in ("id", "phase", "decision", "evaluation_id")} for t in trials],
                      "budget": summary["budget"], "finalization": final,
                      "portable_reproduction_succeeded": True}, indent=2))


if __name__ == "__main__":
    main()
