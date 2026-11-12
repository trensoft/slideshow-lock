# Releasing

The standing procedure for a release. It is a rule for the project; except for the dry run below,
nothing here has been run for 1.0.2 yet: the tag `v1.0.2` does not exist (`git ls-remote --tags origin`
lists `v1.0.0` and `v1.0.1` only, 2026-10-07), so no step after "go" has run. What is known of the
COPR builds of 1.0.0 is in the STATUS at the top of the spec.
`[H]` marks background knowledge or a claim about a server that was not measured.

"The releaser" below is the person who runs the release: they write the report, push the tag and
check the build.

Not touched by this procedure, and done by the maintainer only when needed: the COPR project, the
Fedora account, the webhook and the repository permissions.

## What may be built in the COPR project

The maintainer's rule, set on 2026-10-07.

1. Only a tagged release that has the approval of the maintainer is built in the COPR project
   `trensoft/slideshow-lock`. Since 2026-10-09 that approval is the standing one in "Before the tag"
   below, not a separate "go" for each release.
2. No test build is started in this project: not by a push, not by a manual Rebuild, not by hand.
   The reason: the regular update on the maintainer's machines installs whatever appears there with
   a higher version, so a test build would be installed there as if it were a release.
3. If a test needs a build, the maintainer is told and makes a separate project (for example
   `slideshow-lock-testing`). The releaser does not make one.
4. The version of a release is always higher than that of any earlier build, test builds included.
   A test build gets a lower `Release` than the release, for example `1.0.2-0.1.test` against
   `1.0.2-1`, and never the `Release` of the release. The lower `Release` is set only in the build
   that is tested, not on `main`: `main` has the `Release` of the release. Measured twice,
   by two people, on 2026-10-07 in the same local, rootless CentOS Stream 10 build root (`rpm`
   4.19.1.1), with the strings of 1.0.1, not in the COPR project and not as a COPR build:

   ```
   rpm --eval '%{lua:print(rpm.vercmp("1.0.1-0.1.test","1.0.1-1"))}'
   rpm --eval '%{lua:print(rpm.vercmp("1.0.1-0.1.test.el10","1.0.1-1.el10"))}'
   ```

   Both give -1, so the release updates the test build. Not measured: a Fedora root.

1.0.0 had no such rule: its test builds and the release were all `1.0.0-1`, so the maintainer had to
reinstall by hand.

## Before the tag: the gate and the standing approval

The maintainer's standing rule, given on 2026-10-09 (the maintainer, Telegram; read by the team lead
and passed on to the releaser; the wording is not in this repository, so it is not checked from
here): a release of this project no longer waits for a separate "go" of the maintainer. It is
tagged and pushed at once when all three of these hold:

1. The team gate is green for the same final tree: the two reviewers of the team gate and, if
   the change touches security, the security reviewer as well. A gate for another tree does not
   count: if the tree changes after the gate, the gate is made again for the new tree SHA. The
   identifier of the gate is the tree SHA (`git rev-parse HEAD^{tree}`): the commit SHA can change
   when a patch is applied with `git am`, because the committer field and its date are new, while
   the tree stays the same.
2. The version bump is in the commit: the version in `pyproject.toml`, in
   `packaging/fedora/slideshow-lock.spec` (`Version:` and the `%changelog` entry) and in the
   `<release>` element of the metainfo is the tag without the `v`, and the date in the `%changelog`
   entry and in the metainfo is the day of the tag. A later `-2` entry of the same version (a new
   spec release with no change upstream) is dated on its own day; the metainfo keeps the day of the
   `-1` entry, and `tests/test_version.py` compares the two.
3. The start gate (the section "The start gate" below) is green on the commit that is tagged, on
   every leg of its matrix, `epel-10` first. A leg that did not run is not green: if one chroot did
   not run, there is no tag. The proof is the output of `tools/start_gate/verify_checks.sh <sha>`
   (`VERIFY-CHECKS PASS`), run for that commit before the tag. It is the
   required step before the tag, and with no output of it there is no tag.

When all three hold, the releaser pushes the tag, waits for the COPR build and checks every chroot (see
below), and then sends a done-message to the team lead. No build is made in the COPR project before
the tag, so the first build of the release is the build of the tag, on the commit that passed the
gate.

The done-message has four parts:

