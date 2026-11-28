"""Export a bounded plan for already observed roots, without any network action."""
import argparse
from pathlib import Path

from contract import load_epoch, load_json, plan, write_json
from prepare import outside_repository


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prepared", required=True, type=Path)
    p.add_argument("--policy", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    result = plan(load_epoch(args.prepared / "evaluator" / "epoch.json"), load_json(args.policy))
    output = outside_repository(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    print(str(output))


if __name__ == "__main__":
    main()
