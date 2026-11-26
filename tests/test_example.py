"""Behavior tests for the independent example evaluator."""

import importlib.util
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "jsonl_aggregate"
spec = importlib.util.spec_from_file_location("example_evaluator", EXAMPLE / "evaluate.py")
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class ExampleTests(unittest.TestCase):
    def test_baseline_and_optimized_agree_with_oracle(self):
        for fixture in ("baseline", "optimized"):
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as output:
                result = evaluator.evaluate_candidate(EXAMPLE / "fixtures" / fixture, Path(output))
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["valid"])
                self.assertGreater(result["metrics"]["rows_per_second"], 0)
                self.assertEqual(json.loads((Path(result["evidence_dir"]) / "report.json").read_text()), result)

    def test_fast_wrong_candidate_is_ineligible(self):
        with tempfile.TemporaryDirectory() as output:
            result = evaluator.evaluate_candidate(EXAMPLE / "fixtures" / "incorrect", Path(output))
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["valid"])
            self.assertEqual(result["metrics"], {})
            self.assertIn("negative", result["failure"])
            self.assertTrue((Path(result["evidence_dir"]) / "negative.mismatch.json").is_file())

    def test_candidate_crash_preserves_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "solution.py").write_text("raise RuntimeError('candidate failed deliberately')\n")
            result = evaluator.evaluate_candidate(root, root / "evidence")
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["valid"])
            stderr = (Path(result["evidence_dir"]) / "empty.stderr.txt").read_text()
            self.assertIn("candidate failed deliberately", stderr)

    def test_no_result_is_overwritten_on_repeat(self):
        with tempfile.TemporaryDirectory() as output:
            first = evaluator.evaluate_candidate(EXAMPLE / "fixtures" / "incorrect", Path(output))
            second = evaluator.evaluate_candidate(EXAMPLE / "fixtures" / "incorrect", Path(output))
            self.assertNotEqual(first["eval_run_id"], second["eval_run_id"])
            for item in (first, second):
                self.assertTrue((Path(item["evidence_dir"]) / "report.json").is_file())

    def test_timeout_is_a_candidate_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "solution.py").write_text("import time\ntime.sleep(20)\n")
            with patch.object(evaluator, "CALL_TIMEOUT_SECONDS", 0.1):
                result = evaluator.evaluate_candidate(root, root / "evidence")
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["valid"])
            self.assertIn("timeout", result["failure"])

    def test_packaged_harness_judge_reward_gate(self):
        path = ROOT / "tasks" / "jsonl-aggregate" / "tests" / "judge.py"
        spec = importlib.util.spec_from_file_location("packaged_judge", path)
        judge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(judge)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fixture, valid in (("incorrect", False), ("optimized", True)):
                with self.subTest(fixture=fixture), contextlib.redirect_stdout(io.StringIO()):
                    output = root / fixture
                    result = judge.main(EXAMPLE / "fixtures" / fixture, output)
                    self.assertEqual(result, 0 if valid else 1)
                    reward = output / "reward.json"
                    self.assertEqual(reward.exists(), valid)
                    if valid:
                        payload = json.loads(reward.read_text())
                        self.assertEqual(set(payload), {"reward", "quality_passed"})
                        self.assertEqual(payload["quality_passed"], 1)
                        self.assertGreater(payload["reward"], 0)

    def test_packaged_assets_match_trusted_sources(self):
        task = ROOT / "tasks" / "jsonl-aggregate"
        pairs = [
            (task / "tests" / "evaluate.py", EXAMPLE / "evaluate.py"),
            (task / "tests" / "baseline.py", EXAMPLE / "fixtures" / "baseline" / "solution.py"),
            (task / "environment" / "solution.py", EXAMPLE / "fixtures" / "baseline" / "solution.py"),
            (task / "solution" / "optimized.py", EXAMPLE / "fixtures" / "optimized" / "solution.py"),
        ]
        for bundled, canonical in pairs:
            with self.subTest(path=bundled):
                self.assertEqual(bundled.read_bytes(), canonical.read_bytes())

        spec = importlib.util.spec_from_file_location("task_sync", task / "sync_assets.py")
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        for destination, contents in sync.expected_assets().items():
            with self.subTest(path=destination):
                self.assertEqual(destination.read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()
