"""Explicit protocol/fault fixture: this worker makes no model request."""

import argparse
import json
from pathlib import Path
import shutil
import sys


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixtures", required=True, type=Path)
args = parser.parse_args()
prompt = sys.stdin.read()
handoff = json.loads(Path("handoff.json").read_text())
index = int(handoff["trial_id"].split("-")[-1])
fixture = "optimized" if index == 1 else "incorrect"
history = handoff["history"]["evaluations"]
if index > 1 and len(history) < 2:
    raise RuntimeError("Second worker did not receive the first worker's actual evaluation")
shutil.copyfile(args.fixtures / fixture / "solution.py", Path("solution.py"))
proposal = {
    "decision": "evaluate",
    "hypothesis": "Prewritten protocol fixture: " + ("single-pass exact aggregation" if index == 1 else "negative control drops negative values"),
    "change_summary": "Copied the explicitly selected " + fixture + " fixture; not a model-discovered change.",
    "evidence_used": [handoff["parent_evaluation"]["id"], "actual baseline cProfile", history[-1]["id"]],
    "next_direction": "Use the measured correctness/performance result for final candidate selection.",
}
Path("proposal.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
print(json.dumps({"scripted": True, "model_calls": 0, "received_prompt": bool(prompt),
                  "trial_id": handoff["trial_id"], "fixture": fixture,
                  "observed_evaluations": [row["id"] for row in history]}))
