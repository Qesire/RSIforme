"""Synthetic contract fixtures; no historical discovery or competition EED claimed."""
from pathlib import Path
import sys
import tempfile
import unittest

from rsiforme.archive import Archive, ArchiveError


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "creeperman_replay"
sys.path.insert(0, str(EXAMPLE))
try:
    import contract as c
    from prepare import prepare
    from run import run
finally:
    sys.path.pop(0)


def h(label):
    return c.digest({"synthetic": label})


def record(host="one.example", year=1996):
    return {"hostname": host, "year": year, "evidence_id": f"fixture:{host}:{year}",
            "evidence_locator": f"synthetic://fixture/{host}/{year}",
            "evidence_sha256": h(host + str(year)), "adjudication": "qualified"}


def action(name="a", records=None, *, parent=None, reveals=None, year=1996, state="complete", requests=1):
    outcome = {"state": state, "receipt_locator": "synthetic://receipt/" + name,
               "receipt_sha256": h(name), "cost": {"requests": requests, "bytes": 10, "wall_millis": 2},
               "records": records or [], "reveals": reveals or []}
    if state == "unobserved":
        outcome = {"state": "unobserved", "reason": "No historical execution was recorded"}
    elif state == "incomplete":
        outcome["reason"] = "503 after the recorded prefix; remaining scope unknown"
    return {"id": name, "source_id": "family-" + name, "artifact_id": "file-" + name,
            "locator": "synthetic://artifact/" + name, "requires": [parent] if parent else [],
            "coverage": {"input_sha256": h("payload"), "operator_revision": h("extractor"),
                         "record_locator": "record:0", "original_url": "http://" + name + ".example/",
                         "capture_or_edition": "19960101"},
            "features": {"year": year, "direct_evidence": True, "independent_family": True, "unprocessed": True},
            "cost_bound": {"requests": requests, "bytes": 10, "wall_millis": 2}, "outcome": outcome}


def project(raw):
    universe = {c.host_year(r) for a in raw["actions"] for r in a["outcome"].get("records", [])}
    raw["projection"] = {"key_universe_sha256": c.digest([{"hostname": h, "year": y} for h, y in sorted(universe)]),
                         "receipt_locator": "synthetic://projection", "receipt_sha256": h("projection"), "complete": True}
    return raw


def epoch(actions=None, budget=20):
    actions = actions or [action(records=[record()])]
    return project({"schema_version": 1, "epoch_id": "synthetic-test", "data_kind": "synthetic_fixture",
                    "source_bindings": {k: h(k) for k in c.BINDINGS},
                    "baseline_keys": [], "reviewed_keys": [], "weights": {"example": 6000},
                    "budget": {"requests": budget, "bytes": 1000, "wall_millis": 1000},
                    "initial_frontier": [a["id"] for a in actions if not a["requires"]], "actions": actions})


FIFO = {"schema_version": 1, "order_by": [{"field": "input_order", "direction": "asc"}]}
EARLY = {"schema_version": 1, "order_by": [{"field": "year", "direction": "asc"}]}


