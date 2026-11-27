"""Real controller/worker processes with explicit synthetic scores, not model discovery."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from rsiforme.archive import Archive, ArchiveError, atomic_json, file_sha, load_json, now
from rsiforme.delivery import export_release, verify_release
from rsiforme.research import ResearchController
from rsiforme.workflow import evaluate_local


EVALUATOR = '''import json
from pathlib import Path
import subprocess
import sys
import uuid

COUNTER = Path(@COUNTER@)

def evaluate_candidate(snapshot_dir, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution = "scripted-evaluator-" + uuid.uuid4().hex
    with COUNTER.open("a") as stream:
        stream.write(json.dumps({"id": execution, "source": str(snapshot_dir), "synthetic": True}) + "\\n")
    process = subprocess.run([sys.executable, "-I", "-B", str(Path(snapshot_dir) / "solution.py")],
                             capture_output=True, text=True, timeout=3, check=False)
    (output_dir / "stdout.txt").write_text(process.stdout)
    (output_dir / "stderr.txt").write_text(process.stderr)
    payload = json.loads(process.stdout) if process.returncode == 0 else {}
    score = payload.get("score", 0)
    valid = process.returncode == 0 and payload.get("answer") == sum(range(8)) and type(score) is int
    report = {"status": "completed" if process.returncode == 0 else "failed", "valid": valid,
              "metrics": {"score": score}, "eval_run_id": execution,
              "synthetic_measurement": True, "quality_rule": "answer == sum(range(8))"}
    (output_dir / "report.json").write_text(json.dumps(report))
    return report
'''


WORKER = '''"""A scripted protocol fixture; its score changes are not research discoveries."""
import argparse
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", required=True)
parser.add_argument("--handoff", required=True)
parser.add_argument("--proposal", required=True)
parser.add_argument("--ledger", required=True)
args = parser.parse_args()
prompt = sys.stdin.read()
handoff = json.loads(Path(args.handoff).read_text())
number = int(handoff["trial_id"].split("-")[-1])
parent = handoff["parent_evaluation"]
observed = {"fixture": "scripted-worker", "trial": handoff["trial_id"], "lane": handoff["lane"]["id"],
            "parent_evaluation": parent["id"], "parent_score": parent["metrics"]["score"],
            "history_evaluations": [row["id"] for row in handoff["history"]["evaluations"]],
            "history_phases": [row["phase"] for row in handoff["history"]["trials"]],
            "budget": handoff["budget"], "received_prompt": bool(prompt)}
with Path(args.ledger).open("a") as stream:
    stream.write(json.dumps(observed) + "\\n")
print(json.dumps(observed), flush=True)
if args.scenario == "worker_failure_first" and number == 1:
    print("Injected scripted worker failure", file=sys.stderr)
    raise SystemExit(7)
if args.scenario == "timeout_first" and number == 1:
    time.sleep(30)
score = parent["metrics"]["score"] + 1
answer = sum(range(8))
if args.scenario == "invalid_second" and number == 2:
    score, answer = 999, -1
source = Path.cwd() / "solution.py"
if args.scenario == "missing_source_first" and number == 1:
    source.unlink()
else:
    source.write_text("import json\\nprint(json.dumps(" + repr({"answer": answer, "score": score}) + "))\\n")
proposal = {"decision": "evaluate", "hypothesis": "Scripted fixture: increment the measured parent score",
            "change_summary": "A synthetic score changes; answer is checked by the fixed evaluator",
            "evidence_used": [parent["id"], "profile: baseline output observed"],
            "next_direction": "Read this actual evaluation before the next scripted change"}
Path(args.proposal).write_text(json.dumps(proposal))
'''


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "baseline-work"
        self.work.mkdir()
        (self.work / "solution.py").write_text('import json\nprint(json.dumps({"answer": 28, "score": 1}))\n')
        self.evaluation_calls = self.root / "evaluation-calls.jsonl"
        self.worker_calls = self.root / "worker-calls.jsonl"
        self.evaluator = self.root / "evaluate.py"
        self.evaluator.write_text(EVALUATOR.replace("@COUNTER@", repr(str(self.evaluation_calls))))
        self.worker = self.root / "scripted-worker.py"
        self.worker.write_text(WORKER)
        task = {"schema_version": 1, "task_id": "scripted-research-integration",
                "objective": "score", "direction": "maximize", "source_paths": ["solution.py"],
                "evaluation_timeout_seconds": 5,
                "context": {"task_revision": "synthetic-controller-fixture-v1",
                            "evaluator_revision": "sha256:" + file_sha(self.evaluator),
                            "environment": "Local subprocess integration; score is synthetic, not measured speed"}}
        self.archive = Archive(self.root / "archive")
        self.archive.initialize(task)
        self.baseline_candidate = self.archive.capture(self.work, lane="baseline",
                                                       hypothesis="Establish a real subprocess baseline for a scripted test")
        self.baseline = evaluate_local(self.archive, self.baseline_candidate["id"], self.evaluator)
        self.assertTrue(self.baseline["valid"], self.baseline)
        profile_command = [sys.executable, "-I", "-B", str(self.work / "solution.py")]
        observed = subprocess.run(profile_command, capture_output=True, text=True, check=True)
        self.profile = self.root / "baseline-profile.json"
        atomic_json(self.profile, {"source_digest": self.baseline_candidate["source_digest"],
                                  "context_fingerprint": self.baseline_candidate["context_fingerprint"],
                                  "command": profile_command, "observed_at": now(),
                                  "findings": [{"synthetic_fixture_observation": json.loads(observed.stdout)}],
                                  "stdout": observed.stdout})
        self.controller = ResearchController(self.archive)

    def initialize(self, scenario="improve", *, max_trials=2, max_evaluations=4, worker_timeout=3):
        program = {"schema_version": 1, "backend": {"type": "local"},
                   "budgets": {"max_trials": max_trials, "max_evaluations": max_evaluations,
                               "walltime_seconds": 120, "worker_timeout_seconds": worker_timeout},
                   "worker": {"kind": "scripted-test-fixture", "argv": [sys.executable, "-I", "-B", str(self.worker),
                               "--scenario", scenario, "--handoff", "{handoff}", "--proposal", "{proposal}",
                               "--ledger", str(self.worker_calls)]},
                   "lanes": [{"id": "direction-a", "goal": "Exercise one synthetic score change"},
                             {"id": "direction-b", "goal": "Consume measured history before another fixture change"}],
                   "instructions": "This is a scripted integration test, never label its outputs as model discoveries."}
        return self.controller.initialize(program, baseline_evaluation=self.baseline["id"], profile=self.profile)

    def reopen(self):
        return ResearchController(Archive(self.archive.root))

    @staticmethod
    def lines(path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_second_worker_consumes_first_evaluation_and_budget_reserves_confirmation(self):
        self.initialize(max_trials=9, max_evaluations=4)
        status = self.controller.run()
        first, second = status["trials"]
        self.assertEqual([row["phase"] for row in status["trials"]], ["completed", "completed"])
        self.assertEqual([row["decision"] for row in status["trials"]], ["improved", "improved"])
        observed = self.lines(self.worker_calls)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(row["received_prompt"] for row in observed))
        self.assertEqual(observed[1]["parent_evaluation"], first["evaluation_id"])
        self.assertIn(first["evaluation_id"], observed[1]["history_evaluations"])
        self.assertEqual(observed[1]["parent_score"], 2)
        self.assertEqual(self.archive.evaluation(second["evaluation_id"])["metrics"]["score"], 3)
        self.assertEqual(second["parent_id"], first["candidate_id"])
        self.assertEqual(status["budget"]["evaluations_recorded"], 3)
        self.assertEqual(status["budget"]["confirmation_reserved"], 1)
        self.assertEqual(status["pending_trials"], [])
        with self.assertRaisesRegex(ArchiveError, "budget exhausted"):
            self.controller.prepare()
        final = self.controller.finalize()
        self.assertEqual(final["phase"], "promoted")
        confirmation = self.archive.evaluation(final["evaluation_id"])
        self.assertEqual(confirmation["execution"]["confirmation_of"], second["evaluation_id"])
        self.assertEqual(confirmation["kind"], "confirmation")
        self.assertNotEqual(confirmation["id"], second["evaluation_id"])
        self.assertEqual(len(self.lines(self.evaluation_calls)), 4)
        self.assertEqual(self.reopen().finalize(), final)
        exported = export_release(self.archive, self.root / "portable-release")
        self.assertEqual(exported["release_id"], final["release_id"])
        self.assertTrue(verify_release(self.root / "portable-release")["valid"])

    def test_invalid_latest_candidate_cannot_replace_best_or_final_restoration(self):
        self.initialize("invalid_second")
        status = self.controller.run()
        first, second = status["trials"]
        self.assertEqual(second["decision"], "invalid")
        self.assertFalse(self.archive.evaluation(second["evaluation_id"])["valid"])
        self.assertEqual(self.archive.best()["candidate"]["id"], first["candidate_id"])
        final = self.controller.finalize()
        self.assertEqual(final["candidate_id"], first["candidate_id"])
        self.assertTrue(self.archive.matches_workspace(first["candidate_id"], final["workspace"]))
        self.assertFalse(self.archive.matches_workspace(second["candidate_id"], final["workspace"]))

    def assert_failed_worker_continues(self, scenario, error_fragment, worker_timeout=3):
        self.initialize(scenario, worker_timeout=worker_timeout)
        status = self.controller.run()
        first, second = status["trials"]
        self.assertEqual(first["phase"], "worker_failed")
        self.assertIn(error_fragment, first["worker_error"])
        self.assertEqual(second["phase"], "completed")
        self.assertEqual(second["parent_id"], self.baseline_candidate["id"])
        self.assertEqual([row["lane"] for row in status["trials"]], ["direction-a", "direction-b"])
        self.assertEqual(len(self.lines(self.worker_calls)), 2)
        self.assertEqual(len(self.lines(self.evaluation_calls)), 2)  # baseline + second trial
        self.assertEqual(status["budget"]["trials_started"], 2)
        self.assertEqual(status["pending_trials"], [])

    def test_worker_exit_failure_is_recorded_and_next_lane_runs(self):
        self.assert_failed_worker_continues("worker_failure_first", "unsuccessfully")

    def test_worker_timeout_is_bounded_and_next_lane_runs(self):
        self.assert_failed_worker_continues("timeout_first", "timeout", worker_timeout=1)

    def test_unusable_source_is_a_worker_failure_and_next_lane_runs(self):
        self.assert_failed_worker_continues("missing_source_first", "Candidate capture failed")

    def test_prepared_trial_resumes_without_creating_another_trial(self):
        self.initialize()
        prepared = self.controller.prepare()
        self.assertEqual(prepared["phase"], "prepared")
        self.assertEqual(self.lines(self.worker_calls), [])
        status = self.reopen().run(max_new_trials=0)
        self.assertEqual(len(status["trials"]), 1)
        self.assertEqual(status["trials"][0]["id"], prepared["id"])
        self.assertEqual(status["trials"][0]["phase"], "completed")
        self.assertEqual(len(self.lines(self.worker_calls)), 1)

    def test_interrupted_materialization_resumes_from_preparing_intent(self):
        self.initialize()
        restore = self.archive.restore

        def interrupted_restore(*args, **kwargs):
            restore(*args, **kwargs)
            raise RuntimeError("Injected controller interruption after workspace restoration")

        with mock.patch.object(self.archive, "restore", side_effect=interrupted_restore):
            with self.assertRaisesRegex(RuntimeError, "Injected"):
                self.controller.prepare()
        self.assertEqual(self.controller.trials()[0]["phase"], "preparing")
        status = self.reopen().run(max_new_trials=0)
        self.assertEqual(status["trials"][0]["phase"], "completed")
        self.assertEqual(len(self.lines(self.worker_calls)), 1)

    def test_completed_evaluation_reconciles_after_controller_state_commit_failure(self):
        self.initialize()
        with mock.patch.object(self.controller, "_settle", side_effect=RuntimeError("Injected settle interruption")):
            with self.assertRaisesRegex(RuntimeError, "settle interruption"):
                self.controller.run(max_new_trials=1)
        self.assertEqual(self.controller.trials()[0]["phase"], "evaluating")
        recorded = self.archive.evaluations()[-1]
        self.assertEqual(len(self.lines(self.evaluation_calls)), 2)
        status = self.reopen().run(max_new_trials=0)
        self.assertEqual(status["trials"][0]["evaluation_id"], recorded["id"])
        self.assertEqual(len(self.lines(self.evaluation_calls)), 2)
        self.assertEqual(len(self.lines(self.worker_calls)), 1)

    def test_unresolved_worker_blocks_duplicate_until_explicit_reconciliation(self):
        self.initialize()
        save = self.controller._save

        def interrupted_start(trial, phase=None, **updates):
            result = save(trial, phase, **updates)
            if phase == "worker_starting":
                raise RuntimeError("Injected interruption between start intent and process creation")
            return result

        with mock.patch.object(self.controller, "_save", side_effect=interrupted_start):
            with self.assertRaisesRegex(RuntimeError, "start intent"):
                self.controller.run(max_new_trials=1)
        reopened = self.reopen()
        with self.assertRaisesRegex(ArchiveError, "unresolved"):
            reopened.run(max_new_trials=1)
        with self.assertRaisesRegex(ArchiveError, "existing trial"):
            reopened.prepare()
        self.assertEqual(self.lines(self.worker_calls), [])
        first = reopened.trials()[0]
        resolved = reopened.resolve(first["id"], note="Injected start failed before Popen; no process or worker ledger exists")
        self.assertEqual(resolved["phase"], "abandoned")
        status = reopened.run(max_new_trials=1)
        self.assertEqual(status["trials"][1]["phase"], "completed")
        self.assertEqual(len(self.lines(self.worker_calls)), 1)

    def test_explicitly_abandoned_unknown_evaluation_still_consumes_budget(self):
        self.initialize()
        with mock.patch("rsiforme.research.evaluate_local", side_effect=RuntimeError("Injected unknown dispatch outcome")):
            with self.assertRaisesRegex(RuntimeError, "unknown dispatch"):
                self.controller.run(max_new_trials=1)
        reopened = self.reopen()
        trial = reopened.trials()[0]
        with self.assertRaisesRegex(ArchiveError, "unresolved"):
            reopened.resolve(trial["id"], note="No result found")
        resolved = reopened.resolve(trial["id"], abandon_unknown_evaluation=True,
                                    note="Injected local dispatch never launched a process; inspected ledger and no active job")
        self.assertEqual(resolved["phase"], "abandoned")
        self.assertEqual(reopened.status()["budget"]["evaluations_recorded"], 1)
        self.assertEqual(reopened.status()["budget"]["evaluations_consumed"], 2)
        status = reopened.run(max_new_trials=1)
        self.assertEqual(status["trials"][1]["phase"], "completed")
        self.assertEqual(status["budget"]["evaluations_consumed"], 3)
        self.assertEqual(reopened.finalize()["phase"], "promoted")
        self.assertEqual(reopened.status()["budget"]["evaluations_consumed"], 4)
        self.assertEqual(len(self.lines(self.evaluation_calls)), 3)

    def test_confirmation_restore_can_resume_before_dispatch(self):
        self.initialize(max_trials=1, max_evaluations=3)
        self.controller.run()
        restore = self.archive.restore

        def interrupted_restore(*args, **kwargs):
            restore(*args, **kwargs)
            raise RuntimeError("Injected final restoration interruption")

        with mock.patch.object(self.archive, "restore", side_effect=interrupted_restore):
            with self.assertRaisesRegex(RuntimeError, "final restoration"):
                self.controller.finalize()
        state = load_json(self.controller.root / "finalization.json")
        self.assertFalse(state["dispatched"])
        self.assertEqual(len(self.lines(self.evaluation_calls)), 2)
        final = self.reopen().finalize()
        self.assertEqual(final["phase"], "promoted")
        self.assertEqual(len(self.lines(self.evaluation_calls)), 3)

    def test_committed_release_recovers_missing_current_without_duplicate_release_or_evaluation(self):
        self.initialize(max_trials=1, max_evaluations=3)
        self.controller.run()
        promote = self.archive.promote

        def interrupted_after_promotion(*args, **kwargs):
            promote(*args, **kwargs)
            raise RuntimeError("Injected interruption after promotion committed")

        with mock.patch.object(self.archive, "promote", side_effect=interrupted_after_promotion):
            with self.assertRaisesRegex(RuntimeError, "promotion committed"):
                self.controller.finalize()
        current = self.archive.summary()["current"]
        self.assertEqual(len(self.lines(self.evaluation_calls)), 3)
        # Reproduce the durable state of interruption between release creation
        # and CURRENT replacement, while finalization still awaits its commit.
        (self.archive.root / "CURRENT.json").unlink()
        self.assertEqual(len(list((self.archive.root / "releases").glob("*.json"))), 1)
        reopened = self.reopen()
        final = reopened.finalize()
        self.assertEqual(final["phase"], "promoted")
        self.assertEqual(final["release_id"], current["release_id"])
        self.assertEqual(reopened.archive.summary()["current"], current)
        self.assertEqual(len(list((self.archive.root / "releases").glob("*.json"))), 1)
        self.assertEqual(len(self.lines(self.evaluation_calls)), 3)

    def test_legacy_baseline_without_execution_is_rejected_before_start(self):
        record = {**self.baseline, "execution": None}
        atomic_json(self.archive.root / "evaluations" / (record["id"] + ".json"), record)
        with self.assertRaisesRegex(ArchiveError, "execution provenance"):
            self.initialize()
        self.assertEqual(self.lines(self.worker_calls), [])

    def test_expired_wall_budget_stops_prepared_work_without_dispatch(self):
        self.initialize()
        self.controller.prepare()
        with mock.patch("rsiforme.research._seconds_since", return_value=121):
            status = self.controller.run(max_new_trials=0)
            self.assertEqual(status["trials"][0]["phase"], "stopped")
            self.assertEqual(status["trials"][0]["decision"], "walltime_exhausted_before_worker")
            with self.assertRaisesRegex(ArchiveError, "confirmation budget"):
                self.controller.finalize()
        self.assertEqual(self.lines(self.worker_calls), [])
        self.assertEqual(len(self.lines(self.evaluation_calls)), 1)


if __name__ == "__main__":
    unittest.main()
