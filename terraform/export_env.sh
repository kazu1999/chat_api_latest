#!/usr/bin/env bash

# Usage: source terraform/export_env.sh
# Requires: terraform already applied in this directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform not found" >&2
  return 1 2>/dev/null || exit 1
fi

pushd "$SCRIPT_DIR" >/dev/null

TABLE_NAME=$(terraform output -raw table_name)
REGION=$(terraform output -raw region)
REALTIME_URL=$(terraform output -raw ueki_realtime_function_url 2>/dev/null || echo "")

export DDB_TABLE_NAME="$TABLE_NAME"
export AWS_REGION="$REGION"

echo "Exported DDB_TABLE_NAME=$DDB_TABLE_NAME"
echo "Exported AWS_REGION=$REGION"

if [ -n "$REALTIME_URL" ]; then
  export UEKI_REALTIME_FUNCTION_URL="$REALTIME_URL"
  echo "Exported UEKI_REALTIME_FUNCTION_URL=$REALTIME_URL"
fi

popd >/dev/null
