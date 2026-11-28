"""Evaluate one declared policy using RSIforme's actual Archive and evaluator."""
from __future__ import annotations
import argparse
from pathlib import Path
import tempfile

from contract import load_epoch, load_json, validate_policy, write_json
from prepare import outside_repository


def run(prepared, policy, store):
    from rsiforme.archive import Archive
    from rsiforme.workflow import evaluate_local

    prepared = Path(prepared).resolve()
    frozen = load_epoch(prepared / "evaluator" / "epoch.json")
    validated = validate_policy(load_json(policy))
    task = load_json(prepared / "task.json")
    if task["context"]["epoch_digest"] != frozen["epoch_digest"]:
        raise ValueError("task does not bind this prepared epoch")
    store = outside_repository(store)
    archive = Archive(store)
    archive.initialize(task)
    with tempfile.TemporaryDirectory(prefix="creeperman-policy-") as directory:
        work = Path(directory)
        write_json(work / "policy.json", validated)
        candidate = archive.capture(work, lane="recorded-ranking",
                                    hypothesis="Evaluate the declared ordering against a fixed recorded epoch")
        evaluation = evaluate_local(archive, candidate["id"], prepared / "evaluator" / "evaluate.py")
    archive.write_views()
    profile_path = None
    if evaluation["valid"]:
        profiles = archive.root / "profiles"
        profiles.mkdir(exist_ok=True)
        profile_path = profiles / (evaluation["id"] + ".json")
        profile = {
            "source_digest": evaluation["source_digest"],
            "context_fingerprint": evaluation["context_fingerprint"],
            "command": {"callable": "rsiforme.workflow.evaluate_local",
                        "candidate_id": candidate["id"], "evaluator": str(prepared / "evaluator" / "evaluate.py")},
            "scope": "Actual execution of recorded-action replay; recorded costs are not new network measurements",
            "evaluation_id": evaluation["id"], "epoch_digest": frozen["epoch_digest"],
            "findings": [{"completion_reason": evaluation["completion_reason"],
                          "metrics": evaluation["metrics"],
                          "trace": [{k: row[k] for k in ("action_id", "source_id", "arm", "state", "new_eed_units")}
                                    for row in evaluation["trace"]]}],
        }
        write_json(profile_path, profile)
    return {"candidate_id": candidate["id"], "evaluation": evaluation,
            "profile": str(profile_path) if profile_path else None,
            "online_requests": 0, "production_ledger_written": False}


def main():
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prepared", required=True, type=Path)
    p.add_argument("--policy", required=True, type=Path)
    p.add_argument("--store", required=True, type=Path)
    args = p.parse_args()
    result = run(args.prepared, args.policy, args.store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["evaluation"]["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
