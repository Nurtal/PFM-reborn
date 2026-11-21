#!/bin/bash
# Fails if the image or the running container exceeds the budget in README §8:
#   image < 150 MB, cold start < 5 s, RAM < 150 MB idle and < 256 MB under load.
set -euo pipefail
IMAGE="${1:-pfm-ng:ci}"
MAX_IMAGE_MB=150 MAX_START_S=5 MAX_IDLE_MB=150 MAX_LOAD_MB=256

size_mb=$(( $(docker image inspect "$IMAGE" --format '{{.Size}}') / 1024 / 1024 ))
echo "image size: ${size_mb} MB (max ${MAX_IMAGE_MB})"

name="pfm-budget-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
start=$(date +%s.%N)
docker run -d --name "$name" -p 18000:8000 --read-only --tmpfs /tmp --cap-drop ALL "$IMAGE" >/dev/null
until curl -fsS http://127.0.0.1:18000/healthz >/dev/null 2>&1; do
  sleep 0.1
  if (( $(echo "$(date +%s.%N) - $start > 30" | bc) )); then docker logs "$name"; exit 1; fi
done
startup=$(echo "$(date +%s.%N) - $start" | bc)
echo "cold start: ${startup}s (max ${MAX_START_S}s)"

mem() { docker stats --no-stream --format '{{.MemUsage}}' "$name" | awk '{print $1}' | numfmt --from=iec-i --to-unit=1048576 --suffix=""; }
sleep 3
idle=$(mem | cut -d. -f1)
echo "idle RAM: ${idle} MB (max ${MAX_IDLE_MB})"

# Light load: login page, health, static assets (the scenario grows with each milestone).
for _ in $(seq 1 300); do
  curl -fsS -o /dev/null http://127.0.0.1:18000/healthz &
  curl -fsS -o /dev/null http://127.0.0.1:18000/setup/ &
  wait
done
load=$(mem | cut -d. -f1)
echo "RAM after load: ${load} MB (max ${MAX_LOAD_MB})"

fail=0
(( size_mb <= MAX_IMAGE_MB )) || { echo "FAIL: image too large"; fail=1; }
(( $(echo "$startup <= $MAX_START_S" | bc) )) || { echo "FAIL: start too slow"; fail=1; }
(( idle <= MAX_IDLE_MB )) || { echo "FAIL: idle RAM too high"; fail=1; }
(( load <= MAX_LOAD_MB )) || { echo "FAIL: RAM under load too high"; fail=1; }
exit "$fail"
