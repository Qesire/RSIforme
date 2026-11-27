"""Execute the installed, pinned upstream compiler; never substitute a mock.

Ordinary stdlib CI can skip this optional dependency. The native workflow sets
RSIFORME_REQUIRE_NATIVE=1, so missing upstream dependencies fail that job.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE_AVAILABLE = importlib.util.find_spec("rsi_harness") is not None
if os.environ.get("RSIFORME_REQUIRE_NATIVE") == "1" and not NATIVE_AVAILABLE:
    raise RuntimeError("native CI requires the actual pinned rsi_harness package")


@unittest.skipUnless(NATIVE_AVAILABLE, "requires the separately installed pinned RSI-Harness")
class NativeCompilerTests(unittest.TestCase):
    def test_installed_compiler_is_the_locked_checkout(self):
        import rsi_harness.task.compiler as compiler
        upstream = os.environ.get("RSIFORME_UPSTREAM")
        if upstream is None:
            self.skipTest("set RSIFORME_UPSTREAM to verify the editable checkout identity")
        checkout = Path(upstream).resolve()
        actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        locked = json.loads((ROOT / "configs/upstreams.lock.json").read_text())["execution"]["harness"]["revision"]
        self.assertEqual(actual, locked)
        self.assertEqual(Path(compiler.__file__).resolve(), checkout / "RSI-Harness/src/rsi_harness/task/compiler.py")

    def test_packaged_task_compiles_with_original_upstream(self):
        from rsi_harness.models import CompileOptions
        from rsi_harness.task.compiler import HarborTaskCompiler
        task = HarborTaskCompiler().compile(
            ROOT / "tasks/jsonl-aggregate",
            CompileOptions(agent_name="oracle", primary_reward="reward", max_submissions=2),
        )
        self.assertEqual(task.task_id, "jsonl-aggregate")
        self.assertEqual(str(task.workdir), "/workspace")
        self.assertEqual(task.gpu_requirement.count, 0)
        self.assertEqual(task.verifier.gpu_count, 0)
        self.assertEqual(task.verifier.primary_reward, "reward")
        self.assertEqual(task.agent.name, "oracle")
        self.assertEqual(task.agent.network.mode, "no-network")
        self.assertFalse(task.verifier.final_judge)

    def test_one_submission_selects_upstreams_distinct_final_judge_mode(self):
        from rsi_harness.models import CompileOptions
        from rsi_harness.task.compiler import HarborTaskCompiler
        task = HarborTaskCompiler().compile(
            ROOT / "tasks/jsonl-aggregate",
            CompileOptions(agent_name="oracle", max_submissions=1),
        )
        self.assertTrue(task.verifier.final_judge)

    def test_site_template_uses_actual_upstream_profile_model(self):
        from rsi_harness.cluster.config import load_cluster_profile
        from rsi_harness.errors import SetupError
        profile = ROOT / "configs/slurm.example.toml"
        with self.assertRaises(SetupError):
            load_cluster_profile(profile, environ={"USER": "schema-test"})
        environment = {
            "USER": "schema-test", "RSIFORME_SLURM_PARTITION": "schema-partition",
            "RSIFORME_SLURM_ACCOUNT": "schema-account", "RSIFORME_SHARED_ROOT": "/schema-only/shared",
            "RSIFORME_PODMAN_BINARY": "/schema-only/bin/podman", "RSIFORME_NODE_TMP": "/tmp",
            "RSIFORME_APPTAINER_BINARY": "/schema-only/bin/apptainer",
            "RSIFORME_TASK_WORKDIR": "/workspace", "RSIFORME_TASK_PYTHON": "/usr/local/bin/python3",
        }
        parsed = load_cluster_profile(profile, environ=environment)
        self.assertEqual(parsed.adapter, "slurm")
        self.assertEqual(parsed.scheduler.queue, "schema-partition")
        # This is real schema validation of example values, not a site/runtime test.


if __name__ == "__main__":
    unittest.main()
