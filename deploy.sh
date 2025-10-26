#!/usr/bin/env bash
# ./deploy.sh
# Glue script to run the Dagger-style local workflow and then run the git-based deploy adapter.
#
# Modes:
#   - git-source (default): commit/push current HEAD to remote branch using deploy-adapter-git.py
#   - git-artifact: build artifacts (e.g., ./dist) and push artifact contents to remote branch
#
# Usage examples:
#   # push current HEAD to remote branch `main`
#   ./deploy.sh --remote git@backend.im:org/app.git --branch main
#
#   # build and push artifact dir (dist) into remote branch
#   ./deploy.sh --remote git@backend.im:org/app.git --branch main --mode git-artifact --artifact-dir ./dist
#
# Environment variables:
#   BACKEND_IM_TOKEN   optional, used if deploy-adapter polls a status URL
##  GIT_SSH_KEY or GIT_SSH_KEY_BASE64 optional: SSH private key for pushing to remote
#
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "$0")" && pwd)
DEVOPS_DIR="$ROOT_DIR"
DAGGER_PY="$DEVOPS_DIR/dagger.py"
ADAPTER_PY="$DEVOPS_DIR/deploy-adapter.py"

if [ ! -f "$DAGGER_PY" ]; then
  echo "Error: $DAGGER_PY not found. Make sure ./dagger.py exists." >&2
  exit 2
fi
if [ ! -f "$ADAPTER_PY" ]; then
  echo "Error: $ADAPTER_PY not found. Make sure ./deploy-adapter-git.py exists." >&2
  exit 2
fi

# defaults
MODE="git-source"
REMOTE=""
BRANCH="main"
ARTIFACT_DIR=""
SKIP_PUSH=false
FORCE_PUSH=false
ALLOW_DIRTY=false
STATUS_URL=""
STATUS_TIMEOUT=300

function usage(){
  cat <<EOF
Usage: $0 --remote <git-remote> [--branch <branch>] [--mode git-source|git-artifact] [--artifact-dir <dir>] [--skip-push]

Options:
  --remote       Git remote for Backend.im (required) e.g. git@backend.im:org/app.git
  --branch       Target branch on remote (default: main)
  --mode         git-source (push current HEAD) or git-artifact (push artifact-dir contents). Default: git-source
  --artifact-dir Directory to push to remote when mode=git-artifact (e.g., ./dist)
  --skip-push    Build but do not invoke the deploy adapter (useful for testing)
  --force        Force push the branch
  --allow-dirty  Allow pushing when working tree is dirty
  --status-url   Optional deploy status URL to poll after push
  --status-timeout Poll timeout in seconds (default: 300)
  -h|--help      Show this help

Example:
  $0 --remote git@backend.im:org/app.git --branch main
  $0 --remote git@backend.im:org/app.git --mode git-artifact --artifact-dir ./dist
EOF
}

# parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --artifact-dir) ARTIFACT_DIR="$2"; shift 2;;
    --skip-push) SKIP_PUSH=true; shift 1;;
    --force) FORCE_PUSH=true; shift 1;;
    --allow-dirty) ALLOW_DIRTY=true; shift 1;;
    --status-url) STATUS_URL="$2"; shift 2;;
    --status-timeout) STATUS_TIMEOUT="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [ -z "$REMOTE" ]; then
  echo "Error: --remote is required" >&2
  usage
  exit 2
fi

# Step 1: run dagger build pipeline to generate artifacts (optional)
echo "[deploy.sh] Running dagger workflow to build/test/package..."
# Call dagger.py; capture its last line output 'artifact:<image_ref>' if any
set +e
DAGGER_OUTPUT=$(py -3 "$DAGGER_PY" --image "ghcr.io/yourorg/yourapp" 2>&1)
DAGGER_EXIT=$?
set -e

if [ $DAGGER_EXIT -ne 0 ]; then
  echo "d dagger.py failed:\n$DAGGER_OUTPUT" >&2
  exit $DAGGER_EXIT
fi

# Echo dagger logs for transparency
echo "$DAGGER_OUTPUT"

# Try to parse artifact reference from dagger output (line like: artifact:ghcr.io/org/app:sha)
ARTIFACT_REF=""
while IFS= read -r line; do
  if [[ "$line" == artifact:* ]]; then
    ARTIFACT_REF="${line#artifact:}"
  fi
done <<< "$DAGGER_OUTPUT"

if [ -n "$ARTIFACT_REF" ]; then
  echo "[deploy.sh] Detected artifact ref: $ARTIFACT_REF"
else
  echo "[deploy.sh] No artifact ref detected from dagger output — continuing (mode: $MODE)"
fi

# Step 2: depending on mode, call deploy-adapter-git
if [ "$SKIP_PUSH" = true ]; then
  echo "[deploy.sh] --skip-push set; build completed but not invoking deploy adapter. Exiting."
  exit 0
fi

ADAPTER_CMD=(py -3 "$ADAPTER_PY" --remote "$REMOTE" --branch "$BRANCH")
if [ "$MODE" = "git-artifact" ]; then
  if [ -z "$ARTIFACT_DIR" ]; then
    # default to ./dist
    ARTIFACT_DIR="$ROOT_DIR/dist"
  fi
  if [ ! -d "$ARTIFACT_DIR" ]; then
    echo "Error: artifact-dir '$ARTIFACT_DIR' does not exist" >&2
    exit 2
  fi
  ADAPTER_CMD+=(--artifact-dir "$ARTIFACT_DIR")
fi
if [ "$FORCE_PUSH" = true ]; then
  ADAPTER_CMD+=(--force)
fi
if [ "$ALLOW_DIRTY" = true ]; then
  ADAPTER_CMD+=(--allow-dirty)
fi
if [ -n "$STATUS_URL" ]; then
  ADAPTER_CMD+=(--status-url "$STATUS_URL" --status-timeout "$STATUS_TIMEOUT")
fi

echo "[deploy.sh] Invoking deploy adapter: ${ADAPTER_CMD[*]}"
# Use eval to preserve quoted args
eval "${ADAPTER_CMD[*]}"

EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
  echo "[deploy.sh] Deploy adapter failed with exit code $EXIT_CODE" >&2
  exit $EXIT_CODE
fi

echo "[deploy.sh] Deploy adapter finished. Check Backend.im dashboard or the status URL for deploy progress." 
