#!/bin/bash
# Fails if the image or the running container exceeds the budget in README §8:
#   image < 60 MB compressed (190 MB on disk), cold start < 5 s,
#   RAM < 150 MB idle and < 256 MB under load.
set -euo pipefail
IMAGE="${1:-pfm-ng:ci}"
MAX_IMAGE_MB=190 MAX_COMPRESSED_MB=60 MAX_START_S=5 MAX_IDLE_MB=150 MAX_LOAD_MB=256

compressed_mb=$(( $(docker save "$IMAGE" | gzip -6 | wc -c) / 1024 / 1024 ))
echo "compressed: ${compressed_mb} MB (max ${MAX_COMPRESSED_MB})"

name="pfm-budget-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
# Create first: with the containerd image store (GitHub runners) the image is
# kept compressed and unpacked when the first container is created, a one-off
# cost of the download, not of a start. On-disk size = total size of the
# image's files, which every storage driver agrees on (image inspect reports
# the compressed size with containerd, and block usage varies by driver).
docker create --name "$name" -p 18000:8000 --read-only --tmpfs /tmp --cap-drop ALL "$IMAGE" >/dev/null
size_mb=$(docker run --rm --entrypoint python "$IMAGE" -c '
import os
total = 0
for root, dirs, files in os.walk("/"):
    dirs[:] = [d for d in dirs if os.path.join(root, d) not in ("/proc", "/sys", "/dev")]
    for f in files:
        try:
            total += os.lstat(os.path.join(root, f)).st_size
        except OSError:
            pass
print(total // 1048576)')
echo "image size: ${size_mb} MB (max ${MAX_IMAGE_MB})"

start=$(date +%s.%N)
docker start "$name" >/dev/null
until curl -fsS http://127.0.0.1:18000/healthz >/dev/null 2>&1; do
  sleep 0.1
  if (( $(echo "$(date +%s.%N) - $start > 30" | bc) )); then docker logs "$name"; exit 1; fi
done
startup=$(echo "$(date +%s.%N) - $start" | bc)
echo "cold start: ${startup}s (max ${MAX_START_S}s)"

# docker stats prints e.g. "157.3MiB / 15.5GiB": first number in MiB, locale-independent.
mem() {
  docker stats --no-stream --format '{{.MemUsage}}' "$name" | awk '{
    v = $1; n = v + 0; u = v; sub(/^[0-9.]+/, "", u)
    f = (u == "GiB") ? 1024 : (u == "KiB" || u == "kB") ? 1/1024 : (u == "B") ? 1/1048576 : 1
    printf "%d\n", n * f
  }'
}
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
(( compressed_mb <= MAX_COMPRESSED_MB )) || { echo "FAIL: compressed image too large"; fail=1; }
(( $(echo "$startup <= $MAX_START_S" | bc) )) || { echo "FAIL: start too slow"; fail=1; }
(( idle <= MAX_IDLE_MB )) || { echo "FAIL: idle RAM too high"; fail=1; }
(( load <= MAX_LOAD_MB )) || { echo "FAIL: RAM under load too high"; fail=1; }
exit "$fail"
