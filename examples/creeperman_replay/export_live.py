"""Export a bound ranking policy for Creeperman's live frontier; never award EED."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tempfile

from contract import (ContractError, digest, file_sha, keys, load_epoch, load_json,
                      text, validate_policy, write_json)
from prepare import outside_repository


LIVE_SCHEMA = "creeperman-live-policy-v1"
LIVE_FIELDS = {"unprocessed", "direct_evidence", "year", "arm_yield", "arm_attempts",
               "input_order", "path_score", "static_priority", "depth", "local_available"}
DEFAULT_BOOTSTRAP = {
    "schema_version": 1,
    "order_by": [{"field": field, "direction": direction} for field, direction in (
        ("local_available", "desc"), ("path_score", "desc"), ("arm_yield", "desc"),
        ("static_priority", "desc"), ("depth", "asc"), ("input_order", "asc"))],
}


def _live_order(policy):
    keys(policy, {"schema_version", "order_by"})
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ContractError("unsupported source policy schema")
    order = policy["order_by"]
    if not isinstance(order, list) or not 1 <= len(order) <= len(LIVE_FIELDS):
        raise ContractError("live order_by must be a nonempty bounded list")
    seen = set()
    for rule in order:
        keys(rule, {"field", "direction"})
        field = rule["field"]
        if not isinstance(field, str) or field not in LIVE_FIELDS:
            raise ContractError(f"live frontier cannot supply ranking field {field!r}; no silent mapping")
        if field in seen or rule["direction"] not in ("asc", "desc"):
            raise ContractError("invalid or repeated live ordering field")
        seen.add(field)
    return order


def _settings(explore_every, path_decay):
    if type(explore_every) is not int or not 2 <= explore_every <= 100:
        raise ContractError("explore_every must be an integer in 2..100")
    if (isinstance(path_decay, bool) or not isinstance(path_decay, (int, float))
            or not math.isfinite(path_decay) or not 0 < path_decay <= 1):
        raise ContractError("path_decay must be finite and in (0, 1]")
    return {"explore_every": explore_every, "path_decay": float(path_decay)}


def _package(policy, provenance, *, explore_every, path_decay):
    order = _live_order(policy)
    settings = _settings(explore_every, path_decay)
    provenance = {
        **provenance,
        "source_policy_sha256": digest(policy),
        "runtime_settings_sha256": digest(settings),
        "transfer_scope": "same-named ordering fields; live features and scheduling are a new context",
        "runtime_extension": {"status": "unvalidated", **settings},
        "production_increment_claimed": False,
        "online_validation_status": "not_evaluated",
    }
    # The ID covers provenance and fixed live settings, not merely the order_by list.
    policy_id = "rsif-live-" + digest({"policy": policy, "settings": settings,
                                        "provenance": provenance})[:24]
    return {"schema": LIVE_SCHEMA, "order_by": order, **settings,
            "provenance": {**provenance, "policy_id": policy_id}}


def _save(output, policy):
    output = outside_repository(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, policy)
    return policy


def export_bootstrap(output, *, policy=None, reason, explore_every=5, path_decay=0.5):
    """Explicitly export an untested prior, including live-only observable fields."""
    text(reason, "bootstrap reason")
    if policy is None:
        policy = DEFAULT_BOOTSTRAP
    elif isinstance(policy, (str, Path)):
        policy = load_json(policy)
    package = _package(policy, {
        "status": "unvalidated_bootstrap_prior", "reason": reason,
        "data_kind": "prior", "replay_scope": "not_evaluated",
    }, explore_every=explore_every, path_decay=path_decay)
    return _save(output, package)


def _check_report(bundle, kind, record, task, frozen):
    """Do not let a mutable evaluation summary overrule its sealed raw report."""
    evidence = bundle / "evidence" / kind
    report = load_json(evidence / "report.json")
    if any(record.get(key) != value for key, value in report.items() if key != "evidence_dir"):
        raise ContractError(f"{kind} evaluation summary differs from its sealed replay report")
    if (record.get("score_scope") != "recorded_action_replay"
            or record.get("epoch_digest") != frozen["epoch_digest"]
            or record.get("data_kind") != frozen["data"]["data_kind"]
            or record.get("competition_contribution_claimed") is not False
            or record.get("production_ledger_written") is not False):
        raise ContractError(f"{kind} evaluation does not bind this recorded-action scope")
    sealed = evidence / "evaluator"
    for name, expected in task["evaluator_assets"].items():
        if file_sha(sealed / name) != expected.removeprefix("sha256:"):
            raise ContractError(f"{kind} replay asset differs from frozen task: {name}")
    if task["context"]["evaluator_revision"] != "sha256:" + file_sha(sealed / "evaluate.py"):
        raise ContractError(f"{kind} replay evaluator differs from frozen task")
    if load_epoch(sealed / "epoch.json") != frozen:
        raise ContractError("search and confirmation used different epochs")


def _check_profile(path, search, frozen):
    profile = load_json(path)
    for key, expected in (("evaluation_id", search["id"]),
                          ("source_digest", search["source_digest"]),
                          ("context_fingerprint", search["context_fingerprint"]),
                          ("epoch_digest", frozen["epoch_digest"])):
        if profile.get(key) != expected:
            raise ContractError(f"saved replay profile does not bind selected evaluation: {key}")
    expected_findings = [{"completion_reason": search["completion_reason"],
                          "metrics": search["metrics"],
                          "trace": [{key: row[key] for key in
                                     ("action_id", "source_id", "arm", "state", "new_eed_units")}
                                    for row in search["trace"]]}]
    if profile.get("findings") != expected_findings:
        raise ContractError("saved replay profile findings disagree with retained evaluation")
    command = profile.get("command", {})
    if (command.get("callable") != "rsiforme.workflow.evaluate_local"
            or command.get("candidate_id") != search["candidate_id"]):
        raise ContractError("saved replay profile must identify the actual candidate execution")
    return digest(profile)


def export_evaluated(store, output, *, release_id=None, profile=None,
                     as_bootstrap_prior=False, reason=None, explore_every=5, path_decay=0.5):
    """Export an existing promotion; never select best(), evaluate, or promote here.

    Real recorded replay validates only the original ordering in that fixed epoch.
    Fresh confirmation verifies reproduction, not generalization to live crawling.
    Fixture releases require an explicit bootstrap downgrade and cannot be winners.
    """
    from rsiforme.archive import Archive
    from rsiforme.delivery import export_release

    archive = Archive(store)
    # Reuse the core release verifier: exact candidate, valid search, a fresh bound
    # confirmation, immutable evidence inventory, and frozen evaluator dependencies.
    with tempfile.TemporaryDirectory(prefix="creeperman-live-export-") as directory:
        bundle = Path(directory) / "release"
        export_release(archive, bundle, release_id=release_id)
        release = load_json(bundle / "records" / "release.json")
        candidate = load_json(bundle / "records" / "candidate.json")
        search = load_json(bundle / "records" / "search.json")
        confirmation = load_json(bundle / "records" / "confirmation.json")
        config = load_json(bundle / "task.json")
        task = config["task"]
        if (task.get("objective") != "net_eed_units" or task.get("source_paths") != ["policy.json"]
                or task["context"].get("task_revision") != "creeperman-recorded-ranking-v1"):
            raise ContractError("release is not a Creeperman recorded-ranking task")
        policy = validate_policy(load_json(bundle / "source" / "policy.json"))
        _live_order(policy)
        frozen = load_epoch(bundle / "evidence" / "search" / "evaluator" / "epoch.json")
        if any(task["context"].get(key) != expected for key, expected in {
            "epoch_digest": frozen["epoch_digest"], "data_kind": frozen["data"]["data_kind"],
            **frozen["component_sha256"],
        }.items()):
            raise ContractError("frozen task and sealed epoch identities disagree")
        for kind, record in (("search", search), ("confirmation", confirmation)):
            _check_report(bundle, kind, record, task, frozen)
        profile = Path(profile) if profile else archive.root / "profiles" / (search["id"] + ".json")
        profile_digest = _check_profile(profile, search, frozen)
        data_kind = frozen["data"]["data_kind"]
        if data_kind != "recorded" and not as_bootstrap_prior:
            raise ContractError("synthetic_fixture cannot become validated_replay; explicitly export a bootstrap prior")
        if as_bootstrap_prior:
            text(reason, "bootstrap reason")
        provenance = {
            "status": "unvalidated_bootstrap_prior" if as_bootstrap_prior else "validated_replay",
            "data_kind": data_kind,
            "replay_scope": "fixed recorded epoch only" if data_kind == "recorded" else "synthetic fixture only",
            "candidate_id": candidate["id"], "release_id": release["id"],
            "evaluation_id": search["id"], "confirmation_evaluation_id": confirmation["id"],
            "source_digest": candidate["source_digest"],
            "context_fingerprint": config["context_fingerprint"],
            "epoch_digest": frozen["epoch_digest"], "component_sha256": frozen["component_sha256"],
            "profile_sha256": profile_digest,
            "search_evidence_sha256": search["evidence_digest"],
            "confirmation_evidence_sha256": confirmation["evidence_digest"],
            "fresh_confirmation": True,
        }
        if as_bootstrap_prior:
            provenance["reason"] = reason
        package = _package(policy, provenance, explore_every=explore_every, path_decay=path_decay)
    return _save(output, package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    bootstrap = sub.add_parser("bootstrap", help="export an explicitly unvalidated prior")
    bootstrap.add_argument("--policy", type=Path)
    bootstrap.add_argument("--reason", required=True)
    evaluated = sub.add_parser("evaluated", help="export a previously confirmed replay release")
    evaluated.add_argument("--store", required=True, type=Path)
    evaluated.add_argument("--release-id")
    evaluated.add_argument("--profile", type=Path)
    evaluated.add_argument("--as-bootstrap-prior", action="store_true")
    evaluated.add_argument("--reason")
    for command in (bootstrap, evaluated):
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--explore-every", type=int, default=5)
        command.add_argument("--path-decay", type=float, default=0.5)
    args = vars(parser.parse_args())
    mode = args.pop("mode")
    package = export_bootstrap(**args) if mode == "bootstrap" else export_evaluated(**args)
    print(json.dumps({"policy_id": package["provenance"]["policy_id"],
                      "status": package["provenance"]["status"],
                      "output": str(Path(args["output"]).resolve()),
                      "online_requests": 0, "production_ledger_written": False}, indent=2))


if __name__ == "__main__":
    main()
