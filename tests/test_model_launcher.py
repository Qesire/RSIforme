"""Launcher stop conditions with mocks; no Codex, worker or evaluator is run."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "model_research_launcher", ROOT / "examples/jsonl_aggregate/run_model_research.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class ModelLauncherTests(unittest.TestCase):
    def setUp(self):
        # A regression must fail locally instead of accidentally starting a service.
        process_guard = mock.patch.object(
            launcher.subprocess, "run", side_effect=AssertionError("Unexpected subprocess")
        )
        self.subprocess_run = process_guard.start()
        self.addCleanup(process_guard.stop)
        worker_guard = mock.patch.object(
            launcher.subprocess, "Popen", side_effect=AssertionError("Unexpected worker process")
        )
        worker_guard.start()
        self.addCleanup(worker_guard.stop)

    def controller(self, outcomes, **budget_overrides):
        budget = {
            "max_trials": 2,
            "max_evaluations": 4,
            "walltime_seconds": 900,
            "worker_timeout_seconds": 180,
            "remaining_wall_seconds": 100,
            "evaluations_consumed": 1,
        }
        budget.update(budget_overrides)
        controller = mock.Mock(
            spec=["config", "status", "prepare", "run", "trials", "finalize"]
        )
        controller.config = {"id": "mock-research", "program": {"budgets": copy.deepcopy(budget)}}
        controller.status.return_value = {"budget": budget}
        history = []

        def advance(*, max_new_trials):
            self.assertEqual(max_new_trials, 0)
            history.append(copy.deepcopy(outcomes[len(history)]))

        controller.run.side_effect = advance
        controller.trials.side_effect = lambda: copy.deepcopy(history)
        return controller

    def test_readonly_mount_blocks_even_when_access_reports_writable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installation_id"
            for existing_id in (False, True):
                with self.subTest(existing_id=existing_id):
                    if existing_id:
                        target.write_text("mock-installation-id\n")
                    with (
                        mock.patch.object(
                            launcher.os, "statvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)
                        ),
                        mock.patch.object(launcher.os, "access", return_value=True),
                        mock.patch.object(Path, "read_text", side_effect=AssertionError("Identity read")),
                    ):
                        result = launcher.inspect_state_directory(root)
                    self.assertFalse(result["ready"])
                    self.assertTrue(result["filesystem_readonly"])
                    self.assertTrue(result["required_path_writable"])
                    self.assertEqual(result["installation_id_exists"], existing_id)
                    self.assertEqual(result["reason"], "codex_state_write_unavailable")
                    self.assertEqual(target.exists(), existing_id)

    def test_main_blocked_preflight_never_initializes_or_runs_a_campaign(self):
        blocked = {"ready": False, "reason": "codex_state_write_unavailable"}
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary) / "must-not-exist"
            for argv in (["--store", str(store)], ["--preflight-only"]):
                with self.subTest(argv=argv):
                    with (
                        mock.patch.object(launcher, "codex_preflight", return_value=blocked) as preflight,
                        mock.patch.object(launcher, "run_campaign") as campaign,
                        mock.patch.object(launcher, "Archive") as archive,
                        mock.patch.object(launcher, "evaluate_local") as evaluate,
                        mock.patch.object(launcher, "ResearchController") as controller,
                        contextlib.redirect_stdout(io.StringIO()) as output,
                    ):
                        self.assertEqual(launcher.main(argv), 2)
                        self.assertEqual(json.loads(output.getvalue()), blocked)
                        preflight.assert_called_once_with()
                        campaign.assert_not_called()
                        archive.assert_not_called()
                        evaluate.assert_not_called()
                        controller.assert_not_called()
                        self.assertFalse(store.exists())
        self.subprocess_run.assert_not_called()

    def test_direct_campaign_call_requires_a_passing_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Path(temporary) / "must-not-exist"
            for preflight in ({"ready": False}, {}, {"ready": "true"}):
                with self.subTest(preflight=preflight):
                    with (
                        mock.patch.object(launcher, "Archive") as archive,
                        mock.patch.object(launcher, "evaluate_local") as evaluate,
                        mock.patch.object(launcher, "ResearchController") as controller,
                    ):
                        with self.assertRaises(ValueError):
                            launcher.run_campaign(store, preflight)
                        archive.assert_not_called()
                        evaluate.assert_not_called()
                        controller.assert_not_called()
                        self.assertFalse(store.exists())
        self.subprocess_run.assert_not_called()

    def test_failed_worker_stops_without_a_second_trial_or_finalization(self):
        controller = self.controller([
            {"id": "trial-0001", "phase": "worker_failed", "worker_returncode": 1}
        ])
        original_config = copy.deepcopy(controller.config)
        original_budget = copy.deepcopy(controller.status.return_value["budget"])
        self.assertEqual(launcher.drive_trials(controller), "worker_failed")
        controller.prepare.assert_called_once_with()
        controller.run.assert_called_once_with(max_new_trials=0)
        controller.finalize.assert_not_called()
        self.assertEqual(controller.config, original_config)
        self.assertEqual(controller.status.return_value["budget"], original_budget)

    def test_exhausted_budget_stops_before_preparing_a_trial(self):
        for boundary in ({"remaining_wall_seconds": 0}, {"evaluations_consumed": 3}):
            with self.subTest(boundary=boundary):
                controller = self.controller([], **boundary)
                original_budget = copy.deepcopy(controller.status.return_value["budget"])
                self.assertEqual(launcher.drive_trials(controller), "budget_exhausted")
                controller.prepare.assert_not_called()
                controller.run.assert_not_called()
                controller.finalize.assert_not_called()
                self.assertEqual(controller.status.return_value["budget"], original_budget)

    def test_two_zero_exit_proposals_do_not_replace_first_round_evaluation(self):
        controller = self.controller([
            {"id": "trial-0001", "phase": "completed", "worker_returncode": 0,
             "proposal": {"decision": "evaluate"}, "decision": "unchanged_source"},
            {"id": "trial-0002", "phase": "stopped", "worker_returncode": 0,
             "proposal": {"decision": "stop"}, "decision": "worker_stopped"},
        ])
        self.assertEqual(launcher.drive_trials(controller), "feedback_evidence_incomplete")
        self.assertEqual(controller.run.call_count, 2)
        controller.finalize.assert_not_called()

    def test_completed_summary_keeps_discovery_and_reference_gate_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = mock.Mock(spec=["root", "evaluations"])
            archive.root = Path(temporary)
            archive.evaluations.return_value = []
            controller = self.controller([])
            controller.trials.side_effect = None
            controller.trials.return_value = [
                {"phase": "completed", "worker_returncode": 0, "proposal": {"decision": "evaluate"},
                 "worker_started_at": "2026-01-01T00:00:00+00:00"}
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                summary = launcher.campaign_summary(
                    archive, controller, "completed_pending_evidence_review"
                )
            self.assertEqual(summary["accepted_proposals"], 1)
            self.assertFalse(summary["autonomous_discovery_verified"])
            self.assertEqual(summary["reference_gate_g3"], "open_pending_evidence_review")
            self.assertIsNone(summary["model_request_count"])
            self.assertEqual(
                json.loads((archive.root / "model-research-result.json").read_text()), summary
            )

    def test_capture_failure_does_not_count_a_zero_exit_proposal_as_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = mock.Mock(spec=["root", "evaluations"])
            archive.root = Path(temporary)
            archive.evaluations.return_value = []
            controller = self.controller([])
            controller.trials.side_effect = None
            controller.trials.return_value = [
                {"phase": "worker_failed", "worker_returncode": 0,
                 "proposal": {"decision": "evaluate"},
                 "worker_error": "Candidate capture failed: missing declared source",
                 "worker_started_at": "2026-01-01T00:00:00+00:00"}
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                summary = launcher.campaign_summary(archive, controller, "worker_failed")
            self.assertEqual(summary["worker_launches"], 1)
            self.assertEqual(summary["accepted_proposals"], 0)
            self.assertFalse(summary["autonomous_discovery_verified"])


if __name__ == "__main__":
    unittest.main()
