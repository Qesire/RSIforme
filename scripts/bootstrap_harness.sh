#!/usr/bin/env bash
# Fetch a pinned upstream in a separate directory; never rewrite an existing tree.
set -euo pipefail

rsi_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
rsi_upstream_dir="${1:-$rsi_repo_root/.upstream/OpenRSI-Index}"
command -v git >/dev/null
command -v python3 >/dev/null
command -v uv >/dev/null

mapfile -t rsi_locked_values < <(python3 - "$rsi_repo_root/configs/upstreams.lock.json" <<'PY'
import json
import sys
lock = json.load(open(sys.argv[1], encoding="utf-8"))["execution"]
for value in (lock["harness"]["repository"], lock["harness"]["revision"],
              lock["infra_bench"]["submodule_path"], lock["infra_bench"]["revision"]):
    print(value)
PY
)
if [[ "${#rsi_locked_values[@]}" -ne 4 ]]; then
    printf '%s\n' 'Invalid upstream lock file.' >&2
    exit 2
fi
rsi_upstream_url="${rsi_locked_values[0]}"
rsi_upstream_revision="${rsi_locked_values[1]}"
rsi_task_submodule="${rsi_locked_values[2]}"
rsi_task_revision="${rsi_locked_values[3]}"

if [[ -e "$rsi_upstream_dir" ]]; then
    rsi_existing_revision="$(git -C "$rsi_upstream_dir" rev-parse HEAD)"
    if [[ "$rsi_existing_revision" != "$rsi_upstream_revision" ]]; then
        printf '%s\n' 'Existing upstream is at another revision; use a fresh destination.' >&2
        exit 2
    fi
    if [[ -n "$(git -C "$rsi_upstream_dir" status --porcelain)" ]]; then
        printf '%s\n' 'Existing upstream has changes; use a fresh destination.' >&2
        exit 2
    fi
else
    mkdir -p -- "$(dirname -- "$rsi_upstream_dir")"
    git clone --no-checkout -- "$rsi_upstream_url" "$rsi_upstream_dir"
    git -C "$rsi_upstream_dir" checkout --detach "$rsi_upstream_revision"
fi
git -C "$rsi_upstream_dir" submodule update --init --recursive
if [[ "$(git -C "$rsi_upstream_dir/$rsi_task_submodule" rev-parse HEAD)" != "$rsi_task_revision" ]]; then
    printf '%s\n' 'Task submodule does not match the lock.' >&2
    exit 2
fi
uv tool install --python 3.12 --force "$rsi_upstream_dir/RSI-Harness"
printf 'Pinned Harness checkout: %s\n' "$rsi_upstream_dir"
printf '%s\n' 'No task, image build, GPU job, or model call has been started.'
