#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_VERCEL_PROJECT_ID="prj_ISXZ98mUW2ymsbiBvIKfLIWNDeog"

cd "$REPOSITORY_ROOT"

if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing frontend deployment: the Git working tree is not clean." >&2
  exit 1
fi

RELEASE_SHA="$(git rev-parse HEAD)"
BRANCH_NAME="$(git branch --show-current)"
if [ "$BRANCH_NAME" != "main" ] && [[ "$BRANCH_NAME" != codex/mobile-only-* ]]; then
  echo "Refusing frontend deployment from unexpected branch: $BRANCH_NAME" >&2
  exit 1
fi

npx vercel link --yes --scope offsetows-projects --project swiftchart
LINKED_PROJECT_ID="$(node -e 'console.log(require("./.vercel/project.json").projectId)')"
if [ "$LINKED_PROJECT_ID" != "$EXPECTED_VERCEL_PROJECT_ID" ]; then
  echo "Refusing frontend deployment: Vercel is linked to $LINKED_PROJECT_ID." >&2
  exit 1
fi

cd frontend
npm test
SWIFTCHART_RELEASE_SHA="$RELEASE_SHA" npm run build
cd "$REPOSITORY_ROOT"

npx vercel --prod --yes --scope offsetows-projects \
  --build-env "SWIFTCHART_RELEASE_SHA=$RELEASE_SHA"

echo "Frontend release deployed from $RELEASE_SHA"
