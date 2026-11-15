#!/usr/bin/env bash
# Start the pipeline and wait for the result. Exits non-zero unless the run SUCCEEDED.
#   smoke_test.sh <state-machine-arn> [dataset] [max_videos]
set -euo pipefail
ARN="$1"; DATASET="${2:-lerobot/pusht}"; MAX_VIDEOS="${3:-1}"

EXEC=$(aws stepfunctions start-execution --state-machine-arn "$ARN" \
  --input "{\"dataset\":\"${DATASET}\",\"max_videos\":${MAX_VIDEOS}}" --query executionArn --output text)
echo "Started $EXEC"

for _ in $(seq 1 120); do   # up to 40 minutes
  STATUS=$(aws stepfunctions describe-execution --execution-arn "$EXEC" --query status --output text)
  [ "$STATUS" != "RUNNING" ] && break
  sleep 20
done

OUTPUT=$(aws stepfunctions describe-execution --execution-arn "$EXEC" --query output --output text)
echo "Status: $STATUS"
echo "$OUTPUT" | python3 -c 'import json,sys
raw=sys.stdin.read().strip()
if raw and raw!="None":
    d=json.loads(raw); q=d.get("quality",{}).get("result",{})
    print("Quality verdict:", q.get("verdict"), "-", q.get("summary"))' || true
[ "$STATUS" = "SUCCEEDED" ]
