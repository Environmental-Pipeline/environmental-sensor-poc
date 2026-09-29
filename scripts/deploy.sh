#!/usr/bin/env bash
# Deploy the committed HEAD of this repo to the sensorpull-run container.
#   scripts/deploy.sh <change-name>
# Tags the running image as a rollback point, builds, smoke-tests the new image,
# recreates the container outside the :44-:56 consolidation window, verifies it,
# and prints the rollback command.
set -euo pipefail

NAME="${1:?usage: scripts/deploy.sh <change-name>}"
REPO=/home/aha48/environmental-sensor-poc
DATA=/opt/env-sensor-data
RUN_ARGS=(-d --name sensorpull-run --restart unless-stopped -v "$DATA":/src/data -v "$REPO/.env":/src/.env)

in_window() { local m=$((10#$(date -u +%M))); [ "$m" -ge 44 ] && [ "$m" -le 56 ]; }

cd "$REPO"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "ABORT: uncommitted changes; commit first."; exit 1
fi
if in_window; then echo "ABORT: inside :44-:56 UTC (consolidation). Try again after :56."; exit 1; fi

COMMIT=$(git rev-parse --short HEAD)
TAG="pre-${NAME}-$(date -u +%Y%m%d%H%M)"
docker tag sensorpull:latest "sensorpull:${TAG}"
echo "Rollback image: sensorpull:${TAG}"

docker build . -t sensorpull:latest > /tmp/deploy-build.log 2>&1 || { echo "ABORT: build failed, see /tmp/deploy-build.log"; exit 1; }
docker run --rm --entrypoint python3 sensorpull:latest -c \
  "import sys; sys.path.insert(0,'/src'); import EnvironmentData, modules.csc_filter, modules.weather_enrichment; print('import ok')" \
  || { echo "ABORT: new image fails to import; running container untouched."; exit 1; }

while in_window; do echo "Waiting for :56 UTC..."; sleep 30; done

STAGED_BEFORE=$(ls "$DATA/new-readings" | wc -l)
docker stop sensorpull-run >/dev/null && docker rm sensorpull-run >/dev/null
docker run "${RUN_ARGS[@]}" sensorpull:latest >/dev/null
sleep 8

docker ps --filter name=sensorpull-run --format '{{.Status}}' | grep -q '^Up' || { echo "FAIL: container not up"; exit 1; }
CRON=$(docker exec sensorpull-run sh -c 'crontab -l | grep -c cron-errors')
WANT=$(grep -c cron-errors "$REPO/jobs/cronjobs")
[ "$CRON" = "$WANT" ] || { echo "FAIL: expected $WANT cron lines capturing stderr, found $CRON"; exit 1; }
STAGED_AFTER=$(ls "$DATA/new-readings" | wc -l)
[ "$STAGED_AFTER" -ge "$STAGED_BEFORE" ] || { echo "FAIL: staging files lost ($STAGED_BEFORE -> $STAGED_AFTER)"; exit 1; }

echo "Deployed ${COMMIT} as sensorpull:latest ($(docker inspect sensorpull-run --format '{{.Image}}' | cut -c8-19))."
echo "Rollback: docker stop sensorpull-run && docker rm sensorpull-run && docker run ${RUN_ARGS[*]} sensorpull:${TAG}"
