#!/bin/bash
# Start gate, stage 2: install the RPM into a clean container and start the program from the
# installed copy. Runs as root in a fresh container of the image under test, which has neither the
# source tree nor the build tools of stage 1.
#   /gate this directory
#   /out  the output of build.sh (read only is enough)
# (a) the installed version is the RPM's, (b) the modules import, (c) a real SIGTERM and SIGINT reach
# the installed unix_signal_add in a real GLib main loop, (d) the real `slideshow-lock service` comes
# up to its main loop and ends with status 0 on SIGTERM; on a tag run the tag is v<version>. The
# steps all run; the exit status is 1 if one failed. The last line, "START-GATE RESULT ...", is the
# summary.
set -euo pipefail
. /gate/common.sh

gate_repos
shopt -s nullglob
rpms=(/out/rpm/slideshow-lock-*.noarch.rpm)
[ "${#rpms[@]}" -eq 1 ] || { echo "START-GATE RESULT FAIL: expected one RPM in /out/rpm, found ${#rpms[@]}"; exit 1; }
rpm_file=${rpms[0]}
nevra=$(basename "$rpm_file" .rpm)
version=${nevra#slideshow-lock-}
version=${version%%-*}
sha=$(sha256sum "$rpm_file" | cut -d' ' -f1)
echo "gate: $nevra sha256 $sha"

# no weak dependencies: the program must start without the optional packages (sound, extra pixbuf loaders)
gate_dnf install --setopt=install_weak_deps=false "$rpm_file" dbus-daemon
set +e

status=0
# the install is checked with rpm: an image with no rpm cannot pass, the step is not left out
if command -v rpm >/dev/null 2>&1; then
    installed=$(rpm -q --qf '%{NVRA}' slideshow-lock)
    if [ "$installed" = "$nevra" ]; then
        echo "START-GATE rpm PASS rpm -q slideshow-lock: $installed"
    else
        echo "START-GATE rpm FAIL rpm -q gives $installed, the file is $nevra"
        status=1
    fi
else
    echo "START-GATE rpm FAIL there is no rpm in the image: the install cannot be checked"
    status=1
fi

# a run for a tag (GITHUB_REF_TYPE and GITHUB_REF_NAME come from the runner through start_gate.sh):
# the tag must be v<version of the RPM>, so a tag and a pyproject.toml that differ do not pass
if [ "${GITHUB_REF_TYPE:-}" = "tag" ]; then
    if [ "${GITHUB_REF_NAME:-}" = "v$version" ]; then
        echo "START-GATE tag PASS the tag $GITHUB_REF_NAME is v$version"
    else
        echo "START-GATE tag FAIL the tag is ${GITHUB_REF_NAME:-unset}, the RPM has version $version"
        status=1
    fi
fi

cd "$(mktemp -d)" || exit 1
python3 -I /gate/probe_installed.py "$version" || status=1
python3 -I /gate/probe_service.py || status=1

if [ "$status" -eq 0 ]; then
    echo "START-GATE RESULT PASS $nevra sha256 $sha"
else
    echo "START-GATE RESULT FAIL $nevra sha256 $sha"
fi
exit "$status"