1. The version and the commit SHA that was tagged.
2. What changed, in 3 to 5 lines.
3. Whether the commit is green: the tests, `rpmlint`, and the build on Fedora and on EPEL 10, per
   chroot. A build for this is not made in the COPR project before the tag (see above); the message
   says where it was made, or that it was not.
4. What was not checked and what has to be tried by hand.

Not changed by this rule: the COPR project, the Fedora account, the webhook and the repository
permissions are not touched by the releaser (see the top of this file), the rules in "What may be
built in the COPR project" stay, and a failed step does not delete or move the tag (see "A failed
step").

Check the version before the tag. (The version shown in the settings window is read from
`pyproject.toml` or the installed package at run time, so a release changes it nowhere else.) The open points about the release version and the tarball
are in `docs/copr.md`, section 4.

## The start gate

1.0.11 went out with a start-up error on EL10 and Fedora 43 (`GLibUnix` has no `signal_add`
there; the program ended before its main loop). The checks of the typelibs and of the imports
had passed: they do not run the start. The start gate runs it, on every chroot that the project
builds for, before the tag.

What it does, per leg (`tools/start_gate/start_gate.sh IMAGE [TREE]`, two containers of the image):

1. Stage 1 (`build.sh`): `.copr/Makefile` makes the source RPM from the commit (the file COPR runs),
   `dnf builddep` installs the build requirements, `rpmbuild --rebuild` makes the RPM. When
   `rpmbuild` ends with 11 (`%generate_buildrequires` names more build requirements, on EL10 in two
   rounds), they are installed and it runs again, at most 5 rounds, then the stage fails.
2. Stage 2 (`check.sh`), in a fresh container that has neither the tree nor the build tools: `dnf`
   installs the RPM with its dependencies and without the weak ones, then, from the installed copy
   (`python3 -I`, outside the tree):
   - (a) `rpm -q` and the installed version are those of the RPM, and the module is the one in the
     system site-packages;
   - (b) `slideshow_lock.service` and `slideshow_lock.preview_app` import;
   - (c) a real SIGTERM and a real SIGINT reach `loop.unix_signal_add` in a real GLib main loop (the
     call 1.0.11 broke);
   - (d) the real `slideshow-lock service` comes up to its main loop, with a private D-Bus session
     bus and a private system bus that has `tools/start_gate/fake_login1.py` as logind, and ends with
     exit status 0 on SIGTERM. A traceback in its log fails the step.

The workflow is `.github/workflows/start-gate.yml`: on every pull request, on a push to `main` and
on a push of a `v*` tag, one job per leg, named `start gate (<chroot>)`. The push to `main` is the
run on the commit that is tagged. In short, the push of a `v*` tag is not a gate: it runs after the tag
exists, so it can only report (and it checks that the tag is `v<version of the RPM>`); the COPR
build of the tag does not wait for it. What a tag waits for is `verify_checks.sh`, below. The
workflow has no run by hand and no job with an `if`: a skipped job would leave a check on the commit
that is not a leg. The negative control is a workflow of its own, see below.
The images are the ones of the COPR chroots:

| Leg (COPR chroot) | Image |
|---|---|
| `epel-10` | `quay.io/centos/centos:stream10`, with EPEL 10 and CRB turned on (`pyproject-rpm-macros` and `python3-wheel` are in CRB) |
| `fedora-43` | `registry.fedoraproject.org/fedora:43` |
| `fedora-44` | `registry.fedoraproject.org/fedora:44` |
| `fedora-45` | `registry.fedoraproject.org/fedora:45` |
| `fedora-rawhide` | `registry.fedoraproject.org/fedora:rawhide` |

Start gate chroots (the same as the chroots of the COPR project `trensoft/slideshow-lock`): `epel-10`, `fedora-43`, `fedora-44`, `fedora-45`, `fedora-rawhide`.

The matrix and the chroot list of the COPR project are the same list. When a chroot is added to the
COPR project or taken out of it, the matrix is changed in the same change, and the line above with
it (`tests/test_start_gate_workflow.py` compares the line with the matrix). A chroot with no leg is a chroot
whose start nobody has run.

