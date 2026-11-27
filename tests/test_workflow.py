"""Workflow contracts plus one real local evaluator run.

Native submit responses below are explicitly synthetic protocol fixtures. Mocked
client calls test binding and recovery; they are not evidence of a Harness run.
"""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rsiforme.archive import Archive, ArchiveError, file_sha
from rsiforme.workflow import evaluate_local, import_harness, submit


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "jsonl_aggregate"


def native_response(round_id="agent-1", score=1.25, quality=1):
    """One complete synthetic response in the pinned native client's format."""
    return (
        f"Full verifier output: /run/rsi-harness/feedback/{round_id}.log\n"
        "--- rsi-harness submission ---\n"
        f"round: {round_id}\n"
        "status: completed\n"
        f"reward: {json.dumps({'reward': score, 'quality_passed': quality})}\n"
        f"score: {score}\n"
        "exit_code: 0\n"
        "timed_out: false\n"
        "duration_seconds: 0.1\n"
        "remaining: 3\n"
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "work"
        self.workspace.mkdir()
        shutil.copyfile(EXAMPLE / "fixtures" / "optimized" / "solution.py", self.workspace / "solution.py")
        self.task = {
            "schema_version": 1, "task_id": "synthetic-harness-contract",
            "objective": "reward", "direction": "maximize", "source_paths": ["solution.py"],
            "context": {"task_revision": "synthetic-fixture-v1",
                        "evaluator_revision": "synthetic-native-output",
                        "environment": "unit-test mocks; no Harness runtime"},
            "harness": {"task_id": "jsonl-aggregate", "gates": [{"metric": "quality_passed", "equals": 1}]},
        }
        self.archive = Archive(self.root / "archive")
        self.archive.initialize(self.task)

    def capture(self):
        return self.archive.capture(self.workspace, lane="test", hypothesis="Synthetic test candidate")

    def call_submit(self, response, **kwargs):
        process = subprocess.CompletedProcess(["rsi-submit"], 0, response, "")
        with patch("rsiforme.workflow.shutil.which", return_value="/synthetic/bin/rsi-submit"), \
             patch("rsiforme.workflow.subprocess.run", return_value=process) as called:
            result = submit(self.archive, self.workspace, harness_run_id="synthetic-run", **kwargs)
            called.assert_called_once_with(["rsi-submit"], cwd=self.workspace,
                                           text=True, capture_output=True, check=False)
            return result

    def test_completed_zero_reward_and_failed_quality_are_not_eligible(self):
        result = self.call_submit(native_response(score=0, quality=0),
                                  hypothesis="Check that completed does not bypass quality")
        evaluation = result["evaluation"]
        self.assertEqual(evaluation["status"], "completed")
        self.assertFalse(evaluation["valid"])
        self.assertEqual(evaluation["metrics"], {"reward": 0, "quality_passed": 0})
        self.assertIn("gate failed", evaluation["acceptance_failures"][0])
        self.assertIsNone(self.archive.best())
        self.assertEqual(result["submission"]["status"], "bound")

    def test_missing_task_gate_is_rejected_before_external_call(self):
        del self.task["harness"]
        archive = Archive(self.root / "no-gate")
        archive.initialize(self.task)
        with patch("rsiforme.workflow.subprocess.run") as called:
            with self.assertRaisesRegex(ArchiveError, "explicit.*gates"):
                submit(archive, self.workspace, harness_run_id="synthetic-run",
                       hypothesis="No gate configured")
            called.assert_not_called()
        self.assertEqual(archive.candidates(), [])

    def test_missing_native_executable_does_not_capture_or_send(self):
        with patch("rsiforme.workflow.shutil.which", return_value=None), \
             patch("rsiforme.workflow.subprocess.run") as called:
            with self.assertRaisesRegex(ArchiveError, "unavailable"):
                submit(self.archive, self.workspace, harness_run_id="synthetic-run",
                       hypothesis="Do not submit outside Work")
            called.assert_not_called()
        self.assertEqual(self.archive.candidates(), [])

    def test_unknown_response_preserves_pending_and_blocks_duplicate(self):
        process = subprocess.CompletedProcess(["rsi-submit"], 1, "response lost after transport interruption", "")
        with patch("rsiforme.workflow.shutil.which", return_value="/synthetic/bin/rsi-submit"), \
             patch("rsiforme.workflow.subprocess.run", return_value=process) as called:
            with self.assertRaisesRegex(ArchiveError, "outcome is unknown"):
                submit(self.archive, self.workspace, harness_run_id="synthetic-run",
                       hypothesis="One uncertain submission")
            pending = self.archive.summary()["pending_submissions"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(self.archive.evaluations(), [])
            self.assertTrue((self.archive.root / "evidence" / (pending[0]["id"] + "-attempt.txt")).is_file())
            with self.assertRaisesRegex(ArchiveError, "unresolved submission"):
                submit(self.archive, self.workspace, harness_run_id="synthetic-run",
                       candidate_id=pending[0]["candidate_id"])
            self.assertEqual(called.call_count, 1)
        self.assertEqual(len(Archive(self.archive.root).summary()["pending_submissions"]), 1)

    def test_native_client_timeout_preserves_pending_and_never_dispatches_a_duplicate(self):
        timeout = subprocess.TimeoutExpired(["rsi-submit"], 0.25,
                                            output=b"synthetic partial client response\n",
                                            stderr=b"synthetic remote status is still unknown\n")
        with patch("rsiforme.workflow.shutil.which", return_value="/synthetic/bin/rsi-submit"), \
             patch("rsiforme.workflow.subprocess.run", side_effect=timeout) as called:
            with self.assertRaisesRegex(ArchiveError, "outcome is unknown"):
                submit(self.archive, self.workspace, harness_run_id="synthetic-run",
                       hypothesis="Exercise an uncertain native client timeout", timeout_seconds=0.25)
            self.assertEqual(called.call_args.kwargs["timeout"], 0.25)
            reopened = Archive(self.archive.root)
            pending = reopened.summary()["pending_submissions"]
            self.assertEqual(len(pending), 1)
            self.assertIsNone(pending[0]["round_id"])
            self.assertEqual(reopened.evaluations(), [])
            transcript = (reopened.root / "evidence" / (pending[0]["id"] + "-attempt.txt")).read_text()
            self.assertIn("synthetic partial client response", transcript)
            self.assertIn("remote Judge may still be running", transcript)
            with self.assertRaisesRegex(ArchiveError, "unresolved submission"):
                submit(reopened, self.workspace, harness_run_id="synthetic-run",
                       candidate_id=pending[0]["candidate_id"], timeout_seconds=0.25)
            self.assertEqual(called.call_count, 1)
            self.assertEqual(len(reopened.summary()["pending_submissions"]), 1)

    def test_confirmation_binds_a_distinct_round_to_identical_source(self):
        search = self.call_submit(native_response("agent-1", 1.25), hypothesis="Search candidate")
        candidate = search["evaluation"]["candidate_id"]
        confirmation = self.call_submit(native_response("agent-2", 1.2),
                                        candidate_id=candidate, kind="confirmation")
        self.assertEqual(confirmation["evaluation"]["candidate_id"], candidate)
        self.assertEqual(confirmation["evaluation"]["kind"], "confirmation")
        self.assertNotEqual(search["evaluation"]["id"], confirmation["evaluation"]["id"])
        self.assertEqual(search["evaluation"]["source_digest"], confirmation["evaluation"]["source_digest"])
        self.assertEqual(confirmation["submission"]["round_id"], "agent-2")
        self.assertEqual(len(self.archive.candidates()), 1)
        self.assertEqual(len(self.archive.evaluations()), 2)

    def test_confirmation_rejects_different_live_source_before_submit(self):
        candidate = self.capture()
        (self.workspace / "solution.py").write_text("def aggregate(records): return []\n", encoding="utf-8")
        with patch("rsiforme.workflow.shutil.which", return_value="/synthetic/bin/rsi-submit"), \
             patch("rsiforme.workflow.subprocess.run") as called:
            with self.assertRaisesRegex(ArchiveError, "differs"):
                submit(self.archive, self.workspace, harness_run_id="synthetic-run",
                       candidate_id=candidate["id"], kind="confirmation")
            called.assert_not_called()
        self.assertEqual(self.archive.summary()["pending_submissions"], [])

    def test_promotion_rejects_confirmed_regression_against_current_release(self):
        first = self.call_submit(native_response("agent-1", 1.2), hypothesis="First release")
        candidate = first["evaluation"]["candidate_id"]
        first_confirmation = self.call_submit(native_response("agent-2", 1.15),
                                              candidate_id=candidate, kind="confirmation")
        release = self.archive.promote(candidate, first["evaluation"]["id"], first_confirmation["evaluation"]["id"])
        second = self.call_submit(native_response("agent-3", 1.5),
                                  candidate_id=candidate)
        regressed = self.call_submit(native_response("agent-4", 1.1),
                                     candidate_id=candidate, kind="confirmation")
        with self.assertRaisesRegex(ArchiveError, "does not improve"):
            self.archive.promote(candidate, second["evaluation"]["id"], regressed["evaluation"]["id"])
        self.assertEqual(self.archive.summary()["current"]["release_id"], release["id"])

    def test_local_wrapper_really_executes_and_preserves_evidence(self):
        evaluator = EXAMPLE / "evaluate.py"
        local_task = {
            **self.task, "task_id": "actual-local-cpu-test", "objective": "rows_per_second",
            "context": {"task_revision": "jsonl-aggregate-v1",
                        "evaluator_revision": "sha256:" + file_sha(evaluator),
                        "environment": "current local Python/CPU; smoke integration only"},
        }
        archive = Archive(self.root / "local-archive")
        archive.initialize(local_task)
        candidate = archive.capture(self.workspace, lane="cpu", hypothesis="Execute the trusted local contract")
        result = evaluate_local(archive, candidate["id"], evaluator)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["valid"])
        self.assertGreater(result["metrics"]["rows_per_second"], 0)
        self.assertEqual(result["source_digest"], candidate["source_digest"])
        self.assertEqual(result["context_fingerprint"], candidate["context_fingerprint"])
        evidence = archive.root / result["evidence_path"]
        self.assertTrue((evidence / "report.json").is_file())
        self.assertTrue((evidence / "rsiforme-binding.json").is_file())
        self.assertEqual(json.loads((evidence / "report.json").read_text())["eval_run_id"], result["eval_run_id"])

    def native_artifacts(self, *, score=1.25, run_id="synthetic-run", task_id="jsonl-aggregate"):
        """Synthetic durable files moved away from their original path names."""
        destination = self.root / "copied-evidence" / "arbitrary-directory-name"
        report = {
            "round": "agent-1", "status": "completed", "submitted_at": 1234.5,
            "score": score, "metrics": {"reward": 1.25, "quality_passed": 1},
            "exit_code": 0, "timed_out": False, "runtime_seconds": 0.1,
            "verifier_output_required": True, "full_output_captured": True,
            "error": None,
        }
        files = {
            "run-plan.json": {"paths": {"root": f"/original/runtime/{run_id}"},
                              "task": {"task_id": task_id}},
            "submissions/agent-1/report.json": report,
            "evolve_state.json": {"best_round": "agent-1", "best_score": score,
                                  "submissions": [{"kind": "agent", "round": "agent-1", "at": 1234.5,
                                                   "status": "completed", "score": score,
                                                   "rewards": report["metrics"]}]},
            "final_result.json": {"best_round": "agent-1", "best_score": score,
                                  "best_rewards": {"reward": 99, "quality_passed": 1},
                                  "status": "completed", "total_rounds": 1},
        }
        for relative, contents in files.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(contents), encoding="utf-8")
        (destination / "submissions/agent-1/test_output.txt").write_text("Synthetic verifier evidence.\n", encoding="utf-8")
        return destination

    def bind_native_fixture(self):
        candidate = self.capture()
        intent = self.archive.begin_submission(candidate["id"], "synthetic-run")
        self.archive.bind_submission(intent["id"], "agent-1", note="Known synthetic fixture round")
        return candidate

    def test_import_accepts_moved_directory_using_recorded_run_identity(self):
        candidate = self.bind_native_fixture()
        destination = self.native_artifacts()
        result = import_harness(self.archive, destination, "synthetic-run")
        self.assertFalse(result["already_recorded"])
        self.assertEqual(result["evaluation"]["candidate_id"], candidate["id"])
        self.assertEqual(result["evaluation"]["metrics"]["reward"], 1.25)
        self.assertEqual(result["evaluation"]["native_score"], 1.25)
        self.assertTrue(result["evaluation"]["valid"])
        evidence = self.archive.root / result["evaluation"]["evidence_path"]
        self.assertEqual((evidence / "test_output.txt").read_text(), "Synthetic verifier evidence.\n")
        repeated = import_harness(self.archive, destination, "synthetic-run")
        self.assertTrue(repeated["already_recorded"])
        self.assertEqual(repeated["evaluation"]["id"], result["evaluation"]["id"])
        self.assertEqual(len(self.archive.evaluations()), 1)

    def test_import_rejects_wrong_run_or_task_identity_before_recording(self):
        self.bind_native_fixture()
        destination = self.native_artifacts(run_id="different-native-run")
        with self.assertRaisesRegex(ArchiveError, "run ID disagrees"):
            import_harness(self.archive, destination, "synthetic-run")
        destination = self.native_artifacts(task_id="different-native-task")
        with self.assertRaisesRegex(ArchiveError, "task ID disagrees"):
            import_harness(self.archive, destination, "synthetic-run")
        self.assertEqual(self.archive.evaluations(), [])

    def test_repeated_import_rejects_changed_native_score_even_with_same_metrics(self):
        self.bind_native_fixture()
        destination = self.native_artifacts()
        original = import_harness(self.archive, destination, "synthetic-run")
        self.native_artifacts(score=2.0)  # State/summary agree with each other; saved result differs.
        with self.assertRaisesRegex(ArchiveError, "already imported response"):
            import_harness(self.archive, destination, "synthetic-run")
        self.assertEqual(self.archive.evaluation(original["evaluation"]["id"])["native_score"], 1.25)
        self.assertEqual(len(self.archive.evaluations()), 1)


if __name__ == "__main__":
    unittest.main()
