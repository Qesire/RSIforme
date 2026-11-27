"""Run a bounded JSONL research campaign using the configured Codex CLI.

Preflight sends no model prompt and initializes no campaign. Full execution uses
the existing CLI identity/configuration, never changes CODEX_HOME, and stops on
the first failed worker. A new store is a new campaign, not a budget reset or a
resume command. Inspect the saved trial before any subsequent model invocation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from rsiforme.archive import Archive, ArchiveError, atomic_json, file_sha, now
from rsiforme.delivery import export_release, verify_release
from rsiforme.research import ResearchController
from rsiforme.workflow import evaluate_local


EXAMPLE = Path(__file__).resolve().parent
REPOSITORY = EXAMPLE.parents[1]
SCOPE = """
本次研究只使用本公开 JSONL 任务的当前候选、handoff 及本轮生成的反馈。
只读取当前工作目录中的 solution.py、handoff.json、proposal.schema.json 和你创建的局部检查文件。
handoff 已包含任务合同、真实 profile 和完整评价摘要；不要读取 archive_read_path 下的
评价器源码、reference/oracle 实现、其他 fixture、其他项目或账户材料，不要联网检索现成答案。
可以用 Python 标准库自行构造局部正确性/计时检查，但不调用正式评价器。
仅修改 solution.py，返回符合 schema 的 JSON；明确引用具体 profile 发现、父代 evaluation ID
和使用过的历史 trial/evaluation ID。180 秒 worker 上限内完成最有把握的单一修改；
没有支持的修改则如实 stop。这里的读取范围是工作指令，不能据此声称文件系统读取已被隔离。
""".strip()


def inspect_state_directory(state_root: Path) -> dict:
    """Inspect metadata only; do not read identity files or change permissions."""
    target = state_root / "installation_id"
    probe = target if target.exists() else state_root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        readonly = bool(os.statvfs(probe).f_flag & os.ST_RDONLY)
        writable = os.access(probe, os.W_OK)
        usable_type = state_root.is_dir() if state_root.exists() else probe.is_dir()
        usable_id = not target.exists() or (target.is_file() and not target.is_symlink())
        ready = usable_type and usable_id and writable and not readonly
        return {"ready": ready, "state_directory_exists": state_root.exists(),
                "installation_id_exists": target.exists(), "filesystem_readonly": readonly,
                "required_path_writable": writable,
                "reason": "local_state_metadata_ready" if ready else "codex_state_write_unavailable"}
    except OSError as exc:
        return {"ready": False, "reason": "codex_state_metadata_unavailable", "errno": exc.errno}


def codex_preflight() -> dict:
    """Check local prerequisites, without starting exec/app-server or a model turn.

    The installation_id write is mandatory in the diagnosed 0.172.0-alpha.2
    runtime. For other versions this is a conservative prerequisite, not proof
    that every state path, service endpoint, model or sandbox is operational.
    """
    result = {"kind": "local_codex_preflight", "checked_at": now(), "ready": False,
              "model_prompts_submitted": 0, "worker_launches": 0, "formal_evaluations": 0,
              "scope": "CLI version/login status and state-path metadata only"}
    executable = shutil.which("codex")
    if executable is None:
        return {**result, "reason": "codex_cli_not_found"}
    state_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    result["state"] = inspect_state_directory(state_root)
    try:
        version = subprocess.run([executable, "--version"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=10, check=False)
        result["cli_version"] = version.stdout.strip()[:160] if version.returncode == 0 else None
        result["version_exit_code"] = version.returncode
        if version.returncode != 0:
            return {**result, "reason": "codex_version_check_failed"}
        # Do not copy login output into a public report; it can contain account metadata.
        login = subprocess.run([executable, "login", "status"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=10, check=False)
        result["login_status_exit_code"] = login.returncode
        if login.returncode != 0:
            return {**result, "reason": "codex_login_status_unavailable"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {**result, "reason": "codex_cli_check_failed", "error_type": type(exc).__name__}
    if not result["state"]["ready"]:
        return {**result, "reason": result["state"]["reason"]}
    return {**result, "ready": True, "reason": "local_preconditions_passed",
            "limitation": "Does not verify app-server startup, network access or model availability"}


def drive_trials(controller: ResearchController) -> str:
    """Advance one trial at a time; a failed attempt never triggers another worker."""
    for _ in range(controller.config["program"]["budgets"]["max_trials"]):
        budget = controller.status()["budget"]
        if (budget["remaining_wall_seconds"] <= 0
                or budget["evaluations_consumed"] + 2 > budget["max_evaluations"]):
            return "budget_exhausted"
        controller.prepare()
        controller.run(max_new_trials=0)
        trial = controller.trials()[-1]
        if trial["phase"] == "worker_failed":
            return "worker_failed"
        if trial.get("decision") == "infra_error":
            return "evaluation_infra_error"
        if trial["phase"] == "stopped":
            break
    trials = controller.trials()
    # The first fresh evaluation must exist for a second worker to consume it.
    # A second worker may legitimately decide to stop or leave source unchanged.
    accepted = [t for t in trials if t.get("proposal") and t.get("worker_returncode") == 0
                and t["phase"] in ("completed", "stopped")]
    if len(accepted) < 2 or not trials[0].get("evaluation_id"):
        return "feedback_evidence_incomplete"
    return "ready_for_confirmation"


def campaign_summary(archive: Archive, controller: ResearchController | None,
                     phase: str, **details) -> dict:
    trials = controller.trials() if controller is not None else []
    evaluations = archive.evaluations()
    summary = {"kind": "bounded_codex_research", "recorded_at": now(), "phase": phase,
               "research_id": controller.config["id"] if controller is not None else None,
               "worker_launches": sum(bool(t.get("worker_started_at")) for t in trials),
               "accepted_proposals": sum(bool(t.get("proposal")) and t.get("worker_returncode") == 0
                                         and t["phase"] in ("completed", "stopped")
                                         for t in trials),
               "model_request_count": None,
               "model_request_count_note": "Worker launches do not count individual service requests",
               "autonomous_discovery_verified": False,
               "feedback_review": "Manual inspection of proposals, tool logs and source changes is required",
               "reference_gate_g3": "open_pending_evidence_review",
               "trials": trials, "evaluations": evaluations,
               "budget": controller.status()["budget"] if controller is not None else None,
               "additional_reproduction_attempts": 0, "additional_reproduction_evaluations": 0, **details}
    atomic_json(archive.root / "model-research-result.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("trials", "evaluations")},
                     ensure_ascii=False, indent=2))
    return summary


def run_campaign(store: Path, preflight: dict, *, reproduce: bool = False) -> int:
    if preflight.get("ready") is not True:
        raise ValueError("Local preflight must pass before creating a model research campaign")
    store = store.resolve()
    if store.is_relative_to(REPOSITORY) or REPOSITORY.is_relative_to(store):
        raise ValueError("Choose a new store outside the repository and its ancestors")
    if store.exists():
        raise ValueError("Store already exists; preserve it and inspect its saved state before resuming")
    release_dir = store.with_name(store.name + "-release")
    reproduction_dir = store.with_name(store.name + "-reproduced")
    if release_dir.exists() or (reproduce and reproduction_dir.exists()):
        raise ValueError("Delivery destination already exists; choose a new campaign path")
    evaluator = EXAMPLE / "evaluate.py"
    archive = Archive(store)
    controller = None
    reproduction_accounting = {"additional_reproduction_attempts": 0,
                               "additional_reproduction_evaluations": 0}
    archive.initialize({
        "schema_version": 1, "task_id": "jsonl-model-research-v1",
        "objective": "rows_per_second", "direction": "maximize", "source_paths": ["solution.py"],
        "evaluation_timeout_seconds": 30,
        "contract": {"entrypoint": "aggregate(records)",
                     "input": "List of records; group is str, value is an exact Python int",
                     "output": "List of {group, count, sum}, sorted by group; exact integer count and sum",
                     "domain": "Empty input, negative values, Unicode/empty groups and large integers are valid",
                     "benchmark": "40000 rows, 128 groups, one warmup and three timed repeats; median",
                     "measurement": "Fresh process + JSONL parsing + aggregation + JSON output"},
        "context": {"task_revision": "jsonl-aggregate-v1",
                    "evaluator_revision": "sha256:" + file_sha(evaluator),
                    "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                    "machine": platform.machine()},
                    "worker_kind": "Configured Codex CLI; no prewritten candidate proposals",
                    "launcher_sha256": file_sha(Path(__file__))}})
    atomic_json(store / "preflight.json", preflight)
    try:
        candidate = archive.capture(EXAMPLE / "fixtures/baseline", lane="baseline",
                                    hypothesis="Measure the public baseline before model-proposed changes")
        baseline = evaluate_local(archive, candidate["id"], evaluator)
        if baseline["status"] != "completed" or not baseline["valid"]:
            campaign_summary(archive, None, "baseline_failed")
            return 3
        profile = store / "baseline-profile.json"
        command = [sys.executable, "-B", str(EXAMPLE / "profile_baseline.py"),
                   "--store", str(store), "--candidate", candidate["id"], "--output", str(profile)]
        profile_run = subprocess.run(command, cwd=REPOSITORY, capture_output=True,
                                     text=True, timeout=30, check=False)
        (store / "profile.stdout.txt").write_text(profile_run.stdout, encoding="utf-8")
        (store / "profile.stderr.txt").write_text(profile_run.stderr, encoding="utf-8")
        if profile_run.returncode != 0:
            campaign_summary(archive, None, "profile_failed", profile_returncode=profile_run.returncode)
            return 3
        program = json.loads((REPOSITORY / "configs/research.codex.example.json").read_text())
        program["instructions"] += "\n" + SCOPE
        atomic_json(store / "program.json", program)
        initialized = ResearchController(archive)
        initialized.initialize(program, baseline_evaluation=baseline["id"], profile=profile)
        controller = initialized
        phase = drive_trials(controller)
        if phase != "ready_for_confirmation":
            campaign_summary(archive, controller, phase, finalization=None, export=None)
            return 3
        final = controller.finalize()
        if final["phase"] != "promoted":
            campaign_summary(archive, controller, "confirmation_rejected", finalization=final)
            return 3
        export = export_release(archive, release_dir)
        checked = subprocess.run([sys.executable, "-I", "-B", str(release_dir / "verify.py")],
                                 cwd=release_dir, capture_output=True, text=True, timeout=30, check=False)
        (store / "portable-verify.stdout.txt").write_text(checked.stdout, encoding="utf-8")
        (store / "portable-verify.stderr.txt").write_text(checked.stderr, encoding="utf-8")
        if checked.returncode != 0:
            campaign_summary(archive, controller, "portable_verify_failed", finalization=final, export=export)
            return 3
        verify_release(release_dir)
        details = {"finalization": final, "export": export, "portable_verification_succeeded": True,
                   "baseline_selected": final["candidate_id"] == candidate["id"]}
        if reproduce:
            # This optional new evaluation is outside the four campaign evaluations.
            # Until a retained evaluator result exists, its execution count is unknown.
            reproduction_accounting.update(additional_reproduction_attempts=1,
                                           additional_reproduction_evaluations=None)
            atomic_json(store / "portable-reproduction-intent.json", {
                "recorded_at": now(), "output": str(reproduction_dir), **reproduction_accounting})
            reproduced = subprocess.run([sys.executable, "-I", "-B", str(release_dir / "reproduce.py"),
                                         str(reproduction_dir)], cwd=release_dir, capture_output=True,
                                        text=True, timeout=90, check=False)
            (store / "portable-reproduce.stdout.txt").write_text(reproduced.stdout, encoding="utf-8")
            (store / "portable-reproduce.stderr.txt").write_text(reproduced.stderr, encoding="utf-8")
            result_path = reproduction_dir / "reproduction_result.json"
            if result_path.is_file():
                reproduced_result = json.loads(result_path.read_text())
                if reproduced_result.get("result", {}).get("eval_run_id"):
                    reproduction_accounting["additional_reproduction_evaluations"] = 1
            details["portable_reproduction_succeeded"] = reproduced.returncode == 0
            if reproduced.returncode != 0:
                campaign_summary(archive, controller, "portable_reproduction_failed",
                                 **details, **reproduction_accounting)
                return 3
        campaign_summary(archive, controller, "completed_pending_evidence_review",
                         **details, **reproduction_accounting)
        return 0
    except (ArchiveError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        campaign_summary(archive, controller, "execution_error", error_type=type(exc).__name__, error=str(exc),
                         **reproduction_accounting)
        return 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true", help="Check local prerequisites without a campaign")
    parser.add_argument("--store", type=Path, help="New archive outside the repository; never overwrite an earlier run")
    parser.add_argument("--reproduce", action="store_true", help="Run one separately counted portable reproduction")
    args = parser.parse_args(argv)
    if not args.preflight_only and args.store is None:
        parser.error("--store is required for model execution")
    if args.preflight_only and args.reproduce:
        parser.error("--reproduce cannot be used with --preflight-only")
    preflight = codex_preflight()
    print(json.dumps(preflight, ensure_ascii=False, indent=2))
    if not preflight["ready"]:
        return 2
    if args.preflight_only:
        return 0
    try:
        return run_campaign(args.store, preflight, reproduce=args.reproduce)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
