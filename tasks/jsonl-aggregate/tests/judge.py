"""Harness verifier entry: same-host baseline and candidate, then reward.json."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import evaluate_candidate


def main(workspace=Path("/workspace"), destination=Path("/logs/verifier")):
    workspace, destination = Path(workspace), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    reward_file = destination / "reward.json"
    if reward_file.exists():
        raise RuntimeError("Judge must start with an empty reward destination")
    with tempfile.TemporaryDirectory(prefix="rsiforme-baseline-") as temporary:
        baseline = Path(temporary)
        (baseline / "solution.py").write_bytes((Path(__file__).parent / "baseline.py").read_bytes())
        baseline_report = evaluate_candidate(baseline, destination / "baseline")
    if baseline_report["status"] != "completed" or not baseline_report["valid"]:
        print(json.dumps({"status": "infra_error", "baseline": baseline_report}, allow_nan=False))
        return 1
    candidate_report = evaluate_candidate(workspace, destination / "candidate")
    print(json.dumps({"baseline": baseline_report, "candidate": candidate_report}, allow_nan=False))
    if candidate_report["status"] != "completed" or not candidate_report["valid"]:
        return 1
    reward = candidate_report["metrics"]["rows_per_second"] / baseline_report["metrics"]["rows_per_second"]
    with reward_file.open("x", encoding="utf-8") as stream:
        json.dump({"reward": reward, "quality_passed": 1}, stream, allow_nan=False)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
