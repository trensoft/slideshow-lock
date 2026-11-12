#!/bin/bash
# Start gate, stage 1: build the RPM. Runs as root in a container of the image under test.
#   /src  the tree under test, a git checkout (read only is enough)
#   /gate this directory
#   /out  the output: /out/rpm/slideshow-lock-*.noarch.rpm
# The source RPM is made by .copr/Makefile from /src (git archive of HEAD, the same file COPR runs),
# its build requirements are installed with dnf, and rpmbuild --rebuild makes the binary RPM. This
# does not run the program: stage 2 (check.sh) installs the RPM in a clean container and starts it.
set -euo pipefail
. /gate/common.sh

# the files made here belong to root: whoever started the container must be able to remove them
trap 'chmod -R a+rwX /out' EXIT

gate_repos
gate_dnf install rpm-build git-core gzip make
# dnf5 (Fedora) has the builddep command in dnf5-plugins, dnf4 (EL10) in dnf-plugins-core
gate_dnf install dnf5-plugins || gate_dnf install dnf-plugins-core

mkdir -p /out/srpm /out/build /out/rpm
make -f /src/.copr/Makefile srpm outdir=/out/srpm
srpm=$(ls /out/srpm/*.src.rpm)
gate_dnf builddep "$srpm"

# %generate_buildrequires (pyproject) names more build requirements once the first are installed:
# rpmbuild stops with 11 and a .buildreqs.nosrc.rpm, which is installed with builddep, then once more
rc=0
rpmbuild --rebuild --define "_topdir /out/build" "$srpm" || rc=$?
if [ "$rc" -eq 11 ]; then
    gate_dnf builddep /out/build/SRPMS/*.buildreqs.nosrc.rpm
    rpmbuild --rebuild --define "_topdir /out/build" "$srpm"
elif [ "$rc" -ne 0 ]; then
    exit "$rc"
fi

cp /out/build/RPMS/noarch/slideshow-lock-*.noarch.rpm /out/rpm/
ls -l /out/rpm
