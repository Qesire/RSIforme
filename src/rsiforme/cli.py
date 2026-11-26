"""Command-line entry points for explicit, inspectable research steps."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys
import tomllib

from .archive import Archive, ArchiveError, load_json
from .harness import construct_harness_command
from .workflow import demo, evaluate_local, import_harness, submit


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rsiforme", description="Archive, evaluate and restore candidates around RSI-Harness")
    commands = result.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("init", "Freeze task/context in a research archive"),
        ("capture", "Save exact source, configuration and lineage"),
        ("evaluate", "Run a frozen trusted local evaluator"),
        ("submit", "Capture and invoke native rsi-submit once inside Work"),
        ("recover", "Bind an unresolved submission after checking run history"),
        ("import-harness", "Import one bound round from durable Harness artifacts"),
        ("best", "Show the best valid search candidate and its own metrics"),
        ("restore", "Restore a candidate into a new workspace"),
        ("promote", "Publish a candidate after a separate confirmation"),
        ("report", "Regenerate STATUS.json and JOURNAL.md from saved records"),
        ("demo", "Execute the CPU workflow with prewritten candidate fixtures"),
    ):
        child = commands.add_parser(name, help=help_text)
        child.add_argument("--store", required=True, type=Path)
        if name == "init":
            child.add_argument("--task", required=True, type=Path)
        if name in ("capture", "submit"):
            child.add_argument("--workspace", required=True, type=Path)
            child.add_argument("--lane", default="main")
            child.add_argument("--hypothesis")
            child.add_argument("--parent")
        if name in ("evaluate", "restore", "promote"):
            child.add_argument("--candidate", required=True)
        if name in ("evaluate", "submit"):
            child.add_argument("--kind", choices=("search", "confirmation"), default="search")
        if name == "evaluate":
            child.add_argument("--evaluator", required=True, type=Path)
            child.add_argument("--workspace", type=Path, help="Optional restored workspace; must match the candidate")
        if name == "submit":
            child.add_argument("--candidate")
            child.add_argument("--harness-run-id", required=True)
        if name == "recover":
            child.add_argument("--submission", required=True)
            child.add_argument("--round", required=True)
            child.add_argument("--note", required=True, help="Evidence used to identify this round in the same run")
        if name == "import-harness":
            child.add_argument("--run-dir", required=True, type=Path)
            child.add_argument("--harness-run-id", required=True)
            child.add_argument("--round", help="Default: the run's best_round")
        if name == "restore":
            child.add_argument("--to", required=True, type=Path)
        if name == "promote":
            child.add_argument("--search-evaluation", required=True)
            child.add_argument("--confirmation-evaluation", required=True)
        if name == "demo":
            child.add_argument("--repository", default=Path.cwd(), type=Path,
                               help="Checkout containing examples/jsonl_aggregate (default: cwd)")
    plan = commands.add_parser("harness-command", help="Print a native Harness command; do not execute it")
    plan.add_argument("--task", type=Path)
    plan.add_argument("--agent", default="oracle")
    plan.add_argument("--model")
    plan.add_argument("--cluster")
    plan.add_argument("--resume", metavar="RUN_ID")
    plan.add_argument("--live", action="store_true", help="Print command without --dry-run (still not executed)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "harness-command":
            command = construct_harness_command(args.task, agent=args.agent, model=args.model,
                                                cluster=args.cluster, dry_run=not args.live, run_id=args.resume)
            output = {"executed": False, "argv": command, "shell": shlex.join(command)}
        elif args.command == "demo":
            output = demo(args.store, args.repository)
        else:
            archive = Archive(args.store)
            if args.command == "init":
                if args.task.suffix == ".toml":
                    with args.task.open("rb") as stream:
                        task = tomllib.load(stream)
                else:
                    task = load_json(args.task)
                output = archive.initialize(task)
            elif args.command == "capture":
                output = archive.capture(args.workspace, lane=args.lane,
                                         hypothesis=args.hypothesis or "", parent_id=args.parent)
            elif args.command == "evaluate":
                output = evaluate_local(archive, args.candidate, args.evaluator,
                                        kind=args.kind, workspace=args.workspace)
            elif args.command == "submit":
                output = submit(archive, args.workspace, harness_run_id=args.harness_run_id,
                                candidate_id=args.candidate, lane=args.lane, hypothesis=args.hypothesis,
                                parent_id=args.parent, kind=args.kind)
            elif args.command == "recover":
                output = archive.bind_submission(args.submission, args.round, note=args.note)
            elif args.command == "import-harness":
                output = import_harness(archive, args.run_dir, args.harness_run_id, round_id=args.round)
            elif args.command == "best":
                output = archive.best()
            elif args.command == "restore":
                output = {"restored": str(archive.restore(args.candidate, args.to))}
            elif args.command == "promote":
                output = archive.promote(args.candidate, args.search_evaluation, args.confirmation_evaluation)
            elif args.command == "report":
                output = archive.write_views()
            else:
                raise ArchiveError("Unrecognized command")
            if args.command not in ("best", "report", "submit", "import-harness"):
                archive.write_views()
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"rsiforme: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("rsiforme: interrupted; inspect saved state and pending submissions before resuming", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
