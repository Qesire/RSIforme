"""Offline export contracts; all inputs, including recorded-format tests, are fabricated.

Tests exercise the trusted-exporter boundary, not the truth of source evidence.
No fixture results are a real recorded epoch, online validation, or competition EED.
"""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest

from rsiforme.archive import Archive, ArchiveError
from rsiforme.workflow import evaluate_local
from test_creeperman_replay import EXAMPLE, EARLY, FIFO, action, c, epoch, prepare, run

sys.path.insert(0, str(EXAMPLE))
try:
    from export_live import export_bootstrap, export_evaluated
finally:
    sys.path.pop(0)


def released_fixture(root, *, recorded_format=False, policy=EARLY):
    raw = epoch([action("late", year=2001), action("early", year=1996)])
    if recorded_format:
        # Simulate a trusted exporter classification for contract acceptance only.
        # It does not establish authentic historical provenance or real contribution.
        raw["data_kind"] = "recorded"
    c.write_json(root / "epoch.json", raw)
    c.write_json(root / "policy.json", policy)
    prepare(root / "epoch.json", root / "prepared")
    result = run(root / "prepared", root / "policy.json", root / "archive")
    archive = Archive(root / "archive")
    confirm = evaluate_local(archive, result["candidate_id"], root / "prepared" / "evaluator" / "evaluate.py",
                             kind="confirmation", confirmation_of=result["evaluation"]["id"])
    release = archive.promote(result["candidate_id"], result["evaluation"]["id"], confirm["id"])
    return archive, result, confirm, release


class LiveExportTests(unittest.TestCase):
    def test_bootstrap_identity_binds_policy_and_live_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one = export_bootstrap(root / "one.json", reason="Explicit bootstrap; no measured improvement")
            same = export_bootstrap(root / "same.json", reason="Explicit bootstrap; no measured improvement")
            changed = export_bootstrap(root / "changed.json", reason="Explicit bootstrap; no measured improvement",
                                       explore_every=3)
            self.assertEqual(one, same)
            self.assertNotEqual(one["provenance"]["policy_id"], changed["provenance"]["policy_id"])
            self.assertEqual(one["provenance"]["status"], "unvalidated_bootstrap_prior")
            self.assertEqual(one["provenance"]["runtime_extension"]["status"], "unvalidated")
            self.assertEqual(one["provenance"]["online_validation_status"], "not_evaluated")
            self.assertNotIn("evaluation_id", one["provenance"])
            self.assertFalse(one["provenance"]["production_increment_claimed"])
            self.assertEqual(c.load_json(root / "one.json"), one)
            with self.assertRaises(FileExistsError):
                export_bootstrap(root / "one.json", reason="No overwrite")

    def test_missing_live_features_are_rejected_not_reinterpreted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in ("request_bound", "byte_bound", "wall_bound", "independent_family", "future_eed"):
                with self.subTest(field=field), self.assertRaisesRegex(c.ContractError, "no silent mapping"):
                    export_bootstrap(root / (field + ".json"), reason="field contract",
                                     policy={"schema_version": 1, "order_by": [{"field": field, "direction": "asc"}]})
            self.assertEqual(list(root.iterdir()), [])

    def test_runtime_bounds_and_reason_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for options in ({"reason": ""}, {"explore_every": True}, {"explore_every": 1},
                            {"path_decay": float("nan")}, {"path_decay": 0}):
                with self.subTest(options=options), self.assertRaises(c.ContractError):
                    export_bootstrap(path, **{"reason": "explicit prior", **options})
            self.assertFalse(path.exists())

    def test_search_without_confirmation_or_promotion_is_not_live_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c.write_json(root / "epoch.json", epoch())
            c.write_json(root / "policy.json", FIFO)
            prepare(root / "epoch.json", root / "prepared")
            run(root / "prepared", root / "policy.json", root / "archive")
            with self.assertRaisesRegex(ArchiveError, "promoted release"):
                export_evaluated(root / "archive", root / "live.json", as_bootstrap_prior=True,
                                 reason="synthetic test")
            self.assertFalse((root / "live.json").exists())

    def test_synthetic_release_can_only_be_an_explicit_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, result, confirm, release = released_fixture(root)
            before = (len(archive.evaluations()), archive.summary()["current"])
            with self.assertRaisesRegex(c.ContractError, "synthetic_fixture cannot"):
                export_evaluated(archive.root, root / "rejected.json")
            self.assertFalse((root / "rejected.json").exists())
            live = export_evaluated(archive.root, root / "live.json", as_bootstrap_prior=True,
                                    reason="Use the ordering as an unvalidated prior; fixture is not evidence of gain")
            p = live["provenance"]
            self.assertEqual(p["status"], "unvalidated_bootstrap_prior")
            self.assertEqual(p["data_kind"], "synthetic_fixture")
            self.assertEqual(p["evaluation_id"], result["evaluation"]["id"])
            self.assertEqual(p["confirmation_evaluation_id"], confirm["id"])
            self.assertEqual(p["release_id"], release["id"])
            self.assertEqual(p["source_policy_sha256"], c.digest(EARLY))
            self.assertEqual(p["profile_sha256"], c.digest(c.load_json(result["profile"])))
            self.assertEqual(before, (len(archive.evaluations()), archive.summary()["current"]))

    def test_recorded_format_release_exports_only_original_ordering_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, result, _, _ = released_fixture(root, recorded_format=True)
            live = export_evaluated(archive.root, root / "live.json")
            p = live["provenance"]
            self.assertEqual(p["status"], "validated_replay")
            self.assertEqual(live["order_by"], EARLY["order_by"])
            self.assertEqual(p["epoch_digest"], result["evaluation"]["epoch_digest"])
            self.assertEqual(p["search_evidence_sha256"], result["evaluation"]["evidence_digest"])
            self.assertEqual(p["runtime_extension"]["status"], "unvalidated")
            self.assertEqual(p["online_validation_status"], "not_evaluated")
            self.assertFalse(p["production_increment_claimed"])

    def test_tampered_profile_is_not_a_valid_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, result, _, _ = released_fixture(root)
            profile = c.load_json(result["profile"])
            profile["findings"][0]["metrics"]["net_eed_units"] = 999999
            Path(result["profile"]).write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(c.ContractError, "profile findings"):
                export_evaluated(archive.root, root / "rejected.json", as_bootstrap_prior=True, reason="fixture")

    def test_changed_evaluation_summary_cannot_overrule_sealed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, result, _, _ = released_fixture(root)
            path = archive.root / "evaluations" / (result["evaluation"]["id"] + ".json")
            evaluation = c.load_json(path)
            evaluation["metrics"]["net_eed_units"] = 999999
            path.write_text(json.dumps(evaluation), encoding="utf-8")
            with self.assertRaisesRegex(c.ContractError, "sealed replay report"):
                export_evaluated(archive.root, root / "rejected.json", as_bootstrap_prior=True, reason="fixture")

    def test_changed_raw_evidence_is_rejected_by_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, result, _, _ = released_fixture(root)
            report = archive.root / result["evaluation"]["evidence_path"] / "report.json"
            report.write_text("{}", encoding="utf-8")
            with self.assertRaises(ArchiveError):
                export_evaluated(archive.root, root / "rejected.json", as_bootstrap_prior=True, reason="fixture")


