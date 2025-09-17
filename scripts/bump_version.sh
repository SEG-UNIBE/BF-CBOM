#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <semver e.g., 0.3.2>" >&2
  exit 1
fi

V="$1"

# Always operate from repo root (not scripts/)
ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT_DIR"

echo "Bumping version to: $V"

# 1) Write VERSION file
echo "$V" > VERSION

# 2) Update pyproject.toml version
if [ -f pyproject.toml ]; then
  echo "Updating pyproject.toml ..."
  awk -v ver="$V" '
    BEGIN { inproj=0 }
    /^\[project\]/ { inproj=1 }
    /^\[/ && $0 !~ /^\[project\]/ { inproj=0 }
    inproj && /^[[:space:]]*version[[:space:]]*=/ {
      sub(/version[[:space:]]*=[[:space:]]*"[^\"]*"/, "version = \"" ver "\"")
      inproj=0
    }
    { print }
  ' pyproject.toml > pyproject.toml.__new__ && mv pyproject.toml.__new__ pyproject.toml
else
  echo "pyproject.toml not found in repo root ($ROOT_DIR); skipping."
fi

# 3) Refresh uv.lock so version is consistent
if command -v uv >/dev/null 2>&1; then
  echo "Syncing uv.lock ..."
  uv sync --frozen --no-dev || true
else
  echo "uv not found; skipping lockfile refresh."
fi

# # 3) Commit and tag
# git add VERSION pyproject.toml 2>/dev/null || true
# if ! git diff --cached --quiet; then
#   git commit -m "chore(release): bump version to v$V"
# else
#   echo "Nothing to commit (version already set?)."
# fi

# # Create or move tag to new value
# if git rev-parse "v$V" >/dev/null 2>&1; then
#   echo "Tag v$V already exists; updating annotated tag."
#   git tag -fa "v$V" -m "BF-CBOM v$V"
# else
#   git tag -a "v$V" -m "BF-CBOM v$V"
# fi

# git push --follow-tags
