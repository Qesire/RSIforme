"""Immutable source snapshots and explicit evaluation/release records.

One controller uses a POSIX filesystem. Atomic files are the authoritative state;
STATUS.json and JOURNAL.md are regenerable views. This is not a distributed queue.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Iterator
import uuid


class ArchiveError(ValueError):
    """A research-state invariant was not satisfied."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ArchiveError(f"Expected a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    """Flush data before an atomic rename; no partially written JSON is visible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2,
                                    sort_keys=True, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value):
        raise ArchiveError(f"Invalid identifier: {value!r}")
    return value


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or "\\" in value:
        raise ArchiveError(f"Expected a relative POSIX source path: {value!r}")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or value in (".", "..")
            or any(part in ("", ".", "..", ".git", "__pycache__")
                   for part in value.split("/"))):
        raise ArchiveError(f"Unsafe or ambiguous source path: {value!r}")
    return path.as_posix()


def validate_task(task: dict) -> dict:
    task = json.loads(canonical(task))
    if task.get("schema_version") != 1:
        raise ArchiveError("RSIforme task schema_version must be 1 (separate from Harness schema).")
    _identifier(task.get("task_id"))
    if not isinstance(task.get("objective"), str) or not task["objective"].strip():
        raise ArchiveError("task.objective must name one measured metric")
    if task.get("direction") not in ("maximize", "minimize"):
        raise ArchiveError("task.direction must be maximize or minimize")
    paths = task.get("source_paths")
    if not isinstance(paths, list) or not paths:
        raise ArchiveError("task.source_paths must explicitly select files or directories")
    normalized = sorted(_relative_path(item) for item in paths)
    for i, path in enumerate(normalized):
        if any(path == p or path.startswith(p + "/") for p in normalized[:i]):
            raise ArchiveError("source_paths must not overlap or repeat")
    task["source_paths"] = normalized
    context = task.get("context")
    if not isinstance(context, dict):
        raise ArchiveError("task.context must describe the evaluator and runtime")
    for key in ("task_revision", "evaluator_revision", "environment"):
        if not context.get(key):
            raise ArchiveError(f"task.context.{key} is required")
    return task


def _source_inventory(workspace: Path, paths: list[str]) -> tuple[list[dict], list[str]]:
    files: list[dict] = []
    directories: set[str] = set()

    def visit(path: Path) -> None:
        relative = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            raise ArchiveError(f"Source symlinks are not supported: {relative}; reference external assets separately")
        if path.is_dir():
            directories.add(relative)
            for child in sorted(path.iterdir()):
                if child.name in (".git", "__pycache__") or child.suffix in (".pyc", ".pyo"):
                    continue
                visit(child)
        elif path.is_file():
            files.append({"path": relative, "sha256": file_sha(path),
                          "size": path.stat().st_size,
                          "mode": 0o755 if path.stat().st_mode & 0o111 else 0o644})
        else:
            raise ArchiveError(f"Missing or non-regular source entry: {relative}")

    for relative in paths:
        path = workspace / relative
        for parent in [path, *path.parents]:
            if parent == workspace:
                break
            if parent.is_symlink():
                raise ArchiveError(f"Source path contains a symlink: {relative}")
        visit(path)
    return sorted(files, key=lambda item: item["path"]), sorted(directories)


def _timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ArchiveError(f"{label} must be an ISO timestamp with a timezone")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise ArchiveError(f"{label} must be an ISO timestamp with a timezone") from error
    if parsed.utcoffset() is None:
        raise ArchiveError(f"{label} must be an ISO timestamp with a timezone")
    return parsed


def _execution(value: Any) -> dict | None:
    """Validate optional metadata; legacy evaluation records remain readable."""
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
        raise ArchiveError("execution requires a nonempty id")
    started = _timestamp(value.get("started_at"), "execution.started_at")
    finished = _timestamp(value.get("finished_at"), "execution.finished_at")
    if finished < started:
        raise ArchiveError("execution.finished_at must not precede started_at")
    if "confirmation_of" not in value:
        raise ArchiveError("execution.confirmation_of must name the search evaluation or be null")
    if value["confirmation_of"] is not None:
        _identifier(value["confirmation_of"])
    return value


def _evidence_inventory(root: Path) -> list[dict]:
    """Hash every regular file, including dotfiles and evaluator caches."""
    if root.is_symlink() or not root.is_dir():
        raise ArchiveError(f"Evidence directory is missing or is a symlink: {root}")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ArchiveError(f"Evidence symlinks are not supported: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ArchiveError(f"Evidence contains a non-regular file: {path}")
        files.append({"path": path.relative_to(root).as_posix(), "sha256": file_sha(path),
                      "size": path.stat().st_size})
    return files


class Archive:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def initialize(self, task: dict) -> dict:
        task = validate_task(task)
        manifest = {"schema_version": 1, "created_at": now(), "task": task,
                    "context_fingerprint": fingerprint(task)}
        with self._lock():
            path = self.root / "task.json"
            if path.exists():
                existing = self.config
                if existing["context_fingerprint"] != manifest["context_fingerprint"]:
                    raise ArchiveError("Task/context differs. Start a new archive to preserve comparability.")
                return existing
            atomic_json(path, manifest)
            for name in ("candidates", "evaluations", "submissions", "releases", "evidence", "attachments"):
                (self.root / name).mkdir(exist_ok=True)
        return manifest

    @property
    def config(self) -> dict:
        path = self.root / "task.json"
        if not path.is_file():
            raise ArchiveError(f"Archive is not initialized: {self.root}")
        config = load_json(path)
        if fingerprint(config["task"]) != config.get("context_fingerprint"):
            raise ArchiveError("Frozen task manifest has changed")
        return config

    def _records(self, directory: str) -> list[dict]:
        self.config
        records = [load_json(p) for p in sorted((self.root / directory).glob("*.json"))]
        return sorted(records, key=lambda row: (row["created_at"], row.get("id", "")))

    def candidates(self) -> list[dict]:
        self.config
        return sorted((load_json(p) for p in (self.root / "candidates").glob("*/manifest.json")),
                      key=lambda row: (row["created_at"], row["id"]))

    def candidate(self, candidate_id: str) -> dict:
        path = self.root / "candidates" / _identifier(candidate_id) / "manifest.json"
        if not path.is_file():
            raise ArchiveError(f"Unknown candidate: {candidate_id}")
        manifest = load_json(path)
        if manifest["context_fingerprint"] != self.config["context_fingerprint"]:
            raise ArchiveError("Candidate belongs to a different task/context")
        return manifest

    def source(self, candidate_id: str, *, verify: bool = True) -> Path:
        manifest = self.candidate(candidate_id)
        source = self.root / "candidates" / candidate_id / "source"
        if verify:
            entries = list(source.rglob("*"))
            if any(entry.is_symlink() for entry in entries):
                raise ArchiveError(f"Archived source contains a symlink: {candidate_id}")
            actual_files = {p.relative_to(source).as_posix() for p in entries if p.is_file()}
            if actual_files != {item["path"] for item in manifest["files"]}:
                raise ArchiveError(f"Archived source contains missing or extra files: {candidate_id}")
            files, directories = _source_inventory(source, manifest["source_paths"])
            if files != manifest["files"] or directories != manifest["directories"]:
                raise ArchiveError(f"Archived source has changed: {candidate_id}")
            if fingerprint({"files": files, "directories": directories}) != manifest["source_digest"]:
                raise ArchiveError(f"Invalid source digest: {candidate_id}")
        return source

    def capture(self, workspace: str | Path, *, lane: str, hypothesis: str,
                parent_id: str | None = None) -> dict:
        config = self.config
        _identifier(lane)
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ArchiveError("Record a concrete hypothesis before capturing a candidate")
        workspace = Path(workspace).expanduser().resolve()
        paths = config["task"]["source_paths"]
        if parent_id:
            self.candidate(parent_id)
        for relative in paths:
            selected = workspace / relative
            if self.root == selected or self.root.is_relative_to(selected):
                raise ArchiveError("Archive cannot be inside a captured source directory")
        with self._lock():
            files, directories = _source_inventory(workspace, paths)
            if not files:
                raise ArchiveError("A candidate must contain at least one source/config file")
            candidate_id = "c-" + uuid.uuid4().hex[:16]
            staging = Path(tempfile.mkdtemp(prefix=".capture-", dir=self.root))
            try:
                source = staging / "source"
                source.mkdir()
                for relative in directories:
                    (source / relative).mkdir(parents=True, exist_ok=True)
                for item in files:
                    target = source / item["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(workspace / item["path"], target)
                    target.chmod(item["mode"])
                    with target.open("rb") as saved:
                        os.fsync(saved.fileno())
                    if file_sha(target) != item["sha256"]:
                        raise ArchiveError("Source changed while being captured; retry after editing stops")
                if _source_inventory(workspace, paths) != (files, directories):
                    raise ArchiveError("Source changed while being captured; retry after editing stops")
                manifest = {"schema_version": 1, "id": candidate_id, "created_at": now(),
                            "task_id": config["task"]["task_id"],
                            "context_fingerprint": config["context_fingerprint"],
                            "source_digest": fingerprint({"files": files, "directories": directories}),
                            "source_paths": paths, "files": files, "directories": directories,
                            "lane": lane, "parent_id": parent_id, "hypothesis": hypothesis.strip()}
                atomic_json(staging / "manifest.json", manifest)
                for directory in sorted((p for p in source.rglob("*") if p.is_dir()),
                                        key=lambda p: len(p.parts), reverse=True):
                    sync_directory(directory)
                sync_directory(source)
                os.replace(staging, self.root / "candidates" / candidate_id)
                sync_directory(self.root / "candidates")
                return manifest
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def matches_workspace(self, candidate_id: str, workspace: str | Path) -> bool:
        manifest = self.candidate(candidate_id)
        files, directories = _source_inventory(Path(workspace).resolve(), manifest["source_paths"])
        return files == manifest["files"] and directories == manifest["directories"]

    def restore(self, candidate_id: str, destination: str | Path) -> Path:
        source = self.source(candidate_id)
        destination = Path(destination).expanduser().resolve()
        if destination.exists():
            raise ArchiveError("Restore destination must not exist; use a new workspace to avoid stale files")
        if destination == self.root or destination.is_relative_to(self.root / "candidates"):
            raise ArchiveError("Restore cannot overwrite candidate archives")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        return destination

    def evaluations(self) -> list[dict]:
        return self._records("evaluations")

    def evaluation(self, evaluation_id: str) -> dict:
        path = self.root / "evaluations" / (_identifier(evaluation_id) + ".json")
        if not path.is_file():
            raise ArchiveError(f"Unknown evaluation: {evaluation_id}")
        return load_json(path)

    def _copy_evidence(self, evidence: str | Path, target: Path) -> list[dict]:
        """Copy, hash and flush evidence before publishing its immutable record."""
        original = Path(evidence).expanduser()
        if original.is_symlink():
            raise ArchiveError("Evidence symlinks are not supported")
        original = original.resolve()
        if (original == self.root or self.root.is_relative_to(original)
                or target == original or target.is_relative_to(original)):
            raise ArchiveError("Evidence cannot contain the archive itself or its destination")
        if original.is_dir():
            before = _evidence_inventory(original)
        elif original.is_file():
            before = [{"path": original.name, "sha256": file_sha(original), "size": original.stat().st_size}]
        else:
            raise ArchiveError(f"Evidence does not exist or is not regular: {original}")
        staging = Path(tempfile.mkdtemp(prefix=".evidence-", dir=self.root))
        try:
            copied = staging / "files"
            if original.is_dir():
                shutil.copytree(original, copied)
            else:
                copied.mkdir()
                shutil.copyfile(original, copied / original.name)
            files = _evidence_inventory(copied)
            after = (_evidence_inventory(original) if original.is_dir() else
                     [{"path": original.name, "sha256": file_sha(original), "size": original.stat().st_size}])
            if files != before or after != before:
                raise ArchiveError("Evidence changed while being captured; retry after the writer finishes")
            for item in files:
                with (copied / item["path"]).open("rb") as saved:
                    os.fsync(saved.fileno())
            for directory in sorted((p for p in copied.rglob("*") if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                sync_directory(directory)
            sync_directory(copied)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(copied, target)
            for directory in (target.parent, *target.parent.parents):
                sync_directory(directory)
                if directory == self.root:
                    break
            return files
        finally:
            shutil.rmtree(staging)

    def _verify_evidence_record(self, record: dict) -> dict:
        relative = record.get("evidence_path")
        has_files, has_digest = "evidence_files" in record, "evidence_digest" in record
        if not has_files and not has_digest:
            return {"verified": False, "status": "legacy_unverified" if relative else "not_recorded",
                    "evidence_path": relative, "files": [], "evidence_digest": None}
        if not has_files or not has_digest or not relative:
            raise ArchiveError("Evidence inventory is incomplete")
        path = self.root / _relative_path(relative)
        for parent in (path, *path.parents):
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ArchiveError("Archived evidence path contains a symlink")
        expected = record["evidence_files"]
        if not isinstance(expected, list) or fingerprint(expected) != record["evidence_digest"]:
            raise ArchiveError("Archived evidence inventory digest is invalid")
        actual = _evidence_inventory(path)
        if actual != expected:
            raise ArchiveError("Archived evidence has changed or contains missing/extra files")
        return {"verified": True, "status": "verified", "evidence_path": relative,
                "files": expected, "evidence_digest": record["evidence_digest"]}

    def verify_evidence(self, evaluation_id: str) -> dict:
        """Read-only integrity check. Callers may hold the archive lock already."""
        record = self.evaluation(evaluation_id)
        verified = self._verify_evidence_record(record)
        attachments = []
        directory = self.root / "attachments" / evaluation_id
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ArchiveError("Evidence attachment directory contains a symlink")
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink():
                raise ArchiveError("Evidence attachment manifest contains a symlink")
            attachment = load_json(path)
            if attachment.get("evaluation_id") != evaluation_id or attachment.get("id") != path.stem:
                raise ArchiveError("Evidence attachment belongs to a different evaluation")
            checked = self._verify_evidence_record(attachment)
            if not checked["verified"]:
                raise ArchiveError("Evidence attachment is missing its inventory")
            attachments.append({**attachment, **checked,
                                "manifest_path": path.relative_to(self.root).as_posix()})
        return {"evaluation_id": evaluation_id, **verified, "attachments": attachments}

    def attach_evidence(self, evaluation_id: str, evidence: str | Path, label: str) -> dict:
        """Append later evidence without changing the original evaluation record."""
        if not isinstance(label, str) or not label.strip():
            raise ArchiveError("Evidence attachment needs a descriptive label")
        with self._lock():
            self.evaluation(evaluation_id)
            attachment_id = "a-" + uuid.uuid4().hex[:16]
            target = self.root / "evidence" / (evaluation_id + "-attachments") / attachment_id
            files = self._copy_evidence(evidence, target)
            record = {"schema_version": 1, "id": attachment_id, "evaluation_id": evaluation_id,
                      "created_at": now(), "label": label.strip(),
                      "evidence_path": target.relative_to(self.root).as_posix(),
                      "evidence_files": files, "evidence_digest": fingerprint(files)}
            path = self.root / "attachments" / evaluation_id / (attachment_id + ".json")
            atomic_json(path, record)
            sync_directory(path.parent.parent)
            sync_directory(self.root)
            return {**record, "manifest_path": path.relative_to(self.root).as_posix()}

    def record_evaluation(self, candidate_id: str, result: dict, *, kind: str = "search",
                          evidence: str | Path | None = None) -> dict:
        manifest = self.candidate(candidate_id)
        self.source(candidate_id)
        if kind not in ("search", "confirmation"):
            raise ArchiveError("Evaluation kind must be search or confirmation")
        for key in ("source_digest", "context_fingerprint"):
            if result.get(key) != manifest[key]:
                raise ArchiveError(f"Evaluation {key} does not match the captured candidate")
        status = result.get("status")
        if status not in ("completed", "failed", "infra_error") or type(result.get("valid")) is not bool:
            raise ArchiveError("Evaluation requires status completed/failed/infra_error and a boolean valid")
        if result["valid"] and status != "completed":
            raise ArchiveError("Only a completed evaluation may be valid")
        metrics = result.get("metrics", {})
        if not isinstance(metrics, dict):
            raise ArchiveError("Evaluation metrics must be a dictionary")
        for key, value in metrics.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ArchiveError(f"Metric {key!r} must be a finite number")
        objective = self.config["task"]["objective"]
        if result["valid"] and objective not in metrics:
            raise ArchiveError(f"Valid evaluation is missing objective: {objective}")
        harness_id, round_id = result.get("harness_run_id"), result.get("round_id")
        if bool(harness_id) != bool(round_id):
            raise ArchiveError("harness_run_id and round_id must be recorded together")
        if not result.get("eval_run_id") and not harness_id:
            raise ArchiveError("Provide eval_run_id or the bound Harness run/round identity")
        execution = _execution(result.get("execution"))
        if execution and kind == "search" and execution["confirmation_of"] is not None:
            raise ArchiveError("A search execution cannot confirm another evaluation")
        with self._lock():
            if harness_id:
                binding = self.binding(harness_id, round_id)
                if binding["candidate_id"] != candidate_id or binding["kind"] != kind:
                    raise ArchiveError("Harness round binding disagrees with candidate or evaluation kind")
                if (execution and "confirmation_of" in binding
                        and execution["confirmation_of"] != binding["confirmation_of"]):
                    raise ArchiveError("Harness execution disagrees with the submission's confirmation_of")
            for previous in self.evaluations():
                same_eval = result.get("eval_run_id") and previous.get("eval_run_id") == result["eval_run_id"]
                same_round = harness_id and (previous.get("harness_run_id"), previous.get("round_id")) == (harness_id, round_id)
                same_execution = execution and (previous.get("execution") or {}).get("id") == execution["id"]
                if same_eval or same_round or same_execution:
                    raise ArchiveError("This evaluation execution is already recorded")
            evaluation_id = "e-" + uuid.uuid4().hex[:16]
            record = {**json.loads(canonical(result)), "schema_version": 1, "id": evaluation_id,
                      "candidate_id": candidate_id, "kind": kind, "created_at": now()}
            if evidence:
                target = self.root / "evidence" / evaluation_id
                files = self._copy_evidence(evidence, target)
                record["evidence_path"] = target.relative_to(self.root).as_posix()
                record["evidence_files"] = files
                record["evidence_digest"] = fingerprint(files)
            atomic_json(self.root / "evaluations" / (evaluation_id + ".json"), record)
            return record

    def best(self) -> dict | None:
        task = self.config["task"]
        eligible = [record for record in self.evaluations()
                    if record["kind"] == "search" and record["status"] == "completed" and record["valid"]]
        if not eligible:
            return None
        # Stable ties retain the earliest observation. Confirmation results never enter search ranking.
        select = max if task["direction"] == "maximize" else min
        record = select(eligible, key=lambda row: row["metrics"][task["objective"]])
        return {"candidate": self.candidate(record["candidate_id"]), "evaluation": record}

    def begin_submission(self, candidate_id: str, harness_run_id: str, *, kind: str = "search",
                         confirmation_of: str | None = None) -> dict:
        manifest = self.candidate(candidate_id)
        _identifier(harness_run_id)
        if kind not in ("search", "confirmation"):
            raise ArchiveError("Unknown evaluation kind")
        if kind == "search" and confirmation_of is not None:
            raise ArchiveError("Only a confirmation submission may specify confirmation_of")
        with self._lock():
            if any(s["harness_run_id"] == harness_run_id and s["status"] == "pending"
                   for s in self._records("submissions")):
                raise ArchiveError("This Harness run has an unresolved submission; recover its round first")
            created_at = now()
            if kind == "confirmation":
                eligible = [row for row in self.evaluations()
                            if row["candidate_id"] == candidate_id and row["kind"] == "search"
                            and row["status"] == "completed" and row["valid"]
                            and row["source_digest"] == manifest["source_digest"]
                            and row["context_fingerprint"] == manifest["context_fingerprint"]]
                if confirmation_of is not None:
                    _identifier(confirmation_of)
                    eligible = [row for row in eligible if row["id"] == confirmation_of]
                if not eligible:
                    raise ArchiveError("Confirmation requires an existing valid search of the same candidate/context")
                search = eligible[-1]
                if _timestamp(search["created_at"], "search.created_at") > _timestamp(created_at, "submission.created_at"):
                    raise ArchiveError("The search evaluation must exist before the confirmation submission")
                confirmation_of = search["id"]
            record = {"id": "s-" + uuid.uuid4().hex[:16], "created_at": created_at,
                      "candidate_id": candidate_id, "harness_run_id": harness_run_id,
                      "round_id": None, "status": "pending", "kind": kind,
                      "confirmation_of": confirmation_of,
                      "source_digest": manifest["source_digest"],
                      "context_fingerprint": manifest["context_fingerprint"]}
            atomic_json(self.root / "submissions" / (record["id"] + ".json"), record)
            return record

    def submission(self, submission_id: str) -> dict:
        return load_json(self.root / "submissions" / (_identifier(submission_id) + ".json"))

    def bind_submission(self, submission_id: str, round_id: str, *, note: str,
                        feedback: str | None = None) -> dict:
        _identifier(round_id)
        if not note.strip():
            raise ArchiveError("Record how this round was identified")
        with self._lock():
            record = self.submission(submission_id)
            if record["status"] == "bound":
                if record["round_id"] == round_id:
                    return record
                raise ArchiveError("Submission is already bound to a different round")
            for other in self._records("submissions"):
                if other["harness_run_id"] == record["harness_run_id"] and other["round_id"] == round_id:
                    raise ArchiveError("Harness round is already bound to another submission")
            record.update(status="bound", round_id=round_id, bound_at=now(), binding_note=note)
            if feedback is not None:
                path = self.root / "evidence" / (submission_id + "-submit.txt")
                path.write_text(feedback, encoding="utf-8")
                record["feedback_path"] = path.relative_to(self.root).as_posix()
            atomic_json(self.root / "submissions" / (submission_id + ".json"), record)
            return record

    def binding(self, harness_run_id: str, round_id: str) -> dict:
        matches = [record for record in self._records("submissions")
                   if record["harness_run_id"] == harness_run_id and record["round_id"] == round_id
                   and record["status"] == "bound"]
        if len(matches) != 1:
            raise ArchiveError("No unique candidate binding for this Harness run/round")
        return matches[0]

    def promote(self, candidate_id: str, search_evaluation: str, confirmation_evaluation: str) -> dict:
        manifest = self.candidate(candidate_id)
        self.source(candidate_id)
        search = self.evaluation(search_evaluation)
        confirmation = self.evaluation(confirmation_evaluation)
        for record, kind in ((search, "search"), (confirmation, "confirmation")):
            if (record["candidate_id"] != candidate_id or record["kind"] != kind
                    or record["status"] != "completed" or not record["valid"]
                    or record["source_digest"] != manifest["source_digest"]
                    or record["context_fingerprint"] != manifest["context_fingerprint"]):
                raise ArchiveError("Promotion needs valid search and fresh confirmation of the same candidate/context")
        search_execution = _execution(search.get("execution"))
        confirmation_execution = _execution(confirmation.get("execution"))
        if not search_execution or not confirmation_execution:
            raise ArchiveError("Promotion requires execution metadata; legacy records cannot establish fresh confirmation")
        if (confirmation_execution["confirmation_of"] != search_evaluation
                or search_execution["confirmation_of"] is not None):
            raise ArchiveError("Confirmation must identify the exact selected search evaluation in confirmation_of")
        confirmation_start = _timestamp(confirmation_execution["started_at"], "confirmation.started_at")
        if (confirmation_start < _timestamp(search_execution["finished_at"], "search.finished_at")
                or confirmation_start < _timestamp(search["created_at"], "search.created_at")):
            raise ArchiveError("Confirmation execution must follow both search completion and its recorded result")
        if search_execution["id"] == confirmation_execution["id"]:
            raise ArchiveError("Search and confirmation must be different executions")
        task = self.config["task"]
        with self._lock():
            for evaluation_id in (search_evaluation, confirmation_evaluation):
                if not self.verify_evidence(evaluation_id)["verified"]:
                    raise ArchiveError("Promotion requires verified retained evidence for search and confirmation")
            current_path = self.root / "CURRENT.json"
            existing = [row for row in self._records("releases")
                        if row["candidate_id"] == candidate_id and row["search_evaluation"] == search_evaluation
                        and row["confirmation_evaluation"] == confirmation_evaluation]
            if existing and current_path.exists() and load_json(current_path)["release_id"] == existing[-1]["id"]:
                return existing[-1]
            if current_path.exists():
                previous = load_json(self.root / "releases" / (load_json(current_path)["release_id"] + ".json"))
                prior_value = self.evaluation(previous["confirmation_evaluation"])["metrics"][task["objective"]]
                next_value = confirmation["metrics"][task["objective"]]
                improved = next_value > prior_value if task["direction"] == "maximize" else next_value < prior_value
                if not improved:
                    raise ArchiveError("Confirmation does not improve the current release's objective")
            record = existing[-1] if existing else {"schema_version": 1, "id": "r-" + uuid.uuid4().hex[:16],
                      "created_at": now(), "candidate_id": candidate_id,
                      "source_digest": manifest["source_digest"],
                      "context_fingerprint": manifest["context_fingerprint"],
                      "search_evaluation": search_evaluation,
                      "confirmation_evaluation": confirmation_evaluation}
            if not existing:
                atomic_json(self.root / "releases" / (record["id"] + ".json"), record)
            atomic_json(current_path, {"release_id": record["id"], "candidate_id": candidate_id})
            return record

    def summary(self) -> dict:
        best = self.best()
        current = self.root / "CURRENT.json"
        evaluations = self.evaluations()
        return {"task": self.config["task"], "context_fingerprint": self.config["context_fingerprint"],
                "candidate_count": len(self.candidates()), "evaluation_count": len(evaluations),
                "valid_evaluation_count": sum(e["valid"] for e in evaluations),
                "pending_submissions": [s for s in self._records("submissions") if s["status"] == "pending"],
                "best": best, "current": load_json(current) if current.exists() else None}

    def write_views(self) -> dict:
        with self._lock():
            summary = self.summary()
            atomic_json(self.root / "STATUS.json", summary)
            lines = ["# Research journal", "", f"Task: `{summary['task']['task_id']}`", "",
                     "Generated from immutable candidate/evaluation records. Raw evidence remains linked below.", ""]
            evaluations = self.evaluations()
            for candidate in self.candidates():
                lines += [f"## {candidate['id']} · {candidate['lane']}", "",
                          f"Parent: `{candidate['parent_id']}` · source: `{candidate['source_digest']}`", "",
                          candidate["hypothesis"], ""]
                for result in evaluations:
                    if result["candidate_id"] == candidate["id"]:
                        lines += [f"- `{result['id']}` ({result['kind']}): {result['status']}; valid={result['valid']}; "
                                  f"metrics={json.dumps(result['metrics'], ensure_ascii=False)}; "
                                  f"evidence={result.get('evidence_path', 'external report')}"]
                lines.append("")
            # This derived view can always be regenerated if interrupted.
            (self.root / "JOURNAL.md").write_text("\n".join(lines), encoding="utf-8")
            return summary
