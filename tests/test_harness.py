"""Synthetic contract fixtures shaped like the pinned upstream artifacts.

These tests exercise our adapter. They are not evidence of a Harness/GPU run.
"""

import json
import tempfile
import unittest
from pathlib import Path

from rsiforme.harness import (
    HarnessContractError,
    construct_harness_command,
    parse_submit_output,
    read_best_report,
    read_round_report,
    read_run_identity,
)


FEEDBACK = """Full verifier output: /run/rsi-harness/feedback/agent-2.log
--- rsi-harness submission ---
round: agent-2
status: completed
reward: {"latency": 3.0, "reward": 1.5}
score: 1.5
exit_code: 0
timed_out: false
duration_seconds: 4.2
remaining: 2
"""


class SubmissionParsingTests(unittest.TestCase):
    def test_native_feedback_keeps_round_and_its_metrics_together(self):
        parsed = parse_submit_output(FEEDBACK)
        self.assertEqual(parsed["round_id"], "agent-2")
        self.assertEqual(parsed["score"], 1.5)
        self.assertEqual(parsed["metrics"], {"reward": 1.5, "latency": 3.0})
        self.assertEqual(parsed["remaining"], 2)
        self.assertNotIn("harness_run_id", parsed)

    def test_infrastructure_failure_does_not_become_zero_score(self):
        text = FEEDBACK.replace("completed", "infrastructure_error")
        text = text.replace('reward: {"latency": 3.0, "reward": 1.5}', "reward: null")
        text = text.replace("score: 1.5\n", "").replace("exit_code: 0", "exit_code: null")
        result = parse_submit_output(text)
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "infrastructure_error")

    def test_history_truncation_and_duplicate_fields_are_not_bindable(self):
        for text in (FEEDBACK + FEEDBACK, "round: agent-2\n", FEEDBACK.split("timed_out")[0],
                     FEEDBACK + "round: agent-3\n"):
            with self.subTest(text=text), self.assertRaises(HarnessContractError):
                parse_submit_output(text)

    def test_nonfinite_values_and_cross_round_log_paths_are_rejected(self):
        cases = (
            FEEDBACK.replace("score: 1.5", "score: NaN"),
            FEEDBACK.replace('"latency": 3.0', '"latency": Infinity'),
            FEEDBACK.replace("feedback/agent-2.log", "feedback/agent-1.log"),
            FEEDBACK.replace("round: agent-2", "round: ../../secret"),
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(HarnessContractError):
                parse_submit_output(text)

    def test_final_judge_footer_without_log_or_remaining(self):
        text = "\n".join(FEEDBACK.splitlines()[1:-1])
        text += "\nThis was the run's one final judge; the run ends now.\n"
        parsed = parse_submit_output(text)
        self.assertEqual(parsed["round_id"], "agent-2")
        self.assertNotIn("feedback_path", parsed)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = {
            "round": "agent-2", "status": "completed", "submitted_at": 1234.5,
            "score": 1.5, "metrics": {"reward": 1.5, "quality": 0.92},
            "exit_code": 0, "timed_out": False,
            "verifier_output_required": True, "full_output_captured": True,
            "runtime_seconds": 4.2, "error": None,
        }
        self.state = {"best_round": "agent-2", "best_score": 1.5, "submissions": [
            {"kind": "agent", "round": "agent-2", "at": 1234.5,
             "status": "completed", "score": 1.5, "rewards": self.report["metrics"]},
        ]}
        self.final = {"best_round": "agent-2", "best_score": 1.5,
                      "best_rewards": {"reward": 1.5, "quality": 1.0},
                      "status": "completed", "total_rounds": 2}
        self.write("submissions/agent-2/report.json", self.report)
        self.write("evolve_state.json", self.state)
        self.write("final_result.json", self.final)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_composite_best_rewards_never_leaks_into_selected_candidate(self):
        selected = read_best_report(self.root)
        self.assertEqual(selected["metrics"]["quality"], 0.92)
        self.assertNotIn("best_rewards", selected)
        self.assertEqual(selected["round_id"], "agent-2")

    def test_report_not_committed_in_state_is_not_imported(self):
        self.state["submissions"] = []
        self.write("evolve_state.json", self.state)
        with self.assertRaisesRegex(HarnessContractError, "not uniquely committed"):
            read_best_report(self.root)

    def test_stale_summary_and_mismatched_report_are_rejected(self):
        self.final["best_score"] = 99
        self.write("final_result.json", self.final)
        with self.assertRaisesRegex(HarnessContractError, "score disagrees"):
            read_best_report(self.root)
        self.final["best_score"] = 1.5
        self.write("final_result.json", self.final)
        self.report["round"] = "agent-1"
        self.write("submissions/agent-2/report.json", self.report)
        with self.assertRaisesRegex(HarnessContractError, "round disagrees"):
            read_best_report(self.root)

    def test_no_best_and_path_traversal_are_rejected(self):
        for value in (None, "../agent-2", "/tmp/report", ".."):
            with self.subTest(value=value):
                self.final["best_round"] = value
                self.write("final_result.json", self.final)
                with self.assertRaises(HarnessContractError):
                    read_best_report(self.root)

    def test_symlink_cannot_redirect_to_another_run(self):
        with tempfile.TemporaryDirectory() as outside:
            path = self.root / "submissions/agent-2/report.json"
            path.unlink()
            external = Path(outside) / "report.json"
            external.write_text(json.dumps(self.report), encoding="utf-8")
            path.symlink_to(external)
            with self.assertRaisesRegex(HarnessContractError, "outside"):
                read_best_report(self.root)

    def test_failed_round_is_importable_without_becoming_a_best(self):
        self.report.update(status="infrastructure_error", score=None, metrics={})
        self.state["submissions"][0].update(status="infrastructure_error", score=None, rewards={})
        self.write("submissions/agent-2/report.json", self.report)
        self.write("evolve_state.json", self.state)
        result = read_round_report(self.root, "agent-2")
        self.assertIsNone(result["score"])
        with self.assertRaisesRegex(HarnessContractError, "not completed"):
            read_best_report(self.root)

    def test_run_identity_survives_copy_of_log_directory(self):
        self.write("run-plan.json", {"paths": {"root": "/original/runtime/native-run-17"},
                                     "task": {"task_id": "original-task"}})
        identity = read_run_identity(self.root)
        self.assertEqual(identity["harness_run_id"], "native-run-17")
        self.assertEqual(identity["task_id"], "original-task")
        self.write("run-plan.json", {"paths": {"root": "relative/ambiguous"},
                                     "task": {"task_id": "original-task"}})
        with self.assertRaises(HarnessContractError):
            read_run_identity(self.root)


class CommandTests(unittest.TestCase):
    def test_cluster_dry_run_and_local_resume_have_native_argv(self):
        command = construct_harness_command("task", cluster="site.toml")
        self.assertEqual(command, ["rsi-harness", "run", str(Path("task").resolve()),
                                   "--agent", "oracle", "--cluster", "site.toml", "--dry-run"])
        self.assertEqual(construct_harness_command(None, run_id="run-1"),
                         ["rsi-harness", "resume", "run-1", "--dry-run"])

    def test_cannot_construct_unsupported_local_new_run_dry_run(self):
        with self.assertRaisesRegex(HarnessContractError, "requires --cluster"):
            construct_harness_command("task")
        with self.assertRaisesRegex(HarnessContractError, "recorded task"):
            construct_harness_command("task", run_id="run-1")


if __name__ == "__main__":
    unittest.main()
