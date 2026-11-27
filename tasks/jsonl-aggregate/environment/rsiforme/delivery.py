"""Portable releases of an already promoted candidate and its original evidence.

Export does not select, evaluate, or promote anything. A release can be moved
away from its archive; its included verifier needs only the Python standard
library. Verification checks retained bytes and bindings, not measured benefits.
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import inspect
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

from .archive import Archive, ArchiveError, sync_directory


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ArchiveError(f"Cannot read release JSON {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ArchiveError(f"Release JSON must contain an object: {path.name}")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ArchiveError(f"Expected an unambiguous relative release path: {value!r}")
    return value


def _inventory(root: Path, *, omit_manifest: bool = False) -> tuple[list[dict], list[str]]:
    files, directories = [], []
    if not root.is_dir() or root.is_symlink():
        raise ArchiveError(f"Release directory is missing or linked: {root}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if omit_manifest and relative == "manifest.json":
            continue
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            directories.append(relative)
        elif stat.S_ISREG(mode):
            files.append({"path": relative, "sha256": _file_digest(path),
                          "size": path.stat().st_size, "mode": stat.S_IMODE(mode)})
        else:
            raise ArchiveError(f"Release contains a linked or non-regular entry: {relative}")
    return files, directories


def _expected_directories(files: list[dict], directories: list[str]) -> list[str]:
    expected = set(directories)
    for relative in [item["path"] for item in files] + directories:
        expected.update(parent.as_posix() for parent in PurePosixPath(relative).parents
                        if parent.as_posix() != ".")
    return sorted(expected)


def _verify_evidence_files(directory: Path, record: dict) -> None:
    files, _ = _inventory(directory)
    retained = [{key: item[key] for key in ("path", "sha256", "size")} for item in files]
    if not retained or retained != record.get("evidence_files"):
        raise ArchiveError("Packaged evidence differs from its originally sealed inventory")
    if _digest(retained) != record.get("evidence_digest"):
        raise ArchiveError("Packaged evidence disagrees with its recorded digest")


def _validate_records(task: dict, candidate: dict, release: dict,
                      search: dict, confirmation: dict) -> None:
    context = task.get("context_fingerprint")
    if not context or _digest(task.get("task", {})) != context:
        raise ArchiveError("Frozen task fingerprint disagrees with its contents")
    if (release.get("candidate_id") != candidate.get("id")
            or candidate.get("task_id") != task["task"].get("task_id")
            or release.get("context_fingerprint") != context
            or candidate.get("context_fingerprint") != context
            or release.get("source_digest") != candidate.get("source_digest")):
        raise ArchiveError("Release, candidate, and frozen task bindings disagree")
    if _digest({"files": candidate.get("files"), "directories": candidate.get("directories")}) != candidate.get("source_digest"):
        raise ArchiveError("Candidate source inventory has an invalid digest")
    objective = task["task"].get("objective")
    for record, kind in ((search, "search"), (confirmation, "confirmation")):
        if (record.get("id") != release.get(kind + "_evaluation")
                or record.get("candidate_id") != candidate.get("id")
                or record.get("kind") != kind or record.get("status") != "completed"
                or record.get("valid") is not True
                or record.get("source_digest") != candidate.get("source_digest")
                or record.get("context_fingerprint") != context):
            raise ArchiveError("Release requires its valid search and confirmation of the same candidate/context")
        metrics = record.get("metrics")
        if not isinstance(metrics, dict) or objective not in metrics:
            raise ArchiveError("Selected evaluation is missing its task objective")
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in metrics.values()):
            raise ArchiveError("Selected evaluation has a non-finite or nonnumeric metric")
    if search.get("id") == confirmation.get("id"):
        raise ArchiveError("Search and confirmation must be distinct evaluation records")
    if (search.get("eval_run_id") and search.get("eval_run_id") == confirmation.get("eval_run_id")):
        raise ArchiveError("Search and confirmation reuse one evaluator execution")
    if (search.get("harness_run_id") and
            (search.get("harness_run_id"), search.get("round_id")) ==
            (confirmation.get("harness_run_id"), confirmation.get("round_id"))):
        raise ArchiveError("Search and confirmation reuse one Harness round")
    clocks = []
    for record in (search, confirmation):
        execution = record.get("execution")
        if not isinstance(execution, dict) or not isinstance(execution.get("id"), str) or not execution["id"]:
            raise ArchiveError("Release evaluation requires an explicit execution identity")
        try:
            times = tuple(dt.datetime.fromisoformat(value) for value in
                          (execution["started_at"], execution["finished_at"], record["created_at"]))
            if any(value.utcoffset() is None for value in times) or not times[0] <= times[1] <= times[2]:
                raise ValueError("Execution timestamps must be aware and causal")
        except (KeyError, TypeError, ValueError) as error:
            raise ArchiveError("Release evaluation ordering is invalid") from error
        clocks.append(times)
    if (search["execution"].get("confirmation_of") is not None
            or confirmation["execution"].get("confirmation_of") != search["id"]
            or search["execution"]["id"] == confirmation["execution"]["id"]
            or clocks[1][0] < max(clocks[0][1], clocks[0][2])
            or clocks[1][2] <= clocks[0][2]):
        raise ArchiveError("Confirmation must be a fresh execution of its exact selected search")


def _local_recipe(directory: Path, task: dict, confirmation: dict) -> dict | None:
    recipe = confirmation.get("reproduction")
    if not isinstance(recipe, dict) or recipe.get("adapter") != "local-python":
        return None
    entrypoint = _relative(recipe.get("entrypoint"))
    entry = directory / "evidence" / "confirmation" / entrypoint
    if not entry.is_file() or entry.is_symlink():
        raise ArchiveError("The packaged local evaluator entrypoint is missing")
    expected = task["task"]["context"].get("evaluator_revision")
    if expected != "sha256:" + _file_digest(entry):
        raise ArchiveError("Packaged local evaluator differs from the frozen task")
    if recipe.get("callable", "evaluate_candidate") != "evaluate_candidate":
        raise ArchiveError("Unsupported local evaluator callable")
    assets = recipe.get("assets", {})
    if not isinstance(assets, dict):
        raise ArchiveError("Local evaluator assets must be a path-to-SHA256 mapping")
    frozen_assets = {name: digest.removeprefix("sha256:")
                     for name, digest in task["task"].get("evaluator_assets", {}).items()}
    frozen_assets[entry.name] = expected.removeprefix("sha256:")
    if assets != frozen_assets:
        raise ArchiveError("Packaged evaluator assets disagree with the frozen task's declared dependencies")
    for relative, expected_digest in assets.items():
        asset = entry.parent / _relative(relative)
        if not asset.is_file() or asset.is_symlink() or _file_digest(asset) != expected_digest:
            raise ArchiveError(f"Packaged evaluator asset differs from its recorded bytes: {relative}")
    return recipe


def verify_release(directory: str | Path) -> dict:
    """Check every packaged file and its source/evidence identity without the archive.

    Return a JSON-ready summary on success; raise ArchiveError on missing,
    changed, or inconsistent contents. No packaged candidate/evaluator executes.
    """
    directory = Path(directory).expanduser().resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise ArchiveError("Release manifest must be a regular file")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "rsiforme-release":
        raise ArchiveError("Unsupported release manifest")
    files, directories = _inventory(directory, omit_manifest=True)
    if files != manifest.get("files") or directories != manifest.get("directories"):
        raise ArchiveError("Release file inventory or hashes changed (missing, extra, or modified content)")
    task = _read_json(directory / "task.json")
    candidate = _read_json(directory / "records" / "candidate.json")
    release = _read_json(directory / "records" / "release.json")
    search = _read_json(directory / "records" / "search.json")
    confirmation = _read_json(directory / "records" / "confirmation.json")
    provenance = _read_json(directory / "provenance.json")
    _validate_records(task, candidate, release, search, confirmation)
    source_files, source_directories = _inventory(directory / "source")
    if (source_files != candidate["files"] or
            source_directories != _expected_directories(candidate["files"], candidate["directories"])):
        raise ArchiveError("Packaged source differs from the promoted candidate")
    for kind, record in (("search", search), ("confirmation", confirmation)):
        _verify_evidence_files(directory / "evidence" / kind, record)
        packaged = provenance.get("evaluations", {}).get(kind, {})
        if packaged.get("evaluation_id") != record["id"] or packaged.get("evidence_path") != "evidence/" + kind:
            raise ArchiveError("Packaged evaluation/evidence binding disagrees")
        for item in packaged.get("attachments", []):
            attachment = _read_json(directory / _relative(item["record_path"]))
            if (attachment.get("evaluation_id") != record["id"]
                    or attachment.get("id") != item.get("id")):
                raise ArchiveError("Packaged attachment is bound to a different evaluation")
            _verify_evidence_files(directory / _relative(item["evidence_path"]), attachment)
    for key, expected in (("release_id", release["id"]), ("candidate_id", candidate["id"]),
                          ("context_fingerprint", task["context_fingerprint"]),
                          ("source_digest", candidate["source_digest"])):
        if manifest.get(key) != expected or provenance.get(key) != expected:
            raise ArchiveError(f"Release provenance disagrees: {key}")
    recipe = _local_recipe(directory, task, confirmation)
    if recipe is not None and not (directory / "reproduce.py").is_file():
        raise ArchiveError("Local release is missing its reproduction runner")
    return {"valid": True, "release_id": release["id"], "candidate_id": candidate["id"],
            "task_id": task["task"]["task_id"], "source_digest": candidate["source_digest"],
            "context_fingerprint": task["context_fingerprint"], "file_count": len(files),
            "reproduction_adapter": recipe["adapter"] if recipe else
                confirmation.get("reproduction", {}).get("adapter", "recorded-protocol")}


def _standalone_verifier() -> str:
    """Copy the same stdlib-only verification functions into the release."""
    header = ('#!/usr/bin/env python3\nfrom __future__ import annotations\n'
              'import datetime as dt\nimport hashlib\nimport json\nimport math\n'
              'from pathlib import Path, PurePosixPath\nimport stat\n'
              'class ArchiveError(ValueError):\n    pass\n\n')
    functions = (_read_json, _digest, _file_digest, _relative, _inventory,
                 _expected_directories, _verify_evidence_files, _validate_records,
                 _local_recipe, verify_release)
    main = '''
if __name__ == "__main__":
    import sys
    try:
        if len(sys.argv) > 2:
            raise ArchiveError("Usage: python verify.py [release-directory]")
        root = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(__file__).resolve().parent
        print(json.dumps(verify_release(root), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        print(f"Release verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
'''
    return header + "\n\n".join(inspect.getsource(function) for function in functions) + main


_REPRODUCTION_RUNNER = '''#!/usr/bin/env python3
"""Re-run the packaged trusted local evaluator; keep all new results outside this release."""
from pathlib import Path
import importlib.util
import json
import shutil
import sys
import traceback

sys.dont_write_bytecode = True
bundle = Path(__file__).resolve().parent
if len(sys.argv) != 2:
    raise SystemExit("Usage: python reproduce.py NEW_OUTPUT_DIRECTORY")
output = Path(sys.argv[1]).expanduser().resolve()
if output.exists() or output == bundle or output.is_relative_to(bundle):
    raise SystemExit("Use a new output directory outside the release")
spec = importlib.util.spec_from_file_location("release_verifier", bundle / "verify.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
verified = verifier.verify_release(bundle)
record = json.loads((bundle / "records/confirmation.json").read_text())
recipe = record["reproduction"]
entry = bundle / "evidence/confirmation" / recipe["entrypoint"]
output.mkdir(parents=True)
source = output / "source"
shutil.copytree(bundle / "source", source)
expected_source = verifier._inventory(source)
evidence = output / "evidence"
evidence.mkdir()
report = {"release": verified, "status": "infra_error", "valid": False}
try:
    sys.path.insert(0, str(entry.parent))
    spec = importlib.util.spec_from_file_location("release_evaluator", entry)
    evaluator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evaluator
    spec.loader.exec_module(evaluator)
    result = evaluator.evaluate_candidate(source, evidence)
    if not isinstance(result, dict):
        raise ValueError("Evaluator must return a result dictionary")
    if verifier._inventory(source) != expected_source:
        raise ValueError("Evaluator changed its candidate source")
    verifier.verify_release(bundle)
    report.update(status=result.get("status"), valid=result.get("valid") is True, result=result)
except Exception as error:
    report["error"] = f"{type(error).__name__}: {error}"
    (output / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
(output / "reproduction_result.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
raise SystemExit(0 if report["status"] == "completed" and report["valid"] else 1)
'''


def _source_diff(archive: Archive, candidate: dict) -> str | None:
    parent_id = candidate.get("parent_id")
    if not parent_id:
        return None
    parent = archive.source(parent_id)
    current = archive.source(candidate["id"])
    old_files = {item["path"]: item for item in archive.candidate(parent_id)["files"]}
    new_files = {item["path"]: item for item in candidate["files"]}
    lines = []
    for relative in sorted(old_files.keys() | new_files.keys()):
        old, new = old_files.get(relative), new_files.get(relative)
        if old == new:
            continue
        old_bytes = (parent / relative).read_bytes() if old else b""
        new_bytes = (current / relative).read_bytes() if new else b""
        lines.append(f"diff --git a/{relative} b/{relative}\n")
        if old and new and old["mode"] != new["mode"]:
            lines.extend((f"old mode {stat.S_IFREG | old['mode']:06o}\n",
                          f"new mode {stat.S_IFREG | new['mode']:06o}\n"))
        elif not old:
            lines.append(f"new file mode {stat.S_IFREG | new['mode']:06o}\n")
        elif not new:
            lines.append(f"deleted file mode {stat.S_IFREG | old['mode']:06o}\n")
        try:
            old_text, new_text = old_bytes.decode("utf-8"), new_bytes.decode("utf-8")
            if "\0" in old_text or "\0" in new_text:
                raise UnicodeError("binary content")
            for line in difflib.unified_diff(old_text.splitlines(keepends=True),
                                             new_text.splitlines(keepends=True),
                                             fromfile="a/" + relative if old else "/dev/null",
                                             tofile="b/" + relative if new else "/dev/null"):
                lines.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        except UnicodeError:
            lines.append(f"Binary files a/{relative} and b/{relative} differ; full target bytes are in source/.\n")
    return "".join(lines)


def _instructions(task: dict, candidate: dict, release: dict, recipe: dict | None) -> str:
    lines = ["# Reproduce this promoted release", "",
             f"Task: `{task['task']['task_id']}`. Release: `{release['id']}`.", "",
             "## Verify the handoff", "", "```bash", "python3 verify.py", "```", "",
             "The bundled verifier uses only Python's standard library. It checks source, raw evidence,",
             "frozen task, release records, and all file hashes. It can run after the original archive",
             "is removed. Verification checks retained evidence; it does not remeasure the result.", "",
             "## Re-run", "",
             "Match the hardware, dependencies, datasets and measurement scope recorded in `task.json`.",
             "Keep `source/` as the exact delivered candidate; use a new output directory for each run.", ""]
    if recipe is not None:
        lines += ["The exact local evaluator and declared assets are retained with the confirmation evidence.",
                  "The runner copies the candidate into a new workspace and calls its fixed",
                  "`evaluate_candidate(snapshot_dir, output_dir)` interface:", "", "```bash",
                  "python3 reproduce.py ../release-recheck-001", "```", "",
                  "This creates `source/`, raw `evidence/` and `reproduction_result.json` in the new",
                  "directory. A correct completed result exits 0; errors or rejected results exit 1.",
                  "Use the Python/runtime specified by the frozen task for comparable measurements.", ""]
    else:
        lines += ["This evaluation uses its recorded external runtime. Read the selected raw records in",
                  "`records/search.json` and `records/confirmation.json`, and their complete evidence in",
                  "`evidence/search/` and `evidence/confirmation/`. Follow their saved reproduction",
                  "commands and the frozen task/environment references. For RSI-Harness, restore",
                  "`source/` into a fresh Work workspace for that task and submit to its independent Judge.",
                  "No local evaluator command is inferred for an external task.", ""]
    lines += ["## Provenance", "",
              "`records/` preserves the selected archive records byte for byte. `provenance.json` maps",
              "their original evidence references to the portable copies. `manifest.json` inventories",
              "every exported file except itself. Optional upstream pins come only from the frozen task.", "",
              "Later sealed evidence attachments are included under `evidence/attachments/`, with their",
              "original manifests under `records/attachments/`. They supplement the original evaluation.", "",
              f"Candidate: `{candidate['id']}`; parent: `{candidate.get('parent_id')}`.",
              f"Source digest: `{candidate['source_digest']}`.", "",
              "When present, `source.diff` describes changes from the retained parent. Full target source",
              "is authoritative, including binary files and executable modes.", ""]
    return "\n".join(lines)


def export_release(archive: Archive, destination: str | Path, release_id: str | None = None) -> dict:
    """Export an existing promotion to a new directory; never fall back to best()."""
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ArchiveError("Release destination must not exist")
    if destination == archive.root or destination.is_relative_to(archive.root):
        raise ArchiveError("Release destination must be outside the research archive")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + destination.name + ".export-", dir=destination.parent))
    try:
        with archive._lock():
            config = archive.config
            if release_id is None:
                current_path = archive.root / "CURRENT.json"
                if not current_path.is_file():
                    raise ArchiveError("Export requires a promoted release; best search is not a release")
                release_id = _read_json(current_path).get("release_id")
            if not isinstance(release_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", release_id):
                raise ArchiveError("Invalid promoted release ID")
            release_path = archive.root / "releases" / (release_id + ".json")
            if not release_path.is_file():
                raise ArchiveError(f"Unknown promoted release: {release_id}")
            release = _read_json(release_path)
            if release.get("id") != release_id:
                raise ArchiveError("Release ID disagrees with its record")
            candidate = archive.candidate(release["candidate_id"])
            search = archive.evaluation(release["search_evaluation"])
            confirmation = archive.evaluation(release["confirmation_evaluation"])
            _validate_records(config, candidate, release, search, confirmation)
            source = archive.source(candidate["id"])
            shutil.copytree(source, staging / "source")
            records = staging / "records"
            records.mkdir()
            shutil.copyfile(archive.root / "task.json", staging / "task.json")
            shutil.copyfile(release_path, records / "release.json")
            shutil.copyfile(archive.root / "candidates" / candidate["id"] / "manifest.json", records / "candidate.json")
            evidence_map = {}
            for kind, record in (("search", search), ("confirmation", confirmation)):
                verified_evidence = archive.verify_evidence(record["id"])
                if verified_evidence.get("verified") is not True:
                    raise ArchiveError(f"The promoted {kind} evidence has no originally sealed inventory")
                if not record.get("evidence_path"):
                    raise ArchiveError(f"The promoted {kind} evaluation has no retained raw evidence")
                evidence = archive.root / _relative(record["evidence_path"])
                files, _ = _inventory(evidence)
                if not files:
                    raise ArchiveError(f"The promoted {kind} evaluation has empty raw evidence")
                shutil.copyfile(archive.root / "evaluations" / (record["id"] + ".json"), records / (kind + ".json"))
                shutil.copytree(evidence, staging / "evidence" / kind)
                if _inventory(evidence) != _inventory(staging / "evidence" / kind):
                    raise ArchiveError("Evidence changed while being exported")
                evidence_map[kind] = {"evaluation_id": record["id"],
                                      "original_evidence_path": record["evidence_path"],
                                      "evidence_path": "evidence/" + kind,
                                      "attachments": []}
                for attachment in verified_evidence.get("attachments", []):
                    if attachment.get("verified") is not True:
                        raise ArchiveError("An evidence attachment is not verified")
                    attachment_id = _relative(attachment["id"])
                    if "/" in attachment_id:
                        raise ArchiveError("Attachment ID must be a single filename")
                    data_path = f"evidence/attachments/{kind}/{attachment_id}"
                    record_path = f"records/attachments/{kind}/{attachment_id}.json"
                    attachment_data = archive.root / _relative(attachment["evidence_path"])
                    shutil.copytree(attachment_data, staging / data_path)
                    if _inventory(attachment_data) != _inventory(staging / data_path):
                        raise ArchiveError("Evidence attachment changed while being exported")
                    (staging / record_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(archive.root / _relative(attachment["manifest_path"]), staging / record_path)
                    evidence_map[kind]["attachments"].append({
                        "id": attachment_id, "label": attachment.get("label"),
                        "original_evidence_path": attachment["evidence_path"],
                        "record_path": record_path, "evidence_path": data_path})
                if archive.verify_evidence(record["id"]) != verified_evidence:
                    raise ArchiveError("Sealed evidence changed while being exported")
                if record.get("harness_run_id"):
                    binding = archive.binding(record["harness_run_id"], record["round_id"])
                    shutil.copyfile(archive.root / "submissions" / (binding["id"] + ".json"),
                                    records / (kind + "-submission.json"))
            archive.source(candidate["id"])
            diff = _source_diff(archive, candidate)
            if diff is not None:
                (staging / "source.diff").write_text(diff, encoding="utf-8")
            recipe = _local_recipe(staging, config, confirmation)
            provenance = {"schema_version": 1, "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                          "release_id": release["id"], "candidate_id": candidate["id"],
                          "source_digest": candidate["source_digest"],
                          "context_fingerprint": config["context_fingerprint"],
                          "parent_id": candidate.get("parent_id"), "evaluations": evidence_map,
                          "upstream_pins": config["task"]["context"].get("upstream_pins"),
                          "reproduction_adapter": recipe["adapter"] if recipe else
                              confirmation.get("reproduction", {}).get("adapter", "recorded-protocol")}
            (staging / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2,
                                                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            (staging / "REPRODUCE.md").write_text(_instructions(config, candidate, release, recipe), encoding="utf-8")
            (staging / "verify.py").write_text(_standalone_verifier(), encoding="utf-8")
            (staging / "verify.py").chmod(0o755)
            if recipe is not None:
                (staging / "reproduce.py").write_text(_REPRODUCTION_RUNNER, encoding="utf-8")
                (staging / "reproduce.py").chmod(0o755)
            files, directories = _inventory(staging)
            manifest = {"schema_version": 1, "kind": "rsiforme-release",
                        "release_id": release["id"], "candidate_id": candidate["id"],
                        "source_digest": candidate["source_digest"],
                        "context_fingerprint": config["context_fingerprint"],
                        "files": files, "directories": directories}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                                             sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            verified = verify_release(staging)
            for path in staging.rglob("*"):
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
            for directory in sorted((path for path in staging.rglob("*") if path.is_dir()),
                                    key=lambda path: len(path.parts), reverse=True):
                sync_directory(directory)
            sync_directory(staging)
            if destination.exists():
                raise ArchiveError("Release destination appeared during export; nothing was overwritten")
            staging.rename(destination)
            sync_directory(destination.parent)
            return {**verified, "directory": str(destination),
                    "manifest_sha256": _file_digest(destination / "manifest.json")}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
