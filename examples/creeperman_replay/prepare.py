"""Freeze a locally exported, individually adjudicated epoch for RSIforme."""
from __future__ import annotations
import argparse
from pathlib import Path
import platform
import shutil

from contract import ContractError, file_sha, freeze_epoch, load_json, write_json


def outside_repository(path):
    path = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[2]
    if path == repo or path.is_relative_to(repo) or repo.is_relative_to(path):
        raise ContractError("private prepared assets and runs must be outside the repository and its ancestors")
    return path


def prepare(input_path, output):
    frozen = freeze_epoch(load_json(input_path))
    output = outside_repository(output)
    if output.exists():
        raise ContractError("prepare output must not already exist; epochs are immutable")
    output.mkdir(parents=True)
    evaluator = output / "evaluator"
    evaluator.mkdir()
    for name in ("evaluate.py", "contract.py"):
        shutil.copyfile(Path(__file__).with_name(name), evaluator / name)
    write_json(evaluator / "epoch.json", frozen)
    task = {
        "schema_version": 1,
        "task_id": "creeperman-" + frozen["data"]["epoch_id"].replace(":", "-")[:100] + "-" + frozen["epoch_digest"][:12],
        "objective": "net_eed_units", "direction": "maximize", "source_paths": ["policy.json"],
        "evaluation_timeout_seconds": 120,
        "evaluator_assets": {name: file_sha(evaluator / name) for name in ("contract.py", "epoch.json")},
        "context": {
            "task_revision": "creeperman-recorded-ranking-v1",
            "evaluator_revision": "sha256:" + file_sha(evaluator / "evaluate.py"),
            "environment": platform.platform() + "; Python " + platform.python_version() + "; no network",
            "measurement_scope": "Fixed-budget recorded-action replay; not live crawling or production EED",
            "epoch_digest": frozen["epoch_digest"], "data_kind": frozen["data"]["data_kind"],
            **frozen["component_sha256"],
        },
    }
    write_json(output / "task.json", task)
    return {"prepared": str(output), "task_id": task["task_id"], "epoch_digest": frozen["epoch_digest"],
            "data_kind": frozen["data"]["data_kind"], "online_requests": 0}


def main():
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    print(json.dumps(prepare(args.input, args.output), indent=2))


if __name__ == "__main__":
    main()
