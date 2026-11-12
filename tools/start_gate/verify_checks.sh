#!/usr/bin/env bash
# The tag condition of docs/RELEASING.md ("The start gate"), checked by a machine: before the tag, is
# the start gate green on every chroot of the COPR project, on this commit?
# usage: tools/start_gate/verify_checks.sh SHA     (SHA: the 40 hex digits of the commit to be tagged)
#   1. the chroots of the COPR project are read from the COPR API (not from a file of this tree);
#   2. the check runs of the commit are read from the GitHub API;
#   3. for every COPR chroot there must be a check "start gate (<chroot>)" of GitHub Actions that is
#      completed with the conclusion success, and there must be no such check for a chroot that the
#      COPR project does not have (the matrix of the workflow and the COPR project are one list).
# Exit status 0 only when all of it holds. Anything that cannot be read (no network, an API error, no
# python3, an empty answer, more than 100 checks) is a failure, not a pass: no answer, no tag.
# GITHUB_TOKEN (optional) is sent to the GitHub API, which limits anonymous requests.
# START_GATE_REPO, START_GATE_COPR_OWNER, START_GATE_COPR_PROJECT, START_GATE_GITHUB_API and
# START_GATE_COPR_API change where it looks; they are for the tests of this script.
set -euo pipefail

sha=${1:-}
if ! [[ $sha =~ ^[0-9a-f]{40}$ ]]; then
    echo "usage: verify_checks.sh SHA (the 40 hex digits of the commit to be tagged)" >&2
    exit 2
fi

repo=${START_GATE_REPO:-trensoft/slideshow-lock}
copr_owner=${START_GATE_COPR_OWNER:-trensoft}
copr_project=${START_GATE_COPR_PROJECT:-slideshow-lock}
github_api=${START_GATE_GITHUB_API:-https://api.github.com}
copr_api=${START_GATE_COPR_API:-https://copr.fedorainfracloud.org/api_3}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fail() {
    echo "VERIFY-CHECKS FAIL $sha: $*"
    exit 1
}

copr_url="$copr_api/project/?ownername=$copr_owner&projectname=$copr_project"
checks_url="$github_api/repos/$repo/commits/$sha/check-runs?filter=latest&per_page=100"
echo "COPR project: $copr_url"
echo "check runs:   $checks_url"

curl --fail --silent --show-error --location --max-time 60 -o "$work/copr.json" "$copr_url" \
    || fail "the COPR project could not be read"
auth=()
[ -z "${GITHUB_TOKEN:-}" ] || auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
curl --fail --silent --show-error --location --max-time 60 -o "$work/checks.json" \
    -H "Accept: application/vnd.github+json" ${auth[@]+"${auth[@]}"} "$checks_url" \
    || fail "the check runs of the commit could not be read"

python3 - "$work/copr.json" "$work/checks.json" <<'PY' || fail "see above"
import json
import re
import sys

copr = json.load(open(sys.argv[1], encoding="utf-8"))
checks = json.load(open(sys.argv[2], encoding="utf-8"))

repos = copr.get("chroot_repos")
if not isinstance(repos, dict) or not repos:
    sys.exit("the COPR answer has no chroot_repos")
arch = re.compile(r"-(x86_64|aarch64|ppc64le|s390x|i386|armhfp)$")
wanted = sorted({arch.sub("", name) for name in repos})

runs = checks.get("check_runs")
if not isinstance(runs, list) or checks.get("total_count") != len(runs):
    sys.exit("the check runs are not complete (total_count %r, %r listed)" % (
        checks.get("total_count"), len(runs) if isinstance(runs, list) else None))
leg = re.compile(r"^start gate \((.+)\)$")
found = {}
for run in runs:
    match = leg.match(run.get("name", ""))
    if match:
        found.setdefault(match.group(1), []).append(run)

problems = []
for chroot in wanted:
    legs = found.get(chroot, [])
    if len(legs) != 1:
        problems.append("%s: %d checks named 'start gate (%s)', one is needed" % (chroot, len(legs), chroot))
        continue
    run = legs[0]
    slug = (run.get("app") or {}).get("slug")
    state = "%s/%s" % (run.get("status"), run.get("conclusion"))
    print("  %-16s %s%s" % (chroot, state, "" if slug == "github-actions" else " (app %r)" % slug))
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        problems.append("%s: %s, not completed/success" % (chroot, state))
    elif slug != "github-actions":
        problems.append("%s: the check is not from GitHub Actions (app %r)" % (chroot, slug))
for chroot in sorted(set(found) - set(wanted)):
    problems.append("%s: a leg with no chroot in the COPR project (COPR has %s)" % (chroot, ", ".join(wanted)))
if problems:
    sys.exit("\n".join(problems))
print("COPR chroots: %s" % ", ".join(wanted))
PY

echo "VERIFY-CHECKS PASS $sha: every chroot of the COPR project has its start gate green"
