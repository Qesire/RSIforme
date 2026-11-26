#!/bin/bash
set -euo pipefail
cp /solution/optimized.py /workspace/solution.py
mkdir -p /workspace/research
cat > /workspace/research/hypothesis.md <<'EOF'
The baseline scans every row once per group. Accumulate counts and exact integer
sums in a dictionary during one pass, then sort the group keys. This preserves
the contract while reducing the repeated scanning work.

This is a supplied oracle solution used to verify the task plumbing, not an
autonomously discovered research result.
EOF
