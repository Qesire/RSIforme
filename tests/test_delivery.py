"""Portable handoff tests run real Python processes; scores are test fixtures."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from rsiforme.archive import Archive, ArchiveError, file_sha
from rsiforme.delivery import export_release, verify_release
from rsiforme.workflow import evaluate_local


EVALUATOR = '''from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import uuid

@dataclass
class Observation:
    score: int

def evaluate_candidate(snapshot_dir, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.run([sys.executable, "-I", "-B", str(Path(snapshot_dir) / "src/solution.py")],
                             capture_output=True, text=True, check=False)
    (output_dir / "stdout.txt").write_text(process.stdout)
    (output_dir / "stderr.txt").write_text(process.stderr)
    factor = json.loads((Path(__file__).parent / "fixtures/multiplier.json").read_text())["factor"]
    observed = Observation(int(process.stdout.strip()) * factor)
    report = {"status": "completed", "valid": process.returncode == 0,
              "metrics": {"score": observed.score}, "eval_run_id": "fixture-" + uuid.uuid4().hex}
    (output_dir / "report.json").write_text(json.dumps(report))
    return report
'''


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.work = self.root / "work"
        (self.work / "src").mkdir(parents=True)
        self.evaluator_root = self.root / "original-evaluator"
        (self.evaluator_root / "fixtures").mkdir(parents=True)
        self.evaluator = self.evaluator_root / "evaluate.py"
        self.evaluator.write_text(EVALUATOR, encoding="utf-8")
        asset = self.evaluator_root / "fixtures/multiplier.json"
        asset.write_text('{"factor": 2}\n', encoding="utf-8")
        self.task = {
            "schema_version": 1, "task_id": "portable-delivery-fixture",
            "objective": "score", "direction": "maximize", "source_paths": ["src/solution.py"],
            "evaluator_assets": {"fixtures/multiplier.json": file_sha(asset)},
            "context": {"task_revision": "delivery-integration-fixture-v1",
                        "evaluator_revision": "sha256:" + file_sha(self.evaluator),
                        "environment": "Current local Python; integer output is a test score, not a benchmark",
                        "upstream_pins": {"fixture": "local-integration-v1"}},
        }
        self.archive = Archive(self.root / "archive")
        self.archive.initialize(self.task)

    def candidate(self, value=42, parent=None):
        source = self.work / "src/solution.py"
        source.write_text(f"print({value})\n", encoding="utf-8")
        source.chmod(0o755)
        return self.archive.capture(self.work, lane="delivery-test", parent_id=parent,
                                    hypothesis="Check a portable handoff with a real subprocess")

    def promoted(self):
        parent = self.candidate(21)
        candidate = self.candidate(42, parent["id"])
        search = evaluate_local(self.archive, candidate["id"], self.evaluator)
        self.assertTrue(search["valid"], search)
        confirmation = evaluate_local(self.archive, candidate["id"], self.evaluator,
                                      kind="confirmation", confirmation_of=search["id"])
        self.assertTrue(confirmation["valid"], confirmation)
        release = self.archive.promote(candidate["id"], search["id"], confirmation["id"])
        return candidate, search, confirmation, release

    def run_standalone(self, script, *arguments):
        process = subprocess.run([sys.executable, "-I", "-B", str(script), *map(str, arguments)],
                                 cwd=self.root, capture_output=True, text=True, check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def test_moved_release_survives_removed_archive_and_reexecutes_packaged_evaluator(self):
        candidate, search, confirmation, release = self.promoted()
        raw_search = (self.archive.root / "evaluations" / (search["id"] + ".json")).read_bytes()
        original_evidence = (self.archive.root / confirmation["evidence_path"] / "stdout.txt").read_bytes()
        exported = self.root / "exported"
        result = export_release(self.archive, exported)
        self.assertEqual(result["release_id"], release["id"])
        self.assertEqual(result["candidate_id"], candidate["id"])
        self.assertEqual(result["manifest_sha256"], file_sha(exported / "manifest.json"))
        self.assertEqual((exported / "records/search.json").read_bytes(), raw_search)
        self.assertEqual((exported / "evidence/confirmation/stdout.txt").read_bytes(), original_evidence)
        self.assertEqual((exported / "source/src/solution.py").read_bytes(), b"print(42)\n")
        self.assertEqual((exported / "source/src/solution.py").stat().st_mode & 0o777, 0o755)
        self.assertIn("-print(21)", (exported / "source.diff").read_text())
        self.assertIn("+print(42)", (exported / "source.diff").read_text())
        provenance = json.loads((exported / "provenance.json").read_text())
        self.assertEqual(provenance["upstream_pins"], self.task["context"]["upstream_pins"])

        moved = self.root / "received-elsewhere" / "release"
        moved.parent.mkdir()
        shutil.move(exported, moved)
        shutil.rmtree(self.archive.root)
        shutil.rmtree(self.work)
        shutil.rmtree(self.evaluator_root)
        self.assertTrue(verify_release(moved)["valid"])
        checked = self.run_standalone(moved / "verify.py")
        self.assertEqual(checked["source_digest"], candidate["source_digest"])
        recheck = self.root / "fresh-measurement"
        measured = self.run_standalone(moved / "reproduce.py", recheck)
        self.assertTrue(measured["valid"], measured)
        self.assertEqual(measured["result"]["metrics"], {"score": 84})
        self.assertEqual((recheck / "evidence/stdout.txt").read_text(), "42\n")
        self.assertNotEqual(measured["result"]["eval_run_id"], confirmation["eval_run_id"])
        self.assertTrue((recheck / "reproduction_result.json").is_file())
        self.assertTrue(verify_release(moved)["valid"])

    def test_search_leader_is_not_a_release_and_cannot_replace_current_export(self):
        candidate = self.candidate(41)
        evaluate_local(self.archive, candidate["id"], self.evaluator)
        destination = self.root / "not-promoted"
        with self.assertRaisesRegex(ArchiveError, "promoted release"):
            export_release(self.archive, destination)
        self.assertFalse(destination.exists())
        promoted, _, _, release = self.promoted()
        leader = self.candidate(99, promoted["id"])
        evaluate_local(self.archive, leader["id"], self.evaluator)
        self.assertEqual(self.archive.best()["candidate"]["id"], leader["id"])
        actual = export_release(self.archive, destination)
        self.assertEqual(actual["candidate_id"], promoted["id"])
        self.assertEqual(actual["release_id"], release["id"])
        with self.assertRaisesRegex(ArchiveError, "Unknown promoted release"):
            export_release(self.archive, self.root / "wrong-id", release_id=leader["id"])

    def test_hash_verification_detects_source_evidence_record_and_extra_file_changes(self):
        _, _, _, release = self.promoted()
        destination = self.root / "release"
        export_release(self.archive, destination, release_id=release["id"])
        for relative in ("source/src/solution.py", "evidence/confirmation/stdout.txt",
                         "records/search.json", "task.json"):
            with self.subTest(path=relative):
                path = destination / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\nchanged\n")
                with self.assertRaisesRegex(ArchiveError, "inventory or hashes changed"):
                    verify_release(destination)
                path.write_bytes(original)
        extra = destination / "unrecorded.txt"
        extra.write_text("unrecorded")
        with self.assertRaisesRegex(ArchiveError, "inventory or hashes changed"):
            verify_release(destination)
        extra.unlink()
        self.assertTrue(verify_release(destination)["valid"])

    def test_export_rejects_corrupted_original_evidence_and_never_overwrites(self):
        _, _, confirmation, _ = self.promoted()
        destination = self.root / "release"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep")
        with self.assertRaisesRegex(ArchiveError, "must not exist"):
            export_release(self.archive, destination)
        self.assertEqual(marker.read_text(), "keep")
        raw = self.archive.root / confirmation["evidence_path"] / "stdout.txt"
        raw.write_text("wrong raw evidence\n")
        failed = self.root / "failed-export"
        with self.assertRaises(ArchiveError):
            export_release(self.archive, failed)
        self.assertFalse(failed.exists())

    def test_later_evidence_attachment_remains_portable_and_verified(self):
        _, search, _, _ = self.promoted()
        extra = self.root / "late-judge-output"
        extra.mkdir()
        (extra / "test_output.txt").write_text("Late complete output from the same recorded test.\n")
        attachment = self.archive.attach_evidence(search["id"], extra, label="durable output")
        destination = self.root / "release"
        export_release(self.archive, destination)
        provenance = json.loads((destination / "provenance.json").read_text())
        included = provenance["evaluations"]["search"]["attachments"]
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0]["id"], attachment["id"])
        packaged = destination / included[0]["evidence_path"] / "test_output.txt"
        self.assertEqual(packaged.read_text(), (extra / "test_output.txt").read_text())
        shutil.rmtree(extra)
        shutil.rmtree(self.archive.root)
        self.assertTrue(self.run_standalone(destination / "verify.py")["valid"])
        packaged.unlink()
        with self.assertRaises(ArchiveError):
            verify_release(destination)

    def test_standalone_verifier_checks_confirmation_causality_beyond_file_hashes(self):
        _, search, confirmation, _ = self.promoted()
        destination = self.root / "release"
        export_release(self.archive, destination)
        path = destination / "records/confirmation.json"
        manifest_path = destination / "manifest.json"
        original_record, original_manifest = path.read_bytes(), manifest_path.read_bytes()
        for field, value in (("confirmation_of", "a-different-search"),
                             ("started_at", search["execution"]["started_at"])):
            with self.subTest(field=field):
                changed = json.loads(original_record)
                changed["execution"][field] = value
                path.write_text(json.dumps(changed))
                manifest = json.loads(original_manifest)
                entry = next(item for item in manifest["files"] if item["path"] == "records/confirmation.json")
                entry.update(sha256=file_sha(path), size=path.stat().st_size)
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ArchiveError, "fresh execution"):
                    verify_release(destination)
                process = subprocess.run([sys.executable, "-I", "-B", str(destination / "verify.py")],
                                         cwd=self.root, text=True, capture_output=True, check=False)
                self.assertEqual(process.returncode, 1)
                self.assertIn("fresh execution", process.stderr)
        path.write_bytes(original_record)
        manifest_path.write_bytes(original_manifest)
        self.assertTrue(self.run_standalone(destination / "verify.py")["valid"])


if __name__ == "__main__":
    unittest.main()
