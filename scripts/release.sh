#!/usr/bin/env bash
# Cut a release: check, bump, tag, push. The workflow does the rest.
#
#   scripts/release.sh 0.6.0 [notes.md]
#
# Everything that can be verified before a version number becomes permanent is
# verified here, because a PyPI version cannot be reused and a tag that shipped is
# a tag people have. Nothing is pushed until every check passes; the last thing the
# script does is the only irreversible thing in it.
set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\033[36m··\033[0m %s\n' "$*"; }

version=${1:-}
notes=${2:-}
[[ -n $version ]] || die "usage: scripts/release.sh <version> [notes-file]"
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([abrc.0-9]+)?$ ]] || die "'$version' is not a version"
# checked here rather than where it is used: nothing should fail after the bump
[[ -z $notes || -f $notes ]] || die "no such notes file: $notes"

root=$(git rev-parse --show-toplevel)
cd "$root"
tag="v$version"

step "checking the working tree"
[[ -z $(git status --porcelain) ]] || die "uncommitted changes — commit or stash them first"
branch=$(git rev-parse --abbrev-ref HEAD)
[[ $branch == main ]] || die "on '$branch'; a release is cut from main"
git fetch --quiet origin main
[[ -z $(git log origin/main..HEAD --oneline) ]] && [[ -z $(git log HEAD..origin/main --oneline) ]] \
  || step "local main and origin/main differ — the push below will reconcile them"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "$tag already exists locally"
git ls-remote --exit-code --tags origin "$tag" >/dev/null 2>&1 && die "$tag already exists on origin"

step "linting, formatting and typechecking"
uv run --group dev ruff check src tests
uv run --group dev ruff format --check src tests
uv run --group dev pyright

step "running the tests"
uv run --group dev pytest

step "parsing the web UI's script (it is not linted anywhere else)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
uv run python -c "
import pathlib, re, sys
html = pathlib.Path('src/proxdex/webui.html').read_text()
blocks = re.findall(r'<script>(.*?)</script>', html, re.S)
sys.exit('no script block found') if not blocks else None
pathlib.Path('$tmp/ui.js').write_text('\n'.join(blocks))
"
node --check "$tmp/ui.js"

step "bumping to $version"
printf '__version__ = "%s"\n' "$version" > src/proxdex/_version.py
uv run --group dev ruff format -q src/proxdex/_version.py
reported=$(uv run proxdex --version | awk '{print $NF}')
[[ $reported == "$version" ]] || die "proxdex reports $reported after the bump"

step "building the wheel"
rm -rf dist
uv build --quiet
uv run python -c "
import zipfile, sys
names = zipfile.ZipFile(sorted(__import__('pathlib').Path('dist').glob('*.whl'))[0]).namelist()
missing = [n for n in ('proxdex/webui.html', 'proxdex/static/bootstrap.min.css') if not any(x.endswith(n) for x in names)]
sys.exit('wheel is missing: ' + ', '.join(missing)) if missing else print(f'  {len(names)} entries, data files present')
"

if [[ -n $notes ]]; then
  git commit --quiet -am "Release $tag" || die "nothing to commit — is the version already $version?"
  git tag -a "$tag" -F "$notes"
else
  git commit --quiet -am "Release $tag"
  step "tagging — the message you write becomes the GitHub release notes"
  git tag -a "$tag"
fi

step "pushing main and $tag"
git push origin main
git push origin "$tag"

printf '\033[32m✓\033[0m %s is on its way — checks, PyPI, then the GitHub release.\n' "$tag"
step "watching: gh run watch --exit-status \$(gh run list --workflow Release --limit 1 --json databaseId --jq '.[0].databaseId')"
