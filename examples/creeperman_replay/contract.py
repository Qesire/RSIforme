"""Frozen, recorded-action ranking; no HTTP client or production-ledger writer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from fractions import Fraction


COSTS = ("requests", "bytes", "wall_millis")
FIELDS = {"input_order", "year", "direct_evidence", "independent_family",
          "unprocessed", "arm_attempts", "arm_yield", "request_bound",
          "byte_bound", "wall_bound"}
BINDINGS = {"baseline_sha256", "reviewed_sha256", "rules_sha256", "model_sha256"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class ContractError(ValueError):
    pass


class PolicyError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError(f"duplicate JSON field: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs)


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ContractError("expected an object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ContractError(f"missing fields {sorted(missing)}; unknown fields {sorted(extra)}")


def text(value, name):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ContractError(f"{name} must be a nonempty single-line string")


def sha(value, name):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ContractError(f"{name} must be a lowercase SHA-256")


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ContractError("invalid action/source/epoch identifier")


def host_year(row):
    host, year = row.get("hostname"), row.get("year")
    if (not isinstance(host, str) or len(host) > 253 or "." not in host
            or host != host.lower() or not all(LABEL.fullmatch(p) for p in host.split("."))
            or host.split(".")[-1].isdigit()):
        raise ContractError("hostname must be a canonical exact ASCII DNS hostname")
    if type(year) is not int or not 1996 <= year <= 2001:
        raise ContractError("year must be an integer in 1996..2001")
    return host, year


def cost(value):
    keys(value, COSTS)
    if any(type(value[k]) is not int or value[k] < 0 for k in COSTS):
        raise ContractError("costs must be nonnegative integers")


def component_hashes(raw):
    bindings = raw["source_bindings"]
    return {
        "baseline_sha256": digest({"origin": bindings["baseline_sha256"], "keys": raw["baseline_keys"]}),
        "reviewed_sha256": digest({"origin": bindings["reviewed_sha256"], "keys": raw["reviewed_keys"]}),
        "model_sha256": digest({"origin": bindings["model_sha256"], "weights": raw["weights"]}),
        "rules_sha256": digest({"origin": bindings["rules_sha256"], "contract": "upstream-qualified-exact-host-year-v1"}),
        "materials_sha256": digest({"initial_frontier": raw["initial_frontier"], "actions": [
            {k: v for k, v in a.items() if k != "outcome"} for a in raw["actions"]]}),
        "outcomes_sha256": digest([{ "id": a["id"], "outcome": a["outcome"]} for a in raw["actions"]]),
        "budget_sha256": digest(raw["budget"]),
    }


def validate_epoch(raw):
    keys(raw, {"schema_version", "epoch_id", "data_kind", "source_bindings", "baseline_keys",
               "reviewed_keys", "weights", "budget", "initial_frontier", "actions", "projection"})
    if raw["schema_version"] != 1 or type(raw["schema_version"]) is not int:
        raise ContractError("unsupported epoch schema")
    identity(raw["epoch_id"])
    if raw["data_kind"] not in ("recorded", "synthetic_fixture"):
        raise ContractError("data_kind must explicitly identify recorded or synthetic_fixture data")
    keys(raw["source_bindings"], BINDINGS)
    for name, value in raw["source_bindings"].items():
        sha(value, name)
    for name in ("baseline_keys", "reviewed_keys"):
        if not isinstance(raw[name], list):
            raise ContractError(f"{name} must be a list of exact keys, not a count")
        found = set()
        for row in raw[name]:
            keys(row, {"hostname", "year"})
            key = host_year(row)
            if key in found:
                raise ContractError(f"duplicate {name} key")
            found.add(key)
    if not isinstance(raw["weights"], dict) or not raw["weights"]:
        raise ContractError("fixed TLD weights are required")
    for tld, weight in raw["weights"].items():
        if not LABEL.fullmatch(tld) or type(weight) is not int or not 0 <= weight <= 10000:
            raise ContractError("weights must use exact lowercase TLDs and integer EED ten-thousandths")
    cost(raw["budget"])
    if not any(raw["budget"].values()):
        raise ContractError("at least one positive budget is required")
    if not isinstance(raw["actions"], list) or not raw["actions"]:
        raise ContractError("actions must contain actual per-action records")
    actions = {}
    for action in raw["actions"]:
        keys(action, {"id", "source_id", "artifact_id", "locator", "requires", "coverage",
                      "features", "cost_bound", "outcome"})
        for name in ("id", "source_id", "artifact_id"):
            identity(action[name])
        if action["id"] in actions:
            raise ContractError("duplicate action id")
        actions[action["id"]] = action
        text(action["locator"], "locator")
        dependencies = action["requires"]
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)):
            raise ContractError("requires must be a unique list")
        for dep in dependencies:
            identity(dep)
        coverage = action["coverage"]
        keys(coverage, {"input_sha256", "operator_revision", "record_locator", "original_url", "capture_or_edition"})
        for name in ("input_sha256", "operator_revision"):
            sha(coverage[name], name)
        for name in ("record_locator", "capture_or_edition"):
            text(coverage[name], name)
        if coverage["original_url"] is not None:
            text(coverage["original_url"], "original_url")
        features = action["features"]
        keys(features, {"year", "direct_evidence", "independent_family", "unprocessed"})
        if features["year"] is not None and (type(features["year"]) is not int or not 1996 <= features["year"] <= 2001):
            raise ContractError("feature year is a core-year cue or null, never an adjudication")
        if any(type(features[k]) is not bool for k in ("direct_evidence", "independent_family", "unprocessed")):
            raise ContractError("descriptive boolean features are required")
        cost(action["cost_bound"])
        outcome = action["outcome"]
        if not isinstance(outcome, dict):
            raise ContractError("every action needs a recorded outcome or explicit unobserved state")
        if outcome.get("state") == "unobserved":
            keys(outcome, {"state", "reason"})
            text(outcome["reason"], "reason")
            continue
        keys(outcome, {"state", "receipt_locator", "receipt_sha256", "cost", "records", "reveals"}, {"reason"})
        if outcome["state"] not in ("complete", "incomplete"):
            raise ContractError("outcome state must be complete, incomplete or unobserved")
        if outcome["state"] == "incomplete":
            text(outcome.get("reason"), "incomplete reason")
        text(outcome["receipt_locator"], "receipt_locator")
        sha(outcome["receipt_sha256"], "receipt_sha256")
        cost(outcome["cost"])
        if any(outcome["cost"][k] > action["cost_bound"][k] for k in COSTS):
            raise ContractError("recorded costs exceed the predeclared reservation bound")
        if not isinstance(outcome["records"], list):
            raise ContractError("records must contain individually adjudicated keys, not aggregate yields")
        for row in outcome["records"]:
            keys(row, {"hostname", "year", "evidence_id", "evidence_locator", "evidence_sha256", "adjudication"})
            host_year(row)
            if row["adjudication"] != "qualified":
                raise ContractError("only upstream-qualified records may enter the oracle")
            text(row["evidence_id"], "evidence_id")
            text(row["evidence_locator"], "evidence_locator")
            sha(row["evidence_sha256"], "evidence_sha256")
        reveals = outcome["reveals"]
        if not isinstance(reveals, list) or len(reveals) != len(set(reveals)):
            raise ContractError("reveals must be a unique list")
        for child in reveals:
            identity(child)
    initial = raw["initial_frontier"]
    if not isinstance(initial, list) or not initial or len(initial) != len(set(initial)):
        raise ContractError("initial_frontier must be an explicit unique nonempty list")
    for item in initial:
        if item not in actions or actions[item]["requires"]:
            raise ContractError("initial actions must exist and have no unmet parent dependency")
    edges = {item: set() for item in actions}
    for action in raw["actions"]:
        for parent in action["requires"]:
            if parent not in actions or parent == action["id"]:
                raise ContractError("invalid parent dependency")
        for child in action["outcome"].get("reveals", []):
            if child not in actions:
                raise ContractError("revealed child must exist in the frozen recorded action space")
            edges[action["id"]].add(child)
    visited, active = set(), set()
    def visit(item):
        if item in active:
            raise ContractError("cyclic action dependencies")
        if item in visited:
            return
        active.add(item)
        for parent in actions[item]["requires"]:
            visit(parent)
        active.remove(item)
        visited.add(item)
    for item in actions:
        visit(item)
    reachable, pending = set(initial), list(initial)
    while pending:
        for child in edges[pending.pop()]:
            if child not in reachable:
                reachable.add(child)
                pending.append(child)
    if reachable != actions.keys():
        raise ContractError("actions must originate in the observed frontier or recorded reveal edges")
    universe = {host_year(row) for a in raw["actions"] for row in a["outcome"].get("records", [])}
    projection = raw["projection"]
    keys(projection, {"key_universe_sha256", "receipt_locator", "receipt_sha256", "complete"})
    if projection["complete"] is not True:
        raise ContractError("trusted full-baseline/reviewed membership projection must cover the whole recorded key universe")
    sha(projection["receipt_sha256"], "projection receipt_sha256")
    text(projection["receipt_locator"], "projection receipt_locator")
    expected_universe = digest([{"hostname": h, "year": y} for h, y in sorted(universe)])
    if projection["key_universe_sha256"] != expected_universe:
        raise ContractError("membership projection does not bind the complete recorded key universe")
    for name in ("baseline_keys", "reviewed_keys"):
        if not {host_year(row) for row in raw[name]} <= universe:
            raise ContractError(f"{name} is a scoped projection and must not contain unrelated keys")
    return raw


def freeze_epoch(raw):
    validate_epoch(raw)
    return {"schema_version": 1, "epoch_digest": digest(raw),
            "component_sha256": component_hashes(raw), "data": raw}


def load_epoch(path):
    frozen = load_json(path)
    keys(frozen, {"schema_version", "epoch_digest", "component_sha256", "data"})
    expected = freeze_epoch(frozen["data"])
    if frozen != expected:
        raise ContractError("frozen epoch changed; prepare a new task instead of reusing old results")
    return frozen


def validate_policy(value):
    try:
        keys(value, {"schema_version", "order_by"})
        if value["schema_version"] != 1 or type(value["schema_version"]) is not int:
            raise ContractError("unsupported policy schema")
        order = value["order_by"]
        if not isinstance(order, list) or not 1 <= len(order) <= len(FIELDS):
            raise ContractError("order_by must be a nonempty bounded list")
        used = set()
        for rule in order:
            keys(rule, {"field", "direction"})
            if rule["field"] not in FIELDS or rule["field"] in used or rule["direction"] not in ("asc", "desc"):
                raise ContractError("invalid, repeated or non-observable ranking field")
            used.add(rule["field"])
    except (ContractError, TypeError) as exc:
        raise PolicyError(str(exc)) from exc
    return value


def arm_key(action):
    return action["source_id"], action["features"]["year"], action["coverage"]["operator_revision"]


def rank_actions(policy, actions, positions, arm_stats):
    def order(action):
        arm = arm_stats.get(arm_key(action), {"attempts": 0, "units": 0, "requests": 0})
        features = {**action["features"], "input_order": positions[action["id"]],
                    "arm_attempts": arm["attempts"],
                    "arm_yield": Fraction(arm["units"], max(1, arm["requests"])),
                    "request_bound": action["cost_bound"]["requests"],
                    "byte_bound": action["cost_bound"]["bytes"],
                    "wall_bound": action["cost_bound"]["wall_millis"]}
        features["year"] = features["year"] if features["year"] is not None else 9999
        values = [features[r["field"]] * (1 if r["direction"] == "asc" else -1)
                  for r in policy["order_by"]]
        return (*values, positions[action["id"]])
    return sorted(actions, key=order)


def fits(bound, spent, budget):
    return all(spent[k] + bound[k] <= budget[k] for k in COSTS)


def replay(frozen, policy):
    validate_policy(policy)
    raw = frozen["data"]
    actions = {a["id"]: a for a in raw["actions"]}
    # FIFO is discovery order, not the oracle's private whole-action table order.
    positions = {item: i for i, item in enumerate(raw["initial_frontier"])}
    visible, completed = set(raw["initial_frontier"]), set()
    seen = {host_year(r) for name in ("baseline_keys", "reviewed_keys") for r in raw[name]}
    credited, trace, arms = {}, [], {}
    spent = dict.fromkeys(COSTS, 0)
    reason, valid = "recorded_frontier_exhausted", True
    while True:
        eligible = [actions[i] for i in visible - completed if set(actions[i]["requires"]) <= completed]
        affordable = [a for a in eligible if fits(a["cost_bound"], spent, raw["budget"])]
        if not affordable:
            if eligible:
                reason = "budget_exhausted"
            elif visible - completed:
                reason, valid = "blocked_dependencies", False
            else:
                reason = "recorded_frontier_exhausted"
            break
        action = rank_actions(policy, affordable, positions, arms)[0]
        outcome = action["outcome"]
        row = {"action_id": action["id"], "source_id": action["source_id"],
               "coverage_id": digest(action["coverage"]), "state": outcome["state"],
               "arm": {"source_id": action["source_id"], "year": action["features"]["year"],
                       "operator_revision": action["coverage"]["operator_revision"]},
               "new_keys": [], "new_eed_units": 0}
        trace.append(row)
        if outcome["state"] == "unobserved":
            reason, valid = "unobserved_outcome", False
            row["cost_known"] = False
            break
        row.update(cost=outcome["cost"], receipt_sha256=outcome["receipt_sha256"])
        for unit in COSTS:
            spent[unit] += outcome["cost"][unit]
        if outcome["state"] == "incomplete":
            reason, valid = "incomplete_outcome", False
            row["reason"] = outcome["reason"]
            break
        for record in outcome["records"]:
            key = host_year(record)
            if key in seen:
                continue
            seen.add(key)
            weight = raw["weights"].get(key[0].rsplit(".", 1)[1], 0)
            credited[key] = {**record, "weight_units": weight, "action_id": action["id"]}
            row["new_keys"].append({"hostname": key[0], "year": key[1]})
            row["new_eed_units"] += weight
        arm = arms.setdefault(arm_key(action), {"attempts": 0, "units": 0, "requests": 0})
        arm["attempts"] += 1
        arm["units"] += row["new_eed_units"]
        arm["requests"] += outcome["cost"]["requests"]
        completed.add(action["id"])
        for child in outcome["reveals"]:
            if child not in visible:
                positions[child] = len(positions)
                visible.add(child)
    units = sum(r["weight_units"] for r in credited.values())
    metrics = {**spent, "actions_completed": len(completed), "actions_attempted": len(trace),
               "observed_new_hostname_years": len(credited), "observed_net_eed_units": units}
    if valid:
        metrics["net_eed_units"] = units
    return {"status": "completed" if valid else "infra_error", "valid": valid, "metrics": metrics,
            "completion_reason": reason, "trace": trace, "credited_records": list(credited.values()),
            "remaining_visible": sorted(visible - completed), "epoch_digest": frozen["epoch_digest"],
            "data_kind": raw["data_kind"], "score_scope": "recorded_action_replay",
            "competition_contribution_claimed": False, "production_ledger_written": False}


def plan(frozen, policy):
    """Plan only currently observed roots; never inspect future outcome benefits."""
    validate_policy(policy)
    raw = frozen["data"]
    by_id = {a["id"]: a for a in raw["actions"]}
    positions = {item: i for i, item in enumerate(raw["initial_frontier"])}
    ranked = rank_actions(policy, [by_id[i] for i in raw["initial_frontier"]], positions, {})
    reserved, selected = dict.fromkeys(COSTS, 0), []
    for action in ranked:
        if not fits(action["cost_bound"], reserved, raw["budget"]):
            continue
        selected.append({k: action[k] for k in ("id", "source_id", "artifact_id", "locator", "requires", "coverage", "cost_bound")})
        for unit in COSTS:
            reserved[unit] += action["cost_bound"][unit]
    return {"schema_version": 1, "kind": "creeperman_observed_frontier_plan", "plan_only": True,
            "online_requests": 0, "production_ledger_written": False,
            "epoch_digest": frozen["epoch_digest"], "policy_sha256": digest(policy),
            "reserved_cost": reserved, "actions": selected,
            "execution_contract": "Requires a separate Creeperman executor; recheck current coverage, quotas and production ledger before execution."}
