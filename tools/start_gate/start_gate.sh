#!/usr/bin/env bash
# The start gate of one image: build the RPM from the tree, install it in a clean container, start it.
# usage: tools/start_gate/start_gate.sh IMAGE [TREE]
#   IMAGE  a container image: quay.io/centos/centos:stream10, registry.fedoraproject.org/fedora:43, ...
#   TREE   the git checkout to test; default the checkout this script is in
# Needs podman or docker (START_GATE_ENGINE=docker|podman picks one). Not started by the tests: it
# downloads packages (CI runs it, see .github/workflows/start-gate.yml, and docs/RELEASING.md says
# when). The scripts it runs in the containers come from this directory, so the same gate can test an
# older tree: TREE may be a checkout of a tag that does not have this directory.
set -euo pipefail

image=${1:?usage: start_gate.sh IMAGE [TREE]}
here=$(cd "$(dirname "$0")" && pwd)
tree=$(cd "${2:-$here/../..}" && pwd)
engine=${START_GATE_ENGINE:-$(command -v podman || command -v docker || true)}
[ -n "$engine" ] || { echo "start_gate.sh: no podman or docker" >&2; exit 2; }
[ -e "$tree/.copr/Makefile" ] || { echo "start_gate.sh: $tree has no .copr/Makefile" >&2; exit 2; }

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
chmod 777 "$out"

echo "== stage 1: build the RPM from $tree in $image"
"$engine" run --rm -v "$tree":/src:ro -v "$here":/gate:ro -v "$out":/out "$image" bash /gate/build.sh
echo "== stage 2: install and start it in a clean $image"
# the tag of a run for a tag is passed on (a variable with no value is copied from here, if it is set)
"$engine" run --rm -e GITHUB_REF_TYPE -e GITHUB_REF_NAME \
    -v "$here":/gate:ro -v "$out":/out:ro "$image" bash /gate/check.sh