The required step before the tag is `tools/start_gate/verify_checks.sh <sha>`, with the 40 hex
digits of the commit to be tagged, that is `"$(git rev-parse HEAD)"` of the commit that is tagged
and not the head of a pull request (the runs of a pull request are on its head and test the merge
ref); the tag is made on the same SHA (`GITHUB_TOKEN` in the environment is used if there is one). It
reads two APIs: the chroot list of the COPR project is read from the COPR API (not from this file or
from the workflow, so a chroot added to the project or renamed there is seen), and the check runs of
the commit from the GitHub API. For every COPR chroot there must be exactly one check
`start gate (<chroot>)` of GitHub Actions that is completed with the conclusion `success`; a leg for
a chroot that the COPR project has not fails it too. A check that is red, missing, still running,
cancelled, skipped or from another app is "did not run". The script cuts the architecture off the chroot names from a list (`x86_64`, `aarch64`, `ppc64le`,
`s390x`, `i386`, `armhfp`): a chroot of an architecture that is not in the list, `riscv64` for
example, is a leg that the script does not know and it fails; the architecture is added to the list
when the COPR project gets one. If an API cannot be read the script fails:
no answer, no tag. The done-message names the commit and the output. A red leg is not run again
until it turns green: the cause is found, and the change goes through the team gate again (see
above).

The script is the rule of the procedure, not a lock: that the tag cannot be pushed with a red or a
missing check at all is a setting of the repository (a required status check on the five names, a
tag rule), which is a decision of the maintainer's side and is not made by this file.

The negative control is `tools/start_gate/negative_control.sh IMAGE TREE`: the gate on the tree of
1.0.11, which must fail as 1.0.11 does. It is green only when the output of stage 2 has, each as a
line of its own, `START-GATE a PASS`, `START-GATE b PASS`, `START-GATE c FAIL AttributeError: ...
signal_add` and `START-GATE RESULT FAIL`: the gate failed on that tree, on step (c), for the reason
of 1.0.11 (`GLibUnix` has no `signal_add`), after the install and the import went through. A gate
that failed for another reason (a build error, no network, `gi` missing, a step (a) or (b) that
fails, a (c) that fails with another error) is not the proof, and the control is red. The workflow
is `.github/workflows/negative-control.yml`, a file of its own and run by hand only ("Run workflow",
`workflow_dispatch`): it leaves no check named `start gate (<chroot>)` on the commit, so
`verify_checks.sh` has nothing extra to count. It runs the gate scripts of the ref against the tree
of the commit `adb4c224d32bebbda3a4f76a20d41ce9a7d338b6`, the commit of the tag `v1.0.11`, by its
SHA and not by the tag (a tag can be moved to a tree that passes, and the control would call the gate
soft), on the image of `epel-10`. Start it when the gate is changed and before the first tag that
depends on a changed gate.

The gate can be run by hand: `START_GATE_ENGINE=podman tools/start_gate/start_gate.sh
registry.fedoraproject.org/fedora:43 [TREE]`. It needs podman or docker and the network, so the tests
do not run it. `TREE` may be the checkout of an older tag: the scripts are always the ones of this
checkout. 1.0.11 is the negative control, it must fail on `epel-10` and `fedora-43`
(`negative_control.sh`, above).

Measured, 2026-11-12, not in the containers of the workflow but in rootless root directories made
from the repositories of each chroot (CentOS Stream 10 with EPEL 10 and CRB, Fedora 43, 44, 45 and
Rawhide) with the real `rpmbuild` and stand-ins for `dnf` and `rpm -q` (the packages were unpacked
beforehand; the stand-in for `rpm -q` answers with the name of the RPM file that the stand-in for
`dnf` "installed"): the gate on the tree of v1.0.12 passes on all five legs, also as a run for the
tag `v1.0.12`, and on the tree of v1.0.11 it fails on `epel-10` and `fedora-43` in steps (c) and (d),
with the error that the maintainer saw (`'gi.repository.GLibUnix' object has no attribute
'signal_add'`). The scripts are also run by `tests/test_start_gate_run.py` with stand-ins for the
commands, and `verify_checks.sh` by `tests/test_start_gate_verify.py` against a local server; against
the real GitHub and COPR APIs it was run for a commit that has no `start gate` checks, and failed
as it must. `[H]` Not measured: a run of the workflow on GitHub (and so the `negative-control` job);
`dnf` itself (the flags, the plugin names `dnf5-plugins` and `dnf-plugins-core`, the `epel-release`
and CRB lines); the real `rpm -q` in step (a); the scriptlets of the install; the packages
`gnome-shell` and the weak dependencies (left out in the rootless roots, installed or not in the
containers); the container engine (`start_gate.sh` is run with a stand-in); a real GNOME session. The
gate does not test the slideshow itself, the idle trigger or the lock: the mutter and GNOME Shell
interfaces are not there, and the service logs them as errors and runs on.

