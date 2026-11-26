"""State invariants; synthetic measurements test selection, not performance."""

from pathlib import Path
import tempfile
import unittest

from rsiforme.archive import Archive, ArchiveError


class ArchiveTests(unittest.TestCase):
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
        return self.archive.record_evaluation(candidate["id"], {
            "source_digest": candidate["source_digest"],
            "context_fingerprint": candidate["context_fingerprint"],
            "eval_run_id": name, "status": "completed", "valid": valid,
            "metrics": {"speed": speed, "quality": 10 - speed}, **kwargs,
        }, kind=kind)

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
        confirmation = self.record(candidate, "old-confirmation", 2, kind="confirmation")
        search = self.record(candidate, "later-search", 2)
        with self.assertRaisesRegex(ArchiveError, "must follow"):
            self.archive.promote(candidate["id"], search["id"], confirmation["id"])

    def test_evidence_is_copied_before_external_directory_disappears(self):
        candidate = self.capture()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / "raw.json").write_text('{"measured": true}')
            result = {"status": "completed", "valid": True, "metrics": {"speed": 2},
                      "eval_run_id": "evidence-test", "source_digest": candidate["source_digest"],
                      "context_fingerprint": candidate["context_fingerprint"]}
            record = self.archive.record_evaluation(candidate["id"], result, evidence=evidence)
        self.assertTrue((self.archive.root / record["evidence_path"] / "raw.json").is_file())


if __name__ == "__main__":
    unittest.main()