class CreepermanReplayTests(unittest.TestCase):
    def test_credit_once_baseline_reviewed_and_exact_year_www(self):
        rows = [record(), record(), record("www.one.example"), record("one.example", 1997),
                record("known.example"), record("settled.example")]
        raw = epoch([action("a", rows), action("b", rows)])
        raw["baseline_keys"] = [{"hostname": "known.example", "year": 1996}]
        raw["reviewed_keys"] = [{"hostname": "settled.example", "year": 1996}]
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual(result["metrics"]["net_eed_units"], 18000)
        self.assertEqual(result["metrics"]["observed_new_hostname_years"], 3)
        self.assertEqual(result["trace"][1]["new_eed_units"], 0)
        self.assertEqual(result["metrics"]["requests"], 2)
        self.assertFalse(result["competition_contribution_claimed"])
        self.assertFalse(result["production_ledger_written"])

    def test_source_feature_year_does_not_supply_evidence_year(self):
        raw = epoch([action(records=[record(year=2000)], year=1996)])
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual(result["credited_records"][0]["year"], 2000)

    def test_complete_zero_is_valid_and_not_global_exhaustion(self):
        result = c.replay(c.freeze_epoch(epoch([action(records=[])])), FIFO)
        self.assertTrue(result["valid"])
        self.assertEqual(result["metrics"]["net_eed_units"], 0)
        self.assertEqual(result["completion_reason"], "recorded_frontier_exhausted")

    def test_incomplete_failure_cost_is_charged_but_not_ranked_as_zero(self):
        raw = epoch([action("a", [record()]), action("fail", state="incomplete", requests=3)])
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertFalse(result["valid"])
        self.assertEqual(result["completion_reason"], "incomplete_outcome")
        self.assertEqual(result["metrics"]["requests"], 4)
        self.assertEqual(result["metrics"]["observed_net_eed_units"], 6000)
        self.assertNotIn("net_eed_units", result["metrics"])

    def test_unobserved_action_is_not_assumed_empty(self):
        raw = epoch([action(state="unobserved")])
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertFalse(result["valid"])
        self.assertEqual(result["completion_reason"], "unobserved_outcome")
        self.assertFalse(result["trace"][0]["cost_known"])
        self.assertNotIn("net_eed_units", result["metrics"])

    def test_unselected_unknown_does_not_invalidate_known_budget_prefix(self):
        raw = epoch([action("a", [record()]), action("unknown", state="unobserved")], budget=1)
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertTrue(result["valid"])
        self.assertEqual(result["completion_reason"], "budget_exhausted")

    def test_parent_discovery_precedes_attractive_child_and_plan_excludes_child(self):
        raw = epoch([action("parent", reveals=["child"], year=2001),
                     action("child", [record()], parent="parent", year=1996)], budget=2)
        frozen = c.freeze_epoch(raw)
        result = c.replay(frozen, EARLY)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["parent", "child"])
        plan = c.plan(frozen, EARLY)
        self.assertEqual([a["id"] for a in plan["actions"]], ["parent"])
        self.assertNotIn("outcome", plan["actions"][0])
        self.assertEqual(plan["online_requests"], 0)

    def test_bad_parent_and_orphan_frontier_rejected(self):
        raw = epoch([action("parent", reveals=["child"]), action("child", parent="missing")])
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)
        raw = epoch([action("parent"), action("child", parent="parent")])
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)

    def test_either_discovery_parent_unlocks_child_without_and_dependency(self):
        raw = epoch([action("a", reveals=["child"], year=1996),
                     action("b", reveals=["child"], year=2001),
                     action("child", [record()], year=1996)], budget=2)
        raw["initial_frontier"] = ["a", "b"]
        result = c.replay(c.freeze_epoch(raw), EARLY)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "child"])
        self.assertEqual(result["metrics"]["net_eed_units"], 6000)
        raw["budget"]["requests"] = 3
        result = c.replay(c.freeze_epoch(raw), EARLY)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "child", "b"])
        self.assertEqual(result["metrics"]["net_eed_units"], 6000)

    def test_discovery_cycles_are_allowed_and_do_not_repeat_actions(self):
        raw = epoch([action("a", [record()], reveals=["b"]), action("b", [], reveals=["a"])])
        raw["initial_frontier"] = ["a"]
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "b"])

    def test_reservation_bound_enforces_each_budget_dimension(self):
        raw = epoch([action("large", [record()]), action("small", [record("small.example")])])
        raw["actions"][0]["cost_bound"]["bytes"] = 100
        raw["budget"]["bytes"] = 10
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["small"])
        self.assertEqual(result["metrics"]["bytes"], 10)
        raw["actions"][0]["outcome"]["cost"]["bytes"] = 101
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)

    def test_budget_uses_recorded_cost_not_full_reservation(self):
        raw = epoch([action("a", [record()]), action("b", [record("two.example")])], budget=3)
        raw["actions"][0]["cost_bound"]["requests"] = 3
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual(result["metrics"]["requests"], 2)
        self.assertEqual(result["metrics"]["net_eed_units"], 12000)

    def test_policy_cannot_read_outcomes_choose_ids_or_supply_credit(self):
        for policy in ({**FIFO, "verified": True}, {**FIFO, "net_eed_units": 123},
                       {"schema_version": 1, "order_by": [{"field": "outcome", "direction": "desc"}]},
                       {**FIFO, "action_ids": ["a"]}):
            with self.subTest(policy=policy), self.assertRaises(c.PolicyError):
                c.validate_policy(policy)

    def test_future_outcome_does_not_change_plan(self):
        raw = epoch([action("a", [record()]), action("b", [])], budget=1)
        original = c.plan(c.freeze_epoch(raw), FIFO)["actions"]
        raw["actions"][1]["outcome"]["records"] = [record("rich.example", y) for y in range(1996, 2002)]
        changed = c.plan(c.freeze_epoch(project(raw)), FIFO)["actions"]
        self.assertEqual(original, changed)

    def test_fifo_uses_initial_frontier_then_new_discovery_order(self):
        # C's position in the complete oracle must not let it jump ahead of B.
        raw = epoch([action("a", reveals=["c"]), action("c"), action("b")])
        raw["initial_frontier"] = ["a", "b"]
        frozen = c.freeze_epoch(raw)
        result = c.replay(frozen, FIFO)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "b", "c"])
        raw["initial_frontier"] = ["b", "a"]
        plan = c.plan(c.freeze_epoch(raw), FIFO)
        self.assertEqual([a["id"] for a in plan["actions"]], ["b", "a"])

    def test_repeated_reveal_preserves_first_discovery_position(self):
        raw = epoch([action("a", reveals=["c"]), action("b", reveals=["d", "c"]),
                     action("d"), action("c")])
        raw["initial_frontier"] = ["a", "b"]
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "b", "c", "d"])

    def test_arm_yield_uses_only_prior_observations(self):
        actions = [action("a", [record()]), action("b", []), action("c", [record("third.example")])]
        actions[2]["source_id"] = actions[0]["source_id"]
        policy = {"schema_version": 1, "order_by": [{"field": "arm_yield", "direction": "desc"}]}
        result = c.replay(c.freeze_epoch(epoch(actions)), policy)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "c", "b"])

    def test_year_and_operator_feedback_do_not_leak_between_arms(self):
        actions = [action("a", [record()], year=2000), action("b", [], year=2001),
                   action("c", [record("third.example")], year=2000), action("d", [], year=2000)]
        for item in actions:
            item["source_id"] = "same-publisher"
        actions[3]["coverage"]["operator_revision"] = h("different operator")
        # Both b (different year) and d (different operator) stay at zero feedback.
        # c inherits only a's same-year/same-operator observation despite being last in input order.
        actions = [actions[0], actions[1], actions[3], actions[2]]
        policy = {"schema_version": 1, "order_by": [{"field": "arm_yield", "direction": "desc"}]}
        result = c.replay(c.freeze_epoch(epoch(actions)), policy)
        self.assertEqual([r["action_id"] for r in result["trace"]], ["a", "c", "b", "d"])

    def test_blocked_dependencies_are_not_reported_as_exhausted(self):
        raw = epoch([action("a", reveals=["c"]), action("c", parent="d", reveals=["d"]), action("d")])
        raw["initial_frontier"] = ["a"]
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertFalse(result["valid"])
        self.assertEqual(result["completion_reason"], "blocked_dependencies")
        self.assertEqual(result["remaining_visible"], ["c"])
        self.assertNotIn("net_eed_units", result["metrics"])

    def test_projection_must_bind_complete_key_universe(self):
        raw = epoch()
        raw["projection"]["complete"] = False
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)
        raw = epoch()
        raw["projection"]["key_universe_sha256"] = h("wrong")
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)
        raw = epoch()
        raw["baseline_keys"] = [{"hostname": "unrelated.example", "year": 2001}]
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)

    def test_aggregate_count_cannot_replace_qualified_record_rows(self):
        raw = epoch()
        raw["actions"][0]["outcome"]["records"] = {"count": 100, "eed": "63.2063"}
        with self.assertRaises(c.ContractError):
            c.freeze_epoch(raw)

    def test_coverage_distinguishes_same_payload_different_original_url(self):
        raw = epoch([action("a", [record()]), action("b", [record()])])
        result = c.replay(c.freeze_epoch(raw), FIFO)
        self.assertNotEqual(result["trace"][0]["coverage_id"], result["trace"][1]["coverage_id"])
        self.assertEqual(result["metrics"]["net_eed_units"], 6000)

    def test_frozen_epoch_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "epoch.json"
            frozen = c.freeze_epoch(epoch())
            frozen["data"]["reviewed_keys"] = [{"hostname": "one.example", "year": 1996}]
            c.write_json(path, frozen)
            with self.assertRaises(c.ContractError):
                c.load_epoch(path)

    def test_actual_rsiforme_binding_and_ledger_context_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inp, policy = root / "input.json", root / "policy.json"
            raw = epoch([action("late", [], year=2001), action("early", [record()], year=1996)], budget=1)
            c.write_json(inp, raw)
            c.write_json(policy, EARLY)
            prepared = root / "prepared"
            prepare(inp, prepared)
            result = run(prepared, policy, root / "archive")
            evaluation = result["evaluation"]
            self.assertTrue(evaluation["valid"])
            self.assertEqual(evaluation["metrics"]["net_eed_units"], 6000)
            self.assertEqual(evaluation["data_kind"], "synthetic_fixture")
            self.assertIn("execution", evaluation)
            self.assertIn("evidence_digest", evaluation)
            self.assertFalse(evaluation["competition_contribution_claimed"])
            self.assertTrue(Path(result["profile"]).is_file())
            archive = Archive(root / "archive")
            archive.verify_evidence(evaluation["id"])
            from rsiforme.research import ResearchController
            program = c.load_json(EXAMPLE / "research.example.json")
            controller = ResearchController(archive)
            controller.initialize(program, baseline_evaluation=evaluation["id"], profile=result["profile"])
            trial = controller.prepare()
            handoff = c.load_json(Path(trial["workspace"]) / "handoff.json")
            self.assertEqual(handoff["source_paths"], ["policy.json"])
            self.assertEqual(handoff["baseline_evaluation"]["id"], evaluation["id"])
            raw["reviewed_keys"] = [{"hostname": "one.example", "year": 1996}]
            raw["source_bindings"]["reviewed_sha256"] = h("next production ledger")
            changed_input = root / "changed-input.json"
            c.write_json(changed_input, raw)
            prepare(changed_input, root / "changed-prepared")
            with self.assertRaises(ArchiveError):
                run(root / "changed-prepared", policy, root / "archive")

    def test_prepared_evaluator_assets_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c.write_json(root / "input.json", epoch())
            c.write_json(root / "policy.json", FIFO)
            prepare(root / "input.json", root / "prepared")
            asset = root / "prepared" / "evaluator" / "contract.py"
            asset.write_text(asset.read_text() + "\n# changed evaluator dependency\n")
            with self.assertRaises(ArchiveError):
                run(root / "prepared", root / "policy.json", root / "archive")


if __name__ == "__main__":
    unittest.main()