## After the gate

1. Tag: an annotated tag `v<version>` on the commit that passed the gate.
2. COPR builds from the webhook (below). Wait for the build and check every chroot.
3. In a clean container, `[H]` (background knowledge, not measured here; the project is not made
   yet):

   ```
   sudo dnf install dnf-plugins-core
   sudo dnf copr enable trensoft/slideshow-lock
   sudo dnf install slideshow-lock
   ```

   `[H]` `dnf copr` needs `dnf-plugins-core`. `[H]` On AlmaLinux 10, Rocky Linux 10 and RHEL 10
   EPEL and the CRB repository have to be enabled first; how that is done differs on RHEL.
   `[H]` The owner name `trensoft` is the name of the maintainer's COPR account.
4. The GitHub release, with the list of changes.
5. The done-message (see above): done, per chroot, and what could not be checked.

## A failed step

If the build (step 2), the install test (step 3) or the GitHub release (step 4) fails, the tag is
not deleted and not moved. The done-message says what failed and proposes a fix. A fix is a new version
(for example 1.0.1) and goes through the gate again.

## Pushing the tag, and what is measured

The releaser pushes the tag with `git push`, which uses the stored credential of the machine. The
procedure does not read, print or write the credential. The push was measured as a dry run with a
local annotated tag and `git push --dry-run origin refs/tags/<name>`: with the stored credential
the dry run lists `[new tag]` and exits 0; without a credential (`credential.helper` empty,
prompts off) it stops with `could not read Username` and exit 128. `git ls-remote --tags origin`
was empty after the dry run, and the local tag was deleted: no real tag was pushed.

What this does not show: a dry run does not send the tag, so it does not prove that the real push
is accepted. In particular, a tag rule or ruleset on the repository, if one is set later, is not
tried by it. The first real tag push is the real test.

The GitHub release (step 4) is an API call that needs an access token. The releaser does not do it
with the credential of the machine. It is done by a person who has an established, approved way
to make it, named in the report, or by the maintainer in the web interface.

## The webhook (tag only)

This is the setting, read from the COPR source (`fedora-copr/copr`, the frontend:
`webhooks_general.py` `webhooks_git_push`, and `packages_logic.py` `_tag_belongs_to_package` and
`commits_belong_to_package`). `[H]` That the live COPR server behaves as that source says is not
measured.

- GitHub webhook: only the event "Branch or tag creation". "Pushes" is off. `[H]`
- URL: the COPR GitHub webhook address of the project, with `slideshow-lock/` added at the end.
  Without the package name a tag such as `v1.0.0` does not match. The address contains a secret
  (a uuid), so it is not written in the repository.
- COPR package: "Webhook rebuild" on. The committish `main` is not used: a build of `main` would be
  a test build, which this project does not take.

A push to a branch must not start a build; only the creation of a tag does. Measured: on
2026-10-06 each merge into `main` (#52, #53, #54) started a build 2 seconds later (builds 11084437,
11084605, 11084818). The COPR build list ends with 11084848, version `1.0.0-1`, `succeeded`,
submitted at 12:56:08 UTC on 2026-10-06. It is the build of the tag `v1.0.0`: its source log shows
`git checkout v1.0.0`, which is `f94b29d`, and the tag was made at 12:56:03 UTC (the tagger time in
`git cat-file -p v1.0.0`), 5 seconds before. After it `main` got #55 (`ba5d30d`, merged at 15:59
CEST on 2026-10-06) and #56 to #63, and no build was submitted: #55 did not start a build, and
neither did the later merges. The list was read by the releaser from the COPR API
(`api_3/build/list`) on 2026-10-07 at about 05:50 CEST; this cannot be reproduced from the
repository. `[H]` That the "Pushes" event is now off is a reading of this, not seen in the GitHub
settings.

Any tag creation with the package name in the URL starts a build, whatever the tag is called, and
a tag is not deleted (see "A failed step"), so a tag is never pushed to test the webhook. The
webhook is checked with the "ping" or "Redeliver" function of GitHub (COPR answers a ping with OK
and does not build) or with a push to a branch (it does not build).
