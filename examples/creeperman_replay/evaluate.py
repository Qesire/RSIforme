"""Trusted RSIforme entrypoint for a prepared Creeperman recorded epoch."""
from pathlib import Path
import uuid

from contract import ContractError, PolicyError, load_epoch, load_json, replay, validate_policy, write_json


def evaluate_candidate(snapshot_dir, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution = "creeperman-replay-" + uuid.uuid4().hex
    try:
        frozen = load_epoch(Path(__file__).with_name("epoch.json"))
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "infra_error", "valid": False, "metrics": {}, "error": str(exc)}
    else:
        try:
            policy = validate_policy(load_json(Path(snapshot_dir) / "policy.json"))
            result = replay(frozen, policy)
        except (OSError, ContractError, PolicyError, TypeError) as exc:
            result = {"status": "completed", "valid": False, "metrics": {}, "error": str(exc),
                      "epoch_digest": frozen["epoch_digest"], "data_kind": frozen["data"]["data_kind"]}
    result.update(eval_run_id=execution, evidence_dir=str(output_dir.resolve()),
                  competition_contribution_claimed=False, production_ledger_written=False)
    write_json(output_dir / "report.json", result)
    return result