@unittest.skipUnless(os.environ.get("CREEPERMAN_SOURCE_ROOT"), "set CREEPERMAN_SOURCE_ROOT for cross-repository integration")
class CreepermanLoaderIntegrationTests(unittest.TestCase):
    def test_exported_policy_reorders_actual_frontier(self):
        source = Path(os.environ["CREEPERMAN_SOURCE_ROOT"]).resolve() / "src"
        sys.path.insert(0, str(source))
        try:
            from creeperman.discovery_policy import LivePolicy
            from creeperman.source_frontier import Frontier, SCHEMA
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, result, _, _ = released_fixture(root)
                export_evaluated(archive.root, root / "live.json", as_bootstrap_prior=True,
                                 reason="Offline cross-repository contract test; synthetic replay is not a measured gain")
                frontier = Frontier(root / "frontier.sqlite", policy=root / "live.json")
                try:
                    frontier.init({"schema": SCHEMA, "seeds": [
                        {"family": "later", "provider": "ia", "action": "search", "key": "directory 2001"},
                        {"family": "earlier", "provider": "ia", "action": "search", "key": "directory 1996"},
                    ]})
                    incoming = frontier.db.execute("SELECT * FROM tasks ORDER BY created").fetchall()
                    self.assertEqual(incoming[0]["family"], "later")
                    policy = frontier.policy
                    self.assertIsInstance(policy, LivePolicy)
                    selected = frontier.choose(["ia"], 3)
                    self.assertEqual(selected["family"], "earlier")
                    choice = frontier.db.execute("SELECT * FROM discovery_choices").fetchone()
                    self.assertEqual(choice["policy_sha"], policy.policy_sha)
                    self.assertEqual(policy.config["provenance"]["status"], "unvalidated_bootstrap_prior")
                    self.assertEqual(policy.config["provenance"]["evaluation_id"], result["evaluation"]["id"])
                finally:
                    frontier.close()
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
