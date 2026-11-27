"""State invariants; synthetic measurements test selection, not performance."""

import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from rsiforme.archive import Archive, ArchiveError, atomic_json, file_sha, now


class ArchiveTests(unittest.TestCase):
    def test_promotion_retry_finishes_current_pointer_without_duplicate_release(self):
        import rsiforme.archive as module
        candidate = self.capture()
        search = self.record(candidate, "transaction-search", 2)
        confirmation = self.record(candidate, "transaction-confirm", 2, kind="confirmation")
        original = module.atomic_json

        def interrupted(path, value):
            if path.name == "CURRENT.json":
                raise OSError("Injected interruption before publishing CURRENT")
            return original(path, value)

        with mock.patch.object(module, "atomic_json", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.archive.promote(candidate["id"], search["id"], confirmation["id"])
        self.assertFalse((self.archive.root / "CURRENT.json").exists())
        release = self.archive.promote(candidate["id"], search["id"], confirmation["id"])
        self.assertEqual(self.archive.summary()["current"]["release_id"], release["id"])
        self.assertEqual(len(self.archive._records("releases")), 1)
        self.assertEqual(self.archive.promote(candidate["id"], search["id"], confirmation["id"]), release)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        (self.work / "solution.py").write_text("answer = 1\n", encoding="utf-8")
        self.task = {"schema_version": 1, "task_id": "test", "objective": "speed",
                     "direction": "maximize", "source_paths": ["solution.py"],
                     "context": {"task_revision": "v1", "evaluator_revision": "test-fixture-v1",
                                 "environment": "synthetic unit-test context"}}
        self.archive = Archive(self.root / "archive")
        self.archive.initialize(self.task)

    def capture(self, parent=None):
        return self.archive.capture(self.work, lane="test", hypothesis="Test an explicit source change", parent_id=parent)

    def record(self, candidate, name, speed, kind="search", valid=True, **kwargs):
        previous = [row for row in self.archive.evaluations()
                    if row["candidate_id"] == candidate["id"] and row["kind"] == "search"
                    and row["status"] == "completed" and row["valid"]]
        timestamp = now()
        execution = {"id": "synthetic-" + name, "started_at": timestamp, "finished_at": timestamp,
                     "confirmation_of": previous[-1]["id"] if kind == "confirmation" and previous else None}
        evidence = Path(tempfile.mkdtemp(prefix="synthetic-evidence-", dir=self.root))
        (evidence / "measurement.json").write_text(json.dumps({"synthetic": True, "run": name}))
        return self.archive.record_evaluation(candidate["id"], {
            "source_digest": candidate["source_digest"],
            "context_fingerprint": candidate["context_fingerprint"],
            "eval_run_id": name, "status": "completed", "valid": valid,
            "metrics": {"speed": speed, "quality": 10 - speed}, "execution": execution, **kwargs,
        }, kind=kind, evidence=evidence)

    def test_task_reopen_is_idempotent_but_changed_context_is_rejected(self):
        original = self.archive.initialize(self.task)
        self.assertEqual(original, Archive(self.archive.root).config)
        changed = {**self.task, "direction": "minimize"}
        with self.assertRaisesRegex(ArchiveError, "new archive"):
            self.archive.initialize(changed)

    def test_parent_and_scope_are_explicit(self):
        with self.assertRaisesRegex(ArchiveError, "Unknown candidate"):
            self.capture("missing-parent")
        for paths in (["../secret"], ["/etc"], ["."], ["src", "src/a.py"]):
            with self.subTest(paths=paths), self.assertRaises(ArchiveError):
                Archive(self.root / "bad").initialize({**self.task, "source_paths": paths})

    def test_capture_is_immutable_restore_has_exact_bytes(self):
        original = (self.work / "solution.py").read_bytes()
        candidate = self.capture()
        (self.work / "solution.py").write_text("answer = 2\n")
        self.assertFalse(self.archive.matches_workspace(candidate["id"], self.work))
        restored = self.archive.restore(candidate["id"], self.root / "restored")
        self.assertEqual((restored / "solution.py").read_bytes(), original)
        with self.assertRaisesRegex(ArchiveError, "must not exist"):
            self.archive.restore(candidate["id"], restored)

    def test_executable_mode_is_restored(self):
        (self.work / "solution.py").chmod(0o755)
        candidate = self.capture()
        restored = self.archive.restore(candidate["id"], self.root / "restored")
        self.assertEqual((restored / "solution.py").stat().st_mode & 0o777, 0o755)

    def test_symlink_source_is_rejected(self):
        (self.work / "solution.py").unlink()
        (self.work / "solution.py").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ArchiveError, "symlink"):
            self.capture()

    def test_archived_tampering_and_extra_files_block_restore(self):
        candidate = self.capture()
        source = self.archive.source(candidate["id"])
        (source / "extra.py").write_text("unexpected = True\n")
        with self.assertRaisesRegex(ArchiveError, "extra files"):
            self.archive.restore(candidate["id"], self.root / "restored")
        (source / "extra.py").unlink()
        (source / "solution.py").write_text("answer = 999\n")
        with self.assertRaisesRegex(ArchiveError, "changed"):
            self.archive.source(candidate["id"])

    def test_best_uses_one_valid_search_evaluation(self):
        first = self.capture()
        a = self.record(first, "first", 3)
        second = self.capture(first["id"])
        self.record(second, "invalid-fast", 100, valid=False)
        self.record(first, "confirmation", 1000, kind="confirmation")
        result = self.archive.best()
        self.assertEqual(result["evaluation"]["id"], a["id"])
        self.assertEqual(result["evaluation"]["metrics"], {"speed": 3, "quality": 7})

    def test_minimize_direction_is_respected(self):
        archive = Archive(self.root / "minimum")
        archive.initialize({**self.task, "direction": "minimize"})
        self.archive = archive
        candidate = self.capture()
        self.record(candidate, "slow", 5)
        fast = self.record(candidate, "fast", 2)
        self.assertEqual(archive.best()["evaluation"]["id"], fast["id"])

    def test_wrong_source_context_and_nonfinite_metric_rejected(self):
        candidate = self.capture()
        for overrides in ({"source_digest": "wrong"}, {"context_fingerprint": "wrong"},
                          {"metrics": {"speed": float("nan")}}, {"metrics": {"speed": True}},
                          {"status": "infra_error", "valid": True}):
            with self.subTest(overrides=overrides), self.assertRaises(ArchiveError):
                self.record(candidate, "invalid", 1, **overrides)

    def test_repeated_evaluator_execution_cannot_be_counted_twice(self):
        candidate = self.capture()
        self.record(candidate, "same-execution", 3)
        with self.assertRaisesRegex(ArchiveError, "already recorded"):
            self.record(candidate, "same-execution", 4, kind="confirmation")

    def test_execution_identity_is_unique_even_with_different_evaluator_run_ids(self):
        candidate = self.capture()
        original = self.record(candidate, "first-report", 3)
        with self.assertRaisesRegex(ArchiveError, "already recorded"):
            self.record(candidate, "renamed-report", 4, execution=original["execution"])

    def test_execution_metadata_requires_aware_ordered_times_and_causal_field(self):
        candidate = self.capture()
        timestamp = now()
        valid = {"id": "execution-test", "started_at": timestamp, "finished_at": timestamp,
                 "confirmation_of": None}
        earlier = (dt.datetime.fromisoformat(timestamp) - dt.timedelta(seconds=1)).isoformat()
        cases = [{**valid, "id": None}, {**valid, "started_at": "2026-01-01T00:00:00"},
                 {**valid, "finished_at": earlier},
                 {key: value for key, value in valid.items() if key != "confirmation_of"},
                 {**valid, "confirmation_of": "e-not-a-search"}]
        for execution in cases:
            with self.subTest(execution=execution), self.assertRaises(ArchiveError):
                self.record(candidate, "bad-execution", 1, execution=execution)

    def test_pending_submission_is_recovered_without_resubmitting(self):
        candidate = self.capture()
        intent = self.archive.begin_submission(candidate["id"], "run-1")
        reopened = Archive(self.archive.root)
        with self.assertRaisesRegex(ArchiveError, "unresolved"):
            reopened.begin_submission(candidate["id"], "run-1")
        bound = reopened.bind_submission(intent["id"], "agent-1", note="Matched saved native history")
        self.assertEqual(reopened.binding("run-1", "agent-1"), bound)
        next_intent = reopened.begin_submission(candidate["id"], "run-1")
        with self.assertRaisesRegex(ArchiveError, "already bound"):
            reopened.bind_submission(next_intent["id"], "agent-1", note="Wrong duplicate")

    def test_harness_evaluation_requires_matching_prior_binding(self):
        candidate = self.capture()
        with self.assertRaisesRegex(ArchiveError, "No unique"):
            self.record(candidate, "judge-1", 1, harness_run_id="run-1", round_id="agent-1")
        intent = self.archive.begin_submission(candidate["id"], "run-1")
        self.archive.bind_submission(intent["id"], "agent-1", note="Native complete response")
        result = self.record(candidate, "judge-1", 1, harness_run_id="run-1", round_id="agent-1")
        self.assertEqual(result["candidate_id"], candidate["id"])

    def test_confirmation_submission_selects_a_valid_prior_search_or_explicit_one(self):
        candidate = self.capture()
        with self.assertRaisesRegex(ArchiveError, "existing valid search"):
            self.archive.begin_submission(candidate["id"], "empty-confirm", kind="confirmation")
        first = self.record(candidate, "first-search", 1)
        latest = self.record(candidate, "latest-search", 2)
        self.record(candidate, "invalid-search", 99, valid=False)
        automatic = self.archive.begin_submission(candidate["id"], "auto-confirm", kind="confirmation")
        explicit = self.archive.begin_submission(candidate["id"], "explicit-confirm", kind="confirmation",
                                                 confirmation_of=first["id"])
        self.assertEqual(automatic["confirmation_of"], latest["id"])
        self.assertEqual(explicit["confirmation_of"], first["id"])
        self.assertLessEqual(latest["created_at"], automatic["created_at"])
        other = self.capture(candidate["id"])
        with self.assertRaisesRegex(ArchiveError, "existing valid search"):
            self.archive.begin_submission(other["id"], "wrong-candidate", kind="confirmation",
                                          confirmation_of=first["id"])
        with self.assertRaisesRegex(ArchiveError, "Only a confirmation"):
            self.archive.begin_submission(candidate["id"], "search-with-parent", confirmation_of=first["id"])

    def test_native_confirmation_execution_cannot_change_intent_search(self):
        candidate = self.capture()
        first = self.record(candidate, "initial-search", 1)
        latest = self.record(candidate, "new-search", 2)
        intent = self.archive.begin_submission(candidate["id"], "confirm-run", kind="confirmation",
                                                confirmation_of=first["id"])
        self.archive.bind_submission(intent["id"], "agent-1", note="Native completion")
        timestamp = now()
        with self.assertRaisesRegex(ArchiveError, "confirmation_of"):
            self.record(candidate, "native-confirm", 2, kind="confirmation", harness_run_id="confirm-run",
                        round_id="agent-1", execution={"id": "native-confirm-execution", "started_at": timestamp,
                        "finished_at": timestamp, "confirmation_of": latest["id"]})

    def test_promotion_requires_valid_fresh_confirmation_same_source(self):
        candidate = self.capture()
        search = self.record(candidate, "search", 2)
        failed = self.record(candidate, "failed-confirm", 9, kind="confirmation", valid=False)
        with self.assertRaisesRegex(ArchiveError, "fresh confirmation"):
            self.archive.promote(candidate["id"], search["id"], failed["id"])
        self.assertFalse((self.archive.root / "CURRENT.json").exists())
        confirmation = self.record(candidate, "confirmation", 2, kind="confirmation")
        release = self.archive.promote(candidate["id"], search["id"], confirmation["id"])
        self.assertEqual(self.archive.summary()["current"]["release_id"], release["id"])

    def test_confirmation_must_follow_search(self):
        candidate = self.capture()
        old_timestamp = now()
        search = self.record(candidate, "later-search", 2)
        confirmation = self.record(candidate, "imported-old-confirmation", 2, kind="confirmation",
                                   execution={"id": "old-execution", "started_at": old_timestamp,
                                              "finished_at": old_timestamp, "confirmation_of": search["id"]})
        with self.assertRaisesRegex(ArchiveError, "must follow"):
            self.archive.promote(candidate["id"], search["id"], confirmation["id"])

    def test_delayed_search_import_does_not_make_an_old_execution_a_new_confirmation(self):
        candidate = self.capture()
        earlier = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        search = self.record(candidate, "delayed-search-import", 2, execution={"id": "old-search",
                             "started_at": earlier.isoformat(), "finished_at": earlier.isoformat(),
                             "confirmation_of": None})
        confirmation_time = (earlier + dt.timedelta(hours=1)).isoformat()
        confirmation = self.record(candidate, "delayed-confirmation-import", 2, kind="confirmation",
                                   execution={"id": "old-confirmation", "started_at": confirmation_time,
                                              "finished_at": confirmation_time, "confirmation_of": search["id"]})
        with self.assertRaisesRegex(ArchiveError, "recorded result"):
            self.archive.promote(candidate["id"], search["id"], confirmation["id"])

    def test_confirmation_names_the_exact_selected_search(self):
        candidate = self.capture()
        first = self.record(candidate, "first-search", 1)
        self.record(candidate, "second-search", 2)
        confirmation = self.record(candidate, "confirm-second-search", 2, kind="confirmation")
        with self.assertRaisesRegex(ArchiveError, "exact selected search"):
            self.archive.promote(candidate["id"], first["id"], confirmation["id"])

    def test_legacy_records_remain_readable_and_rankable_but_cannot_promote(self):
        candidate = self.capture()
        search = self.record(candidate, "legacy-search", 3, execution=None)
        confirmation = self.record(candidate, "legacy-confirm", 3, kind="confirmation", execution=None)
        self.assertEqual(self.archive.best()["evaluation"]["id"], search["id"])
        self.assertEqual(Archive(self.archive.root).evaluation(confirmation["id"]), confirmation)
        with self.assertRaisesRegex(ArchiveError, "legacy records"):
            self.archive.promote(candidate["id"], search["id"], confirmation["id"])

    def test_evidence_is_copied_before_external_directory_disappears(self):
        candidate = self.capture()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / "raw.json").write_text('{"measured": true}')
            (evidence / "__pycache__").mkdir()
            (evidence / "__pycache__" / "diagnostic.pyc").write_bytes(b"synthetic evidence")
            (evidence / ".diagnostic").write_text("synthetic hidden diagnostic")
            result = {"status": "completed", "valid": True, "metrics": {"speed": 2},
                      "eval_run_id": "evidence-test", "source_digest": candidate["source_digest"],
                      "context_fingerprint": candidate["context_fingerprint"]}
            record = self.archive.record_evaluation(candidate["id"], result, evidence=evidence)
        self.assertTrue((self.archive.root / record["evidence_path"] / "raw.json").is_file())
        record_path = self.archive.root / "evaluations" / (record["id"] + ".json")
        before = record_path.read_bytes()
        verified = self.archive.verify_evidence(record["id"])
        self.assertTrue(verified["verified"])
        self.assertEqual({item["path"] for item in verified["files"]},
                         {"raw.json", "__pycache__/diagnostic.pyc", ".diagnostic"})
        self.assertEqual(record_path.read_bytes(), before)

    def test_evidence_mutation_missing_and_extra_files_are_rejected(self):
        candidate = self.capture()
        for mutation in ("rewrite", "remove", "extra"):
            with self.subTest(mutation=mutation):
                result = self.record(candidate, "evidence-" + mutation, 1)
                evidence = self.archive.root / result["evidence_path"]
                if mutation == "rewrite":
                    (evidence / "measurement.json").write_text("changed")
                elif mutation == "remove":
                    (evidence / "measurement.json").unlink()
                else:
                    (evidence / "extra.log").write_text("late unsealed output")
                with self.assertRaisesRegex(ArchiveError, "changed|missing/extra"):
                    self.archive.verify_evidence(result["id"])

    def test_later_evidence_is_append_only_and_all_attachments_are_verified(self):
        candidate = self.capture()
        result = self.record(candidate, "initial-feedback", 1)
        record_path = self.archive.root / "evaluations" / (result["id"] + ".json")
        initial = record_path.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "judge.log").write_text("synthetic complete Judge output")
            first = self.archive.attach_evidence(result["id"], directory, "full Judge output")
            (Path(directory) / "judge.log").write_text("synthetic follow-up diagnostic")
            second = self.archive.attach_evidence(result["id"], directory, "follow-up diagnostic")
        self.assertEqual(record_path.read_bytes(), initial)
        checked = Archive(self.archive.root).verify_evidence(result["id"])
        self.assertTrue(checked["verified"])
        self.assertEqual({item["id"] for item in checked["attachments"]}, {first["id"], second["id"]})
        self.assertTrue(all(item["verified"] for item in checked["attachments"]))
        self.assertTrue(all((self.archive.root / item["manifest_path"]).is_file()
                            for item in checked["attachments"]))
        (self.archive.root / first["evidence_path"] / "judge.log").write_text("tampered later")
        with self.assertRaisesRegex(ArchiveError, "changed"):
            self.archive.verify_evidence(result["id"])

    def test_legacy_evidence_is_never_reported_as_verified(self):
        candidate = self.capture()
        result = self.record(candidate, "legacy-evidence", 1)
        legacy = {key: value for key, value in result.items()
                  if key not in ("evidence_files", "evidence_digest")}
        path = self.archive.root / "evaluations" / (result["id"] + ".json")
        atomic_json(path, legacy)  # Deliberately reproduce a v0.1 record schema.
        checked = self.archive.verify_evidence(result["id"])
        self.assertFalse(checked["verified"])
        self.assertEqual(checked["status"], "legacy_unverified")
        self.archive.attach_evidence(result["id"], self.archive.root / result["evidence_path"],
                                     "newly sealed copy of legacy evidence")
        self.assertFalse(self.archive.verify_evidence(result["id"])["verified"])

    def test_evidence_symlink_is_rejected_without_publishing_an_evaluation(self):
        candidate = self.capture()
        evidence = self.root / "evidence-symlink"
        evidence.symlink_to(self.work, target_is_directory=True)
        with self.assertRaisesRegex(ArchiveError, "symlink"):
            self.archive.record_evaluation(candidate["id"], {
                "source_digest": candidate["source_digest"], "context_fingerprint": candidate["context_fingerprint"],
                "eval_run_id": "symlink-evidence", "status": "completed", "valid": True,
                "metrics": {"speed": 1}}, evidence=evidence)
        self.assertEqual(self.archive.evaluations(), [])

    def test_tampered_evidence_prevents_promotion(self):
        candidate = self.capture()
        search = self.record(candidate, "search-with-evidence", 2)
        confirmation = self.record(candidate, "confirmed-with-evidence", 2, kind="confirmation")
        evidence = self.archive.root / confirmation["evidence_path"] / "measurement.json"
        original_digest = file_sha(evidence)
        evidence.write_text("changed")
        self.assertNotEqual(file_sha(evidence), original_digest)
        with self.assertRaisesRegex(ArchiveError, "changed"):
            self.archive.promote(candidate["id"], search["id"], confirmation["id"])
        self.assertFalse((self.archive.root / "CURRENT.json").exists())


if __name__ == "__main__":
    unittest.main()
