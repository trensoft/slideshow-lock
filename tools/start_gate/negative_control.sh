#!/usr/bin/env bash
# The negative control of the start gate: on the tree of 1.0.11, the release that went out with the
# start-up error, the gate must FAIL, and for the reason of 1.0.11. A gate that cannot fail on the
# known bad tree is not a gate; one that fails there for another reason is not the proof either.
# usage: tools/start_gate/negative_control.sh IMAGE TREE
#   IMAGE  the image of a leg where 1.0.11 is known to fail: quay.io/centos/centos:stream10 (epel-10)
#   TREE   a git checkout of the commit of the tag v1.0.11 (adb4c224d32bebbda3a4f76a20d41ce9a7d338b6)
# Exit status 0 only when start_gate.sh ran both stages and the output of stage 2 has, each as a line
# of its own: "START-GATE a PASS" and "START-GATE b PASS" (the install and the import went through),
# "START-GATE c FAIL AttributeError: ... signal_add" (the signal watch that 1.0.11 broke: GLibUnix has
# no signal_add on EL10) and the summary "START-GATE RESULT FAIL". A gate that passes is soft (exit 1).
# A gate that stopped before stage 2 ended, or failed for another reason (no network, a build error,
# a step (c) that fails with another error, a step before (c) that fails), proves nothing and is not
# a pass either (exit 1).
set -uo pipefail

image=${1:?usage: negative_control.sh IMAGE TREE}
tree=${2:?usage: negative_control.sh IMAGE TREE}
here=$(cd "$(dirname "$0")" && pwd)
log=$(mktemp)
trap 'rm -f "$log"' EXIT

bash "$here/start_gate.sh" "$image" "$tree" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}

if [ "$rc" -eq 0 ]; then
    echo "NEGATIVE-CONTROL FAIL: the gate PASSED on $tree; it is soft, it does not catch the 1.0.11 error"
    exit 1
fi
# every line is anchored: a line of the build log that only contains the words does not count
if grep -q '^START-GATE RESULT FAIL' "$log" \
    && grep -q '^START-GATE a PASS' "$log" \
    && grep -q '^START-GATE b PASS' "$log" \
    && grep -q '^START-GATE c FAIL AttributeError: .*signal_add' "$log"; then
    echo "NEGATIVE-CONTROL PASS: the gate failed on $tree in step (c) with the error of 1.0.11, as it must"
    exit 0
fi
echo "NEGATIVE-CONTROL FAIL: the gate failed (status $rc) but not as 1.0.11 does (steps (a) and (b) pass," \
    "step (c) fails with the AttributeError for signal_add, in stage 2): it proves nothing"
exit 1
