#!/bin/bash
# Re-run scripts/run_grid.py (resumable) until the grid is complete, a STOP file exists, or the deadline passes.
# Usage: scripts/run_grid_until_done.sh <A|B> <out dir> <deadline epoch seconds> [extra run_grid args...]
model=$1; out=$2; deadline=$3; shift 3
cd "$(dirname "$0")/.." || exit 1
py=${PYTHON:-../.venv/bin/python}
while :; do
  now=$(date +%s); [ "$now" -ge "$deadline" ] && { echo "deadline reached"; exit 3; }
  [ -e "$out/STOP" ] && { echo "STOP file found"; exit 3; }
  mins=$(( (deadline - now) / 60 )); [ "$mins" -gt 60 ] && mins=60
  nice -n 10 "$py" scripts/run_grid.py --model "$model" --out "$out" --deadline-min "$mins" "$@" > "$out.last.log" 2>&1
  rc=$?; tail -3 "$out.last.log"
  [ "$rc" -eq 0 ] && { echo "grid complete"; exit 0; }
  if grep -qiE "quota" "$out.last.log"; then sleep 1200; else sleep 120; fi
done
