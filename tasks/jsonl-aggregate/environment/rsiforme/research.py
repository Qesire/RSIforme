"""A persistent, budgeted single-controller research loop.

The worker is an ordinary argv process (for example a configured coding Agent).
It receives the real parent, profiling and evaluation history. Evaluation stays
with the frozen task. Scheduler/containers remain the execution backend's job.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import uuid

from .archive import Archive, ArchiveError, atomic_json, canonical, file_sha, fingerprint, load_json, now, _identifier, _execution
from .workflow import evaluate_local, submit


TERMINAL = {"completed", "worker_failed", "abandoned", "stopped"}
PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": ["evaluate", "stop"]},
        "hypothesis": {"type": "string"}, "change_summary": {"type": "string"},
        "evidence_used": {"type": "array", "items": {"type": "string"}},
        "next_direction": {"type": "string"},
    },
    "required": ["decision", "hypothesis", "change_summary", "evidence_used", "next_direction"],
}


def _seconds_since(timestamp: str) -> float:
    return max(0.0, (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(timestamp)).total_seconds())


def _process_identity(pid: int) -> str | None:
    """Linux start time avoids confusing a reused PID with our worker."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class ResearchController:
    def __init__(self, archive: Archive):
        self.archive = archive
        self.root = archive.root / "research"

    @contextlib.contextmanager
    def _runner_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".runner.lock").open("a") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ArchiveError("A controller already owns this research session") from exc
            yield

    @property
    def config(self) -> dict:
        config = load_json(self.root / "config.json")
        if fingerprint(config["program"]) != config["program_fingerprint"]:
            raise ArchiveError("Frozen research program has changed")
        if config["context_fingerprint"] != self.archive.config["context_fingerprint"]:
            raise ArchiveError("Research session task/context changed")
        if file_sha(self.root / "profile.json") != config["profile_sha256"]:
            raise ArchiveError("Baseline profile changed")
        return config

    def initialize(self, program: dict, *, baseline_evaluation: str, profile: str | Path) -> dict:
        program = json.loads(canonical(program))
        if program.get("schema_version") != 1:
            raise ArchiveError("Research program schema_version must be 1")
        budgets = program.get("budgets", {})
        for key in ("max_trials", "max_evaluations", "walltime_seconds", "worker_timeout_seconds"):
            if type(budgets.get(key)) is not int or budgets[key] <= 0:
                raise ArchiveError(f"Positive integer program.budgets.{key} is required")
        if budgets["max_evaluations"] < 2:
            raise ArchiveError("Budget must include a baseline and a separate confirmation")
        command = program.get("worker", {}).get("argv")
        if not isinstance(command, list) or not command or not all(isinstance(s, str) and s for s in command):
            raise ArchiveError("program.worker.argv must be an explicit argument list")
        lanes = program.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            raise ArchiveError("At least one research lane is required")
        seen = set()
        for lane in lanes:
            _identifier(lane["id"])
            if lane["id"] in seen or not isinstance(lane.get("goal"), str) or not lane["goal"].strip():
                raise ArchiveError("Research lanes need unique IDs and concrete goals")
            seen.add(lane["id"])
        backend = program.setdefault("backend", {"type": "local"})
        if backend.get("type") not in ("local", "harness"):
            raise ArchiveError("Research backend must be local or harness")
        if backend["type"] == "harness" and not (backend.get("workspace") and backend.get("harness_run_id")):
            raise ArchiveError("Harness backend needs its actual Work path and run ID")
        baseline = self.archive.evaluation(baseline_evaluation)
        if baseline["kind"] != "search" or not baseline["valid"] or baseline["status"] != "completed":
            raise ArchiveError("Research must start from a measured, valid baseline")
        baseline_execution = _execution(baseline.get("execution"))
        if baseline_execution is None or baseline_execution["confirmation_of"] is not None:
            raise ArchiveError("Research baseline needs search execution provenance; rerun a legacy baseline")
        verified = self.archive.verify_evidence(baseline["id"])
        if not verified["verified"]:
            raise ArchiveError("Baseline evidence must be sealed before research")
        source = self.archive.candidate(baseline["candidate_id"])
        profile = Path(profile).resolve()
        profile_data = load_json(profile)
        if (profile_data.get("source_digest") != source["source_digest"]
                or profile_data.get("context_fingerprint") != source["context_fingerprint"]):
            raise ArchiveError("Profile is not bound to the measured baseline/source context")
        if not profile_data.get("command") or not profile_data.get("findings"):
            raise ArchiveError("Baseline profile needs its actual command and observed findings")
        with self._runner_lock():
            if (self.root / "config.json").exists():
                raise ArchiveError("Research session already initialized; resume it instead")
            (self.root / "trials").mkdir(exist_ok=True)
            shutil.copyfile(profile, self.root / "profile.json")
            config = {"schema_version": 1, "id": "research-" + uuid.uuid4().hex[:16],
                      "created_at": now(), "program": program, "program_fingerprint": fingerprint(program),
                      "context_fingerprint": source["context_fingerprint"],
                      "baseline_evaluation": baseline["id"], "baseline_candidate": source["id"],
                      "profile_sha256": file_sha(self.root / "profile.json")}
            atomic_json(self.root / "config.json", config)
            self._views()
            return config

    def trials(self) -> list[dict]:
        return [load_json(path) for path in sorted((self.root / "trials").glob("*/state.json"))]

    def trial(self, trial_id: str) -> dict:
        return load_json(self.root / "trials" / _identifier(trial_id) / "state.json")

    def _save(self, trial: dict, phase: str | None = None, **updates) -> dict:
        if phase is not None:
            trial["phase"] = phase
        trial.update(updates, updated_at=now())
        atomic_json(self.root / "trials" / trial["id"] / "state.json", trial)
        self._views()
        return trial

    def status(self) -> dict:
        config = self.config
        trials = self.trials()
        budgets = config["program"]["budgets"]
        pending = [t for t in trials if t["phase"] not in TERMINAL]
        evaluations = self.archive.evaluations()
        evaluation_count = len(evaluations)
        recorded_executions = {(row.get("execution") or {}).get("id") for row in evaluations}
        evaluated_candidates = {row["candidate_id"] for row in evaluations if row["kind"] == "search"}
        abandoned_evaluations = sum(bool(t.get("evaluation_dispatch_abandoned"))
                                    and t["evaluation_request_id"] not in recorded_executions
                                    and t.get("candidate_id") not in evaluated_candidates for t in trials)
        reserved = sum(t.get("evaluation_id") is None for t in pending)
        remaining = max(0, budgets["walltime_seconds"] - _seconds_since(config["created_at"]))
        finalization = self.root / "finalization.json"
        final = load_json(finalization) if finalization.exists() else None
        return {"research_id": config["id"], "context_fingerprint": config["context_fingerprint"],
                "trials": trials, "pending_trials": [t["id"] for t in pending],
                "budget": {**budgets, "trials_started": len(trials), "evaluations_recorded": evaluation_count,
                           "evaluations_abandoned": abandoned_evaluations,
                           "evaluations_consumed": evaluation_count + abandoned_evaluations,
                           "evaluations_reserved": reserved, "confirmation_reserved": 0 if final and final.get("evaluation_id") else 1,
                           "remaining_wall_seconds": remaining},
                "finalization": final}

    def _views(self) -> None:
        atomic_json(self.root / "STATUS.json", self.status())

    def _parent(self, explicit: str | None) -> tuple[dict, dict]:
        if explicit is None:
            best = self.archive.best()
            if best is None:
                raise ArchiveError("No valid parent candidate")
            return best["candidate"], best["evaluation"]
        candidate = self.archive.candidate(explicit)
        records = [r for r in self.archive.evaluations() if r["candidate_id"] == explicit
                   and r["kind"] == "search" and r["valid"] and r["status"] == "completed"]
        if not records:
            raise ArchiveError("Explicit research parent needs a valid search evaluation")
        return candidate, records[-1]

    def _prepare(self, *, lane_id: str | None = None, parent_id: str | None = None) -> dict:
        status = self.status()
        if status["pending_trials"]:
            raise ArchiveError("Resume or resolve the existing trial before starting another")
        if status["finalization"]:
            raise ArchiveError("Research is already in finalization")
        budget = status["budget"]
        if (budget["trials_started"] >= budget["max_trials"] or budget["remaining_wall_seconds"] <= 0
                or budget["evaluations_consumed"] + 2 > budget["max_evaluations"]):
            raise ArchiveError("Research budget exhausted; one evaluation is reserved for confirmation")
        config = self.config
        lanes = config["program"]["lanes"]
        if lane_id:
            selected = [lane for lane in lanes if lane["id"] == lane_id]
            if len(selected) != 1:
                raise ArchiveError("Unknown research lane")
            lane = selected[0]
        else:
            lane = lanes[len(status["trials"]) % len(lanes)]
        parent, parent_evaluation = self._parent(parent_id)
        if not self.archive.verify_evidence(parent_evaluation["id"])["verified"]:
            raise ArchiveError("Research parent needs sealed evaluation evidence")
        if _execution(parent_evaluation.get("execution")) is None:
            raise ArchiveError("Research parent needs execution provenance")
        trial_id = f"trial-{len(status['trials']) + 1:04d}"
        trial_root = self.root / "trials" / trial_id
        work = trial_root / "work"
        handoff = {"schema_version": 1, "research_id": config["id"], "trial_id": trial_id,
                   "task": self.archive.config["task"], "lane": lane, "budget": budget,
                   "parent": parent, "parent_evaluation": parent_evaluation,
                   "baseline_evaluation": self.archive.evaluation(config["baseline_evaluation"]),
                   "profile": load_json(self.root / "profile.json"),
                   "history": {"trials": status["trials"], "candidates": self.archive.candidates(),
                               "evaluations": self.archive.evaluations()},
                   "source_paths": self.archive.config["task"]["source_paths"],
                   "archive_read_path": str(self.archive.root),
                   "instructions": config["program"].get("instructions", "")}
        # The immutable handoff lives outside the editable worker directory. An
        # interrupted restore can be rebuilt without changing its chosen history.
        atomic_json(trial_root / "handoff.json", handoff)
        trial = {"schema_version": 1, "id": trial_id, "created_at": now(), "phase": "preparing",
                 "lane": lane["id"], "goal": lane["goal"], "parent_id": parent["id"],
                 "parent_evaluation": parent_evaluation["id"], "workspace": str(work),
                 "handoff_sha256": file_sha(trial_root / "handoff.json"),
                 "selection": {"policy": "explicit" if parent_id else "best-valid-search",
                               "reason": "Start from a measured valid candidate under the same frozen contract"},
                 "evaluation_request_id": config["id"] + "/" + trial_id,
                 "evaluation_id": None}
        self._save(trial)
        return self._materialize(trial)

    def _materialize(self, trial: dict) -> dict:
        """Only a persisted, not-yet-started preparation may rebuild its work."""
        work = self.root / "trials" / trial["id"] / "work"
        handoff = work.parent / "handoff.json"
        if trial["workspace"] != str(work) or work.is_symlink():
            raise ArchiveError("Research workspace differs from its controller-owned path")
        if file_sha(handoff) != trial["handoff_sha256"]:
            raise ArchiveError("Frozen preparation handoff changed")
        if work.exists():
            if not work.is_dir():
                raise ArchiveError("Research workspace is not a directory")
            shutil.rmtree(work)
        self.archive.restore(trial["parent_id"], work)
        shutil.copyfile(handoff, work / "handoff.json")
        atomic_json(work / "proposal.schema.json", PROPOSAL_SCHEMA)
        return self._save(trial, "prepared")

    def prepare(self, *, lane_id: str | None = None, parent_id: str | None = None) -> dict:
        with self._runner_lock():
            return self._prepare(lane_id=lane_id, parent_id=parent_id)

    def _worker(self, trial: dict) -> dict:
        work = Path(trial["workspace"])
        if file_sha(work / "handoff.json") != trial["handoff_sha256"]:
            raise ArchiveError("Worker handoff changed after preparation")
        replacements = {"{workspace}": str(work), "{handoff}": str(work / "handoff.json"),
                        "{proposal}": str(work / "proposal.json"), "{schema}": str(work / "proposal.schema.json")}
        argv = []
        for value in self.config["program"]["worker"]["argv"]:
            for placeholder, replacement in replacements.items():
                value = value.replace(placeholder, replacement)
            argv.append(value)
        timeout = min(self.config["program"]["budgets"]["worker_timeout_seconds"],
                      self.status()["budget"]["remaining_wall_seconds"])
        if timeout <= 0:
            return self._save(trial, "stopped", decision="walltime_exhausted_before_worker")
        prompt = ("Read handoff.json and the candidate source in this working directory. "
                  "Use its profiling, parent and prior evaluation evidence to pursue the current lane goal. "
                  "Modify only the declared source paths; do not modify handoff.json, the evaluator, archive, "
                  "or other fixture implementations. Perform useful local checks if needed. "
                  "Return a JSON proposal conforming to proposal.schema.json, with decision=evaluate or stop. "
                  "State actual evidence used and the change; do not invent measurements. "
                  "Save the proposal to proposal.json if your caller does not save the final response.\n"
                  + self.config["program"].get("instructions", ""))
        trial_root = work.parent
        (trial_root / "worker-prompt.txt").write_text(prompt, encoding="utf-8")
        self._save(trial, "worker_starting", worker_argv=argv, worker_started_at=now())
        started = time.monotonic()
        with (trial_root / "worker.stdout.log").open("w") as stdout, (trial_root / "worker.stderr.log").open("w") as stderr:
            try:
                process = subprocess.Popen(argv, cwd=work, stdin=subprocess.PIPE, stdout=stdout,
                                           stderr=stderr, text=True, start_new_session=True)
            except OSError as exc:
                return self._save(trial, "worker_failed", worker_error=str(exc), worker_returncode=None)
            self._save(trial, "worker_running", worker_pid=process.pid,
                       worker_process_identity=_process_identity(process.pid))
            try:
                process.communicate(prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                return self._save(trial, "worker_failed", worker_error="worker timeout",
                                  worker_returncode=process.returncode, worker_seconds=time.monotonic() - started)
        if process.returncode:
            return self._save(trial, "worker_failed", worker_error="worker exited unsuccessfully",
                              worker_returncode=process.returncode, worker_seconds=time.monotonic() - started)
        return self._save(trial, "worker_complete", worker_returncode=0,
                          worker_seconds=time.monotonic() - started, worker_finished_at=now())

    def _evaluator(self) -> Path:
        baseline = self.archive.evaluation(self.config["baseline_evaluation"])
        self.archive.verify_evidence(baseline["id"])
        reproduction = baseline.get("reproduction", {})
        if reproduction.get("adapter") != "local-python":
            raise ArchiveError("Local backend needs a retained local evaluator bundle")
        return self.archive.root / baseline["evidence_path"] / reproduction["entrypoint"]

    def _install_native_source(self, candidate_id: str) -> Path:
        backend = self.config["program"]["backend"]
        workspace = Path(backend["workspace"]).resolve()
        source = self.archive.source(candidate_id)
        # This is an explicitly configured Work. Scope is fixed in task.json.
        # Every previous submitted version is already retained in the archive.
        for relative in self.archive.config["task"]["source_paths"]:
            target = workspace / relative
            if target == self.archive.root or self.archive.root.is_relative_to(target):
                raise ArchiveError("Native source installation would overwrite the research archive")
            if target.is_symlink():
                raise ArchiveError("Native source target must not be a symlink")
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if (source / relative).is_dir():
                shutil.copytree(source / relative, target)
            else:
                shutil.copy2(source / relative, target)
        return workspace

    def _settle(self, trial: dict, evaluation: dict) -> dict:
        if not evaluation["valid"]:
            decision = "infra_error" if evaluation["status"] == "infra_error" else "invalid"
        else:
            objective = self.archive.config["task"]["objective"]
            old = self.archive.evaluation(trial["parent_evaluation"])["metrics"][objective]
            new = evaluation["metrics"][objective]
            improves = new > old if self.archive.config["task"]["direction"] == "maximize" else new < old
            decision = "improved" if improves else "not_improved"
        return self._save(trial, "completed", evaluation_id=evaluation["id"],
                          decision=decision, completed_at=now())

    def _advance(self, trial: dict) -> dict:
        if trial["phase"] in TERMINAL:
            return trial
        if trial["phase"] in ("worker_starting", "worker_running"):
            raise ArchiveError("Worker outcome is unresolved; inspect logs and resolve this trial, never launch a duplicate")
        if trial["phase"] == "preparing":
            trial = self._materialize(trial)
        if trial["phase"] == "prepared":
            budget = self.status()["budget"]
            if budget["evaluations_consumed"] + 2 > budget["max_evaluations"]:
                return self._save(trial, "stopped", decision="evaluation_budget_exhausted_before_worker")
            trial = self._worker(trial)
        if trial["phase"] in TERMINAL:
            return trial
        if trial["phase"] == "worker_complete":
            try:
                proposal = load_json(Path(trial["workspace"]) / "proposal.json")
                if proposal.get("decision") not in ("evaluate", "stop") or not proposal.get("hypothesis"):
                    raise ArchiveError("Worker proposal needs a decision and a concrete hypothesis")
                if not isinstance(proposal.get("evidence_used"), list) or not proposal["evidence_used"]:
                    raise ArchiveError("Worker proposal must identify evidence used from this handoff")
            except (OSError, ValueError) as exc:
                return self._save(trial, "worker_failed", worker_error=f"Invalid proposal: {exc}")
            if file_sha(Path(trial["workspace"]) / "handoff.json") != trial["handoff_sha256"]:
                return self._save(trial, "worker_failed", worker_error="Worker altered the frozen handoff")
            if proposal["decision"] == "stop":
                return self._save(trial, "stopped", proposal=proposal, decision="worker_stopped")
            try:
                candidate = self.archive.capture(trial["workspace"], lane=trial["lane"],
                                                 parent_id=trial["parent_id"], hypothesis=proposal["hypothesis"])
            except (ArchiveError, OSError) as exc:
                return self._save(trial, "worker_failed", proposal=proposal,
                                  worker_error=f"Candidate capture failed: {type(exc).__name__}: {exc}")
            self._save(trial, "captured", candidate_id=candidate["id"], proposal=proposal)
            if candidate["source_digest"] == self.archive.candidate(trial["parent_id"])["source_digest"]:
                return self._save(trial, "completed", decision="unchanged_source",
                                  reused_evaluation_id=trial["parent_evaluation"])
        if trial["phase"] == "evaluating":
            records = [r for r in self.archive.evaluations()
                       if (r.get("execution") or {}).get("id") == trial["evaluation_request_id"]
                       or (r["candidate_id"] == trial["candidate_id"] and r["kind"] == "search")]
            if len(records) == 1:
                return self._settle(trial, records[0])
            raise ArchiveError("Evaluation outcome is unresolved; reconcile saved evidence before another evaluation")
        if trial["phase"] == "captured":
            budget = self.status()["budget"]
            if budget["remaining_wall_seconds"] <= 0:
                return self._save(trial, "stopped", decision="walltime_exhausted_before_evaluation")
            if budget["evaluations_consumed"] + 2 > budget["max_evaluations"]:
                return self._save(trial, "stopped", decision="evaluation_budget_exhausted_before_evaluation")
            self._save(trial, "evaluating", evaluation_started_at=now())
            backend = self.config["program"]["backend"]
            if backend["type"] == "local":
                evaluation = evaluate_local(self.archive, trial["candidate_id"], self._evaluator(),
                                            execution_id=trial["evaluation_request_id"],
                                            timeout_seconds=budget["remaining_wall_seconds"])
            else:
                workspace = self._install_native_source(trial["candidate_id"])
                remaining = self.status()["budget"]["remaining_wall_seconds"]
                if remaining <= 0:
                    return self._save(trial, "stopped", decision="walltime_exhausted_before_submission")
                evaluation = submit(self.archive, workspace, harness_run_id=backend["harness_run_id"],
                                    candidate_id=trial["candidate_id"], timeout_seconds=remaining)["evaluation"]
            return self._settle(trial, evaluation)
        return trial

    def run(self, *, max_new_trials: int | None = None) -> dict:
        """Continue from saved phases. Each worker receives the latest measured history."""
        with self._runner_lock():
            started = 0
            while True:
                pending = [t for t in self.trials() if t["phase"] not in TERMINAL]
                if pending:
                    self._advance(pending[0])
                else:
                    budget = self.status()["budget"]
                    if (max_new_trials is not None and started >= max_new_trials) or self.status()["finalization"]:
                        break
                    if (budget["trials_started"] >= budget["max_trials"] or budget["remaining_wall_seconds"] <= 0
                            or budget["evaluations_consumed"] + 2 > budget["max_evaluations"]):
                        break
                    self._prepare()
                    started += 1
            self.archive.write_views()
            self._views()
            return self.status()

    def resolve(self, trial_id: str, *, note: str, accept_worker_output: bool = False,
                abandon_unknown_evaluation: bool = False) -> dict:
        """Explicit reconciliation for an interrupted worker; never silently repeat it."""
        if not note.strip():
            raise ArchiveError("Recovery must identify the inspected evidence")
        with self._runner_lock():
            trial = self.trial(trial_id)
            if trial["phase"] == "evaluating":
                if abandon_unknown_evaluation:
                    recorded = [r for r in self.archive.evaluations()
                                if (r.get("execution") or {}).get("id") == trial["evaluation_request_id"]
                                or (r["candidate_id"] == trial["candidate_id"] and r["kind"] == "search")]
                    if recorded:
                        return self._advance(trial)
                    backend = self.config["program"]["backend"]
                    if backend["type"] == "harness" and any(
                            s["harness_run_id"] == backend["harness_run_id"] and s["status"] == "pending"
                            for s in self.archive._records("submissions")):
                        raise ArchiveError("Recover the native pending submission before abandoning its evaluation")
                    # The explicit caller must have inspected execution evidence
                    # and established that no job remains active. Keep this debit
                    # even though no evaluation result can be recovered.
                    return self._save(trial, "abandoned", decision="unknown_evaluation_abandoned",
                                      evaluation_dispatch_abandoned=True, recovery_note=note, recovered_at=now())
                return self._advance(trial)
            if abandon_unknown_evaluation:
                raise ArchiveError("Only an unresolved evaluation can use abandon_unknown_evaluation")
            if trial["phase"] not in ("worker_starting", "worker_running"):
                raise ArchiveError("Trial does not have an unresolved worker")
            pid = trial.get("worker_pid")
            if pid and trial.get("worker_process_identity") and _process_identity(pid) == trial["worker_process_identity"]:
                raise ArchiveError("The original worker is still running; do not duplicate or abandon it")
            phase = "worker_complete" if accept_worker_output else "abandoned"
            return self._save(trial, phase, recovery_note=note, recovered_at=now())

    def finalize(self) -> dict:
        """Restore the selected source, reserve a fresh confirmation, and promote."""
        with self._runner_lock():
            if self.status()["pending_trials"]:
                raise ArchiveError("Resolve pending trials before final confirmation")
            path = self.root / "finalization.json"
            if path.exists():
                state = load_json(path)
                if state["phase"] in ("promoted", "rejected"):
                    return state
            else:
                budget = self.status()["budget"]
                if budget["evaluations_consumed"] >= budget["max_evaluations"] or budget["remaining_wall_seconds"] <= 0:
                    raise ArchiveError("No remaining confirmation budget")
                best = self.archive.best()
                if best is None:
                    raise ArchiveError("No valid candidate to confirm")
                state = {"phase": "preparing", "created_at": now(), "candidate_id": best["candidate"]["id"],
                         "search_evaluation": best["evaluation"]["id"],
                         "execution_id": self.config["id"] + "/confirmation",
                         "workspace": str(self.root / "confirmed-source"), "dispatched": False}
                atomic_json(path, state)

            if not state.get("dispatched", True):
                budget = self.status()["budget"]
                if budget["evaluations_consumed"] >= budget["max_evaluations"] or budget["remaining_wall_seconds"] <= 0:
                    raise ArchiveError("No remaining confirmation budget")
                restored = self.root / "confirmed-source"
                if state["workspace"] != str(restored) or restored.is_symlink():
                    raise ArchiveError("Confirmation workspace differs from its controller-owned path")
                if restored.exists():
                    if not restored.is_dir():
                        raise ArchiveError("Confirmation workspace is not a directory")
                    shutil.rmtree(restored)
                self.archive.restore(state["candidate_id"], restored)
                backend = self.config["program"]["backend"]
                evaluator = self._evaluator() if backend["type"] == "local" else None
                workspace = self._install_native_source(state["candidate_id"]) if backend["type"] == "harness" else None
                budget = self.status()["budget"]
                if budget["evaluations_consumed"] >= budget["max_evaluations"] or budget["remaining_wall_seconds"] <= 0:
                    raise ArchiveError("No remaining confirmation budget")
                state.update(phase="confirming", dispatched=True, dispatched_at=now())
                atomic_json(path, state)
                if backend["type"] == "local":
                    confirmation = evaluate_local(self.archive, state["candidate_id"], evaluator,
                                                  kind="confirmation", workspace=restored,
                                                  execution_id=state["execution_id"], confirmation_of=state["search_evaluation"],
                                                  timeout_seconds=budget["remaining_wall_seconds"])
                else:
                    confirmation = submit(self.archive, workspace, harness_run_id=backend["harness_run_id"],
                                          candidate_id=state["candidate_id"], kind="confirmation",
                                          confirmation_of=state["search_evaluation"],
                                          timeout_seconds=budget["remaining_wall_seconds"])["evaluation"]
            else:
                records = [r for r in self.archive.evaluations()
                           if (r.get("execution") or {}).get("id") == state["execution_id"]
                           or (r["candidate_id"] == state["candidate_id"] and r["kind"] == "confirmation"
                               and (r.get("execution") or {}).get("confirmation_of") == state["search_evaluation"])]
                if len(records) != 1:
                    raise ArchiveError("Confirmation outcome is unresolved; inspect its existing evidence before retrying")
                confirmation = records[0]
            state["evaluation_id"] = confirmation["id"]
            state["phase"] = "confirmed"
            atomic_json(path, state)
            if confirmation["valid"] and confirmation["status"] == "completed":
                releases = [r for r in self.archive._records("releases")
                            if r["candidate_id"] == state["candidate_id"]
                            and r["search_evaluation"] == state["search_evaluation"]
                            and r["confirmation_evaluation"] == confirmation["id"]]
                if releases:
                    # Finish the release -> CURRENT transaction if the original
                    # process died between those two durable file writes.
                    release = self.archive.promote(state["candidate_id"], state["search_evaluation"], confirmation["id"])
                    state.update(phase="promoted", release_id=release["id"])
                else:
                    try:
                        release = self.archive.promote(state["candidate_id"], state["search_evaluation"], confirmation["id"])
                    except ArchiveError as exc:
                        if "does not improve" not in str(exc):
                            raise
                        state.update(phase="rejected", reason=f"Promotion gate rejected confirmation: {exc}")
                    else:
                        state.update(phase="promoted", release_id=release["id"])
            else:
                state.update(phase="rejected", reason="confirmation failed the fixed task gate")
            atomic_json(path, state)
            self.archive.write_views()
            self._views()
            return state
