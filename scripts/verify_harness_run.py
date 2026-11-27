#!/usr/bin/env python3
"""Run/verify the pinned native CPU integration; no model call is simulated.

The scripted mode reuses upstream's own operator-acceptance Agent replacement.
Only the coding worker is scripted. Work/Judge, snapshots, native submission,
network policy, timeouts, artifacts and reward extraction remain upstream code.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from rsiforme.archive import Archive, atomic_json
from rsiforme.harness import HARNESS_REVISION, read_best_report, read_round_report, read_run_identity
from rsiforme.workflow import import_harness


WORK_SCRIPT = r'''set -euo pipefail
rsi_ci_files=$(mktemp -d /tmp/rsiforme-fixtures-XXXXXX)
tar -xf "$RSI_ACCEPTANCE_FILES" -C "$rsi_ci_files"
python - "$rsi_ci_files/fixtures" <<'RSIFORME_WORKER'
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib
import tempfile
from rsiforme.archive import Archive, atomic_json
from rsiforme.harness import parse_submit_output
from rsiforme.workflow import normalize_harness_result, submit

def actual_history():
    text = subprocess.check_output(['rsi-submit', '--list'], text=True)
    blocks = re.split(r'(?m)(?=^--- rsi-harness submission ---$)', text)
    reports = {}
    for block in blocks:
        if block.startswith('--- rsi-harness submission ---'):
            report = parse_submit_output(block)
            if report['round_id'] in reports:
                raise RuntimeError('duplicate round in native history')
            reports[report['round_id']] = report
    return text, reports

def submit_with_lost_response(archive, work, run_id, parent):
    before_text, before = actual_history()
    candidate = archive.capture(
        work, lane='native-ci', parent_id=parent,
        hypothesis='Prewritten incorrect fixture; also inject client-process loss after native response',
    )
    intent = archive.begin_submission(candidate['id'], run_id)
    # Real native request, real Judge, real client-process termination. The
    # response is deliberately discarded before any candidate/round binding.
    child = subprocess.run([sys.executable, '-c',
        "import os,subprocess,sys; p=subprocess.run(['rsi-submit'],capture_output=True); "
        "sys.exit(p.returncode) if p.returncode else os._exit(86)"], check=False)
    if child.returncode != 86:
        raise RuntimeError('fault injection did not reach its post-response boundary')
    archive = Archive(archive.root)
    if archive.submission(intent['id'])['status'] != 'pending':
        raise RuntimeError('lost response did not preserve the pending intent')
    after_text, after = actual_history()
    new_rounds = set(after) - set(before)
    if len(new_rounds) != 1 or not set(before).issubset(after):
        raise RuntimeError('native history does not uniquely identify the accepted round')
    round_id = new_rounds.pop()
    binding = archive.bind_submission(
        intent['id'], round_id,
        note='Recovered unique new round from real same-run history after injected client exit 86',
        feedback=after_text,
    )
    result = normalize_harness_result(archive, binding, after[round_id])
    with tempfile.TemporaryDirectory(prefix='rsiforme-native-fault-') as raw:
        evidence = Path(raw)
        (evidence / 'history-before.txt').write_text(before_text)
        (evidence / 'history-after.txt').write_text(after_text)
        atomic_json(evidence / 'fault.json', {
            'kind': 'injected_client_exit_after_native_response', 'exit_code': child.returncode,
            'pending_intent_id': intent['id'], 'recovered_round_id': round_id,
            'native_rounds_added': 1, 'resubmitted': False,
        })
        evaluation = archive.record_evaluation(candidate['id'], result, evidence=evidence)
    return {'submission': binding, 'evaluation': evaluation}

fixtures = Path(sys.argv[1])
work = Path('/workspace')
store = work / 'research' / 'archive'
archive = Archive(store)
task = tomllib.loads(Path('/opt/rsiforme/archive-task.toml').read_text())
task['context']['environment'] = {
    'native': json.loads(os.environ['RSIFORME_NATIVE_CONTEXT']),
    'python': platform.python_version(),
    'machine': platform.machine(),
    'protocol': 'scripted native Work/Judge acceptance; prewritten candidates',
}
archive.initialize(task)
run_id = os.environ['HARNESS_RUN_ID']
records = []
parent = None
for fixture in ('baseline', 'optimized', 'incorrect'):
    shutil.copyfile(fixtures / fixture / 'solution.py', work / 'solution.py')
    if fixture == 'incorrect':
        result = submit_with_lost_response(archive, work, run_id, parent)
    else:
        result = submit(
            archive, work, harness_run_id=run_id, lane='native-ci', parent_id=parent,
            hypothesis='Prewritten integration fixture: ' + fixture,
        )
    parent = result['submission']['candidate_id']
    records.append({'fixture': fixture, **result})
    print(json.dumps({'fixture': fixture, 'evaluation': result['evaluation']}, allow_nan=False), flush=True)

if records[-1]['evaluation']['valid']:
    raise RuntimeError('negative-control source unexpectedly passed the native Judge')
archive = Archive(store)
best = archive.best()
if best is None:
    raise RuntimeError('no valid native search candidate')
restored = archive.restore(best['candidate']['id'], work / 'research' / 'restored')
shutil.copyfile(restored / 'solution.py', work / 'solution.py')
confirmation = submit(
    archive, work, harness_run_id=run_id, candidate_id=best['candidate']['id'],
    kind='confirmation',
)
release = archive.promote(
    best['candidate']['id'], best['evaluation']['id'], confirmation['evaluation']['id'],
)
archive.write_views()
atomic_json(store / 'native-worker-result.json', {
    'kind': 'real_native_harness_scripted_worker',
    'autonomous_discovery': False,
    'harness_run_id': run_id,
    'records': records,
    'fault_injection': {
        'kind': 'injected_client_exit_after_native_response', 'exit_code': 86,
        'recovered_round_id': records[2]['submission']['round_id'], 'resubmitted': False,
    },
    'confirmation': confirmation,
    'release': release,
})
print(json.dumps({'native_worker_finished': True, 'release': release}, allow_nan=False), flush=True)
RSIFORME_WORKER
'''


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"not a JSON object: {path}")
    return value


def _one_task_root(logs: Path) -> Path:
    matches = sorted(logs.glob("runs/*/*/final_result.json"))
    if len(matches) != 1:
        raise ValueError(f"expected one completed native run in {logs}, found {len(matches)}")
    return matches[0].parent


def _judge_source_digest(task_root: Path, round_id: str) -> str:
    candidate_root = task_root / "verifier" / round_id / "candidate"
    reports = sorted(candidate_root.glob("*/report.json"))
    if len(reports) != 1:
        raise ValueError("expected one candidate evaluator execution directory in the native Judge")
    payload = _read(reports[0])
    if payload.get("eval_run_id") != reports[0].parent.name:
        raise ValueError("candidate evaluator ID differs from its evidence directory")
    digest = payload.get("source_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("native Judge did not record the candidate source SHA-256")
    return digest


def verify_run(output: Path, mode: str, repository: Path) -> dict:
    """Check actual durable native artifacts; absence is an error, never a skip."""
    task_root = _one_task_root(output / "logs")
    identity = read_run_identity(task_root)
    if identity["task_id"] != "jsonl-aggregate":
        raise ValueError("native run used a different task")
    plan = _read(task_root / "run-plan.json")
    final = _read(task_root / "final_result.json")
    state = _read(task_root / "evolve_state.json")
    expected_rounds = 2 if mode == "oracle" else 4
    if final.get("status") != "completed" or final.get("timed_out") is not False:
        raise ValueError(f"native run did not complete: {final}")
    if final.get("agent_exit_code") != 0 or final.get("total_rounds") != expected_rounds:
        raise ValueError("native Agent exit or number of Judge rounds is wrong")
    if len(state["submissions"]) != expected_rounds:
        raise ValueError("durable submission count differs from final result")
    if plan["task"]["agent"]["name"] != ("oracle" if mode == "oracle" else "codex"):
        raise ValueError("unexpected native Agent adapter")
    reports = [read_round_report(task_root, f"agent-{i}") for i in range(1, expected_rounds + 1)]
    if mode == "oracle":
        expected_source = hashlib.sha256(
            (repository / "examples/jsonl_aggregate/fixtures/optimized/solution.py").read_bytes()
        ).hexdigest()
        for report in reports:
            if (report["status"] != "completed" or report.get("exit_code") != 0
                    or report["metrics"].get("quality_passed") != 1
                    or report["metrics"].get("reward", 0) <= 0):
                raise ValueError("oracle candidate failed its native correctness/reward gate")
            if _judge_source_digest(task_root, report["round_id"]) != expected_source:
                raise ValueError("native oracle evaluated a different source file")
        archive_checks = None
    else:
        archive = Archive(output / "archive")
        worker = _read(archive.root / "native-worker-result.json")
        if worker["harness_run_id"] != identity["harness_run_id"]:
            raise ValueError("Work archive was bound to the wrong native run")
        fault = worker.get("fault_injection", {})
        if (fault.get("kind") != "injected_client_exit_after_native_response"
                or fault.get("exit_code") != 86 or fault.get("resubmitted") is not False
                or fault.get("recovered_round_id") != "agent-3"):
            raise ValueError("native lost-response recovery was not exercised")
        if archive.summary()["pending_submissions"]:
            raise ValueError("native wrapper left an unresolved submission")
        if len(archive.candidates()) != 3 or len(archive.evaluations()) != 4:
            raise ValueError("native archive is missing candidate or evaluation records")
        for index, report in enumerate(reports, start=1):
            binding = archive.binding(identity["harness_run_id"], report["round_id"])
            candidate = archive.candidate(binding["candidate_id"])
            archive.source(candidate["id"])
            source = next(item for item in candidate["files"] if item["path"] == "solution.py")
            if _judge_source_digest(task_root, report["round_id"]) != source["sha256"]:
                raise ValueError("native Judge source does not match the pre-submit archived candidate")
            imported = import_harness(archive, task_root, identity["harness_run_id"], round_id=report["round_id"])
            evaluation = imported["evaluation"]
            if index == 3:
                if report["status"] == "completed" or evaluation["valid"]:
                    raise ValueError("native Judge accepted the intentionally incorrect source")
            elif (report["status"] != "completed" or not evaluation["valid"]
                  or report["metrics"].get("quality_passed") != 1):
                raise ValueError("a valid scripted candidate did not pass its native Judge")
        current = _read(archive.root / "CURRENT.json")
        release = _read(archive.root / "releases" / (current["release_id"] + ".json"))
        confirmation = archive.evaluation(release["confirmation_evaluation"])
        search = archive.evaluation(release["search_evaluation"])
        if (confirmation["kind"] != "confirmation" or search["kind"] != "search"
                or confirmation["round_id"] == search["round_id"]
                or confirmation["candidate_id"] != search["candidate_id"]):
            raise ValueError("release lacks a separate native confirmation of the same candidate")
        archive_checks = {"candidates": 3, "evaluations": 4, "source_matches_native_judge": True,
                          "invalid_control_rejected": True, "lost_response_recovered": True,
                          "release": release}
    best = read_best_report(task_root)
    summary = {
        "kind": "native_harness_validation",
        "mode": mode,
        "upstream_revision": HARNESS_REVISION,
        "harness_run_id": identity["harness_run_id"],
        "task_id": identity["task_id"],
        "round_count": len(reports),
        "rounds": [{key: report.get(key) for key in
                    ("round_id", "status", "score", "metrics", "exit_code", "runtime_seconds")}
                   for report in reports],
        "best_round": best["round_id"],
        "best_round_metrics": best["metrics"],
        "images": plan["images"],
        "task_digests": {key: plan["task"][key] for key in
                         ("source_digest", "tests_digest", "environment_digest", "instruction_digest")},
        "archive_checks": archive_checks,
        "autonomous_discovery": False,
        "verified": True,
    }
    atomic_json(output / "verified.json", summary)
    print(json.dumps(summary, indent=2, allow_nan=False))
    return summary


def run_scripted(upstream: Path, task: Path, output: Path, data_root: Path, repository: Path) -> None:
    helper = upstream / "RSI-Harness/tests/acceptance/scripted_cli.py"
    if not helper.is_file():
        raise ValueError(f"pinned upstream acceptance helper is missing: {helper}")
    spec = importlib.util.spec_from_file_location("rsiforme_pinned_acceptance_helper", helper)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import pinned acceptance helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.install_scripted_agent(WORK_SCRIPT, [repository / "examples/jsonl_aggregate/fixtures"], {})
    import rsi_harness.runtime.production as production
    prepared_adapter = production.RSILoopAgentAdapter

    class ArchiveAcceptanceAdapter(prepared_adapter):
        def prepare(self, request):
            prepared = super().prepare(request)
            native_context = {
                "images": request.run_plan.images.model_dump(mode="json"),
                "task_source_digest": request.run_plan.task.source_digest,
            }
            environment = dict(prepared.environment)
            environment["HARNESS_RUN_ID"] = request.run_plan.paths.root.name
            environment["RSIFORME_NATIVE_CONTEXT"] = json.dumps(native_context, allow_nan=False)
            return replace(prepared, environment=tuple(sorted(environment.items())))

        def run(self, request):
            result = super().run(request)
            # Work is still present at this exact lifecycle boundary. Use the
            # supplied container identity, then let the coordinator clean up.
            output.mkdir(parents=True, exist_ok=True)
            subprocess.run(["docker", "cp", request.container.container_id + ":/workspace/research/archive",
                            str(output / "archive")], check=True)
            return result

    production.RSILoopAgentAdapter = ArchiveAcceptanceAdapter
    from rsi_harness.cli import app
    app(args=["run", str(task), "--agent", "codex", "--disable-stop-hook",
              "--max-submissions", "4", "--timeout", "600", "--primary-reward", "reward",
              "--data-root", str(data_root), "--logs-root", str(output / "logs"), "--verbose"],
        prog_name="rsi-harness", standalone_mode=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scripted = commands.add_parser("scripted", help="Run real native Work/Judge with upstream's scripted acceptance worker")
    scripted.add_argument("--upstream", required=True, type=Path)
    scripted.add_argument("--task", required=True, type=Path)
    scripted.add_argument("--data-root", required=True, type=Path)
    scripted.add_argument("--output", required=True, type=Path)
    scripted.add_argument("--repository", default=Path(__file__).resolve().parents[1], type=Path)
    verify = commands.add_parser("verify", help="Require actual native artifacts and check their source/round binding")
    verify.add_argument("--mode", choices=("oracle", "scripted"), required=True)
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--repository", default=Path(__file__).resolve().parents[1], type=Path)
    args = parser.parse_args()
    if args.command == "scripted":
        run_scripted(args.upstream.resolve(), args.task.resolve(), args.output.resolve(),
                     args.data_root.resolve(), args.repository.resolve())
    else:
        verify_run(args.output.resolve(), args.mode, args.repository.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
