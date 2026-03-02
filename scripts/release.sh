#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 0.2.0"
  exit 1
fi

VERSION="$1"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must be SemVer without prefix (e.g. 0.2.0)"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT_DIR}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree must be clean before release."
  exit 1
fi

echo "[release] Updating version to ${VERSION}"
python3 - <<PY
from pathlib import Path
import re

version = "${VERSION}"

init_file = Path("src/larops/__init__.py")
text = init_file.read_text(encoding="utf-8")
text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text)
init_file.write_text(text, encoding="utf-8")

pyproject = Path("pyproject.toml")
text = pyproject.read_text(encoding="utf-8")
text = re.sub(r'version = "[^"]+"', f'version = "{version}"', text, count=1)
pyproject.write_text(text, encoding="utf-8")
PY

LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
if [[ -n "${LAST_TAG}" ]]; then
  CHANGES="$(git log --pretty=format:'- %h %s' "${LAST_TAG}"..HEAD)"
else
  CHANGES="$(git log --pretty=format:'- %h %s')"
fi
if [[ -z "${CHANGES}" ]]; then
  CHANGES="- Initial release baseline."
fi

TODAY="$(date +%Y-%m-%d)"
export LAROPS_RELEASE_VERSION="${VERSION}"
export LAROPS_RELEASE_TODAY="${TODAY}"
export LAROPS_RELEASE_CHANGES="${CHANGES}"
python3 - <<'PY'
import os
from pathlib import Path

path = Path("CHANGELOG.md")
existing = path.read_text(encoding="utf-8") if path.exists() else "# Changelog\n\n"
version = os.environ["LAROPS_RELEASE_VERSION"]
today = os.environ["LAROPS_RELEASE_TODAY"]
changes_text = os.environ["LAROPS_RELEASE_CHANGES"]
header = f"## v{version} - {today}\n\n"
changes = changes_text.strip() + "\n\n"

if header in existing:
    raise SystemExit("Release section already exists in CHANGELOG.md")

if existing.startswith("# Changelog"):
    parts = existing.split("\n", 2)
    prefix = parts[0] + "\n\n"
    rest = existing[len(prefix):]
    updated = prefix + header + changes + rest
else:
    updated = "# Changelog\n\n" + header + changes + existing

path.write_text(updated, encoding="utf-8")
PY

git add src/larops/__init__.py pyproject.toml CHANGELOG.md
git commit -m "release: v${VERSION}"
git tag -a "v${VERSION}" -m "Release v${VERSION}"

echo "[release] Created commit and tag v${VERSION}"
echo "[release] Push commands:"
echo "  git push origin main"
echo "  git push origin v${VERSION}"
