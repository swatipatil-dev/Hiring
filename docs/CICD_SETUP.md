# CI/CD setup

One-time setup so `.github/workflows/deploy.yml` can release to the cPanel box.

Deployment is otherwise nine hand-typed steps from [`DEPLOY_UPDATE.md`](DEPLOY_UPDATE.md),
each conditional on what changed. That runbook stays the source of truth — the pipeline
automates it, it does not replace it. **Read it before changing the workflow.**

**A merge to `main` deploys to production once CI passes.** Nobody clicks anything. Read
the next section before merging.

---

## Auto-deploy: a push to main ships

A merge to `main` runs CI. If CI passes, the deploy runs on its own. Nobody clicks anything.

That is a deliberate choice with a known cost, recorded here so it stays a decision rather
than a surprise:

- **There is no human approval step.** Required reviewers are a paid feature for private
  repositories and are unavailable on this plan — the option does not appear in the
  environment settings. CI is the only gate.
- **CI does not cover proctoring or the coding judge.** `requirements-ci.txt` omits
  `opencv-python`, `mediapipe` and `ultralytics`, so those tests *skip* rather than run. A
  regression in either reaches candidates mid-exam with nothing having checked it. Closing
  that gap is the highest-value follow-up this pipeline has.
- **Nothing runs the manual smoke matrix** at the end of this file. With a manual deploy you
  were prompted to; now you are not.

### Why it triggers on CI, not on push

`deploy.yml` waits for the **"HireFlow CI" workflow to complete** and inspects its
conclusion:

```yaml
on:
  workflow_run:
    workflows: ["HireFlow CI"]
    types: [completed]
    branches: [main]
```

A plain `on: push` would start `deploy.yml` and `ci.yml` at the same instant. The deploy
would race the tests and could ship code whose suite is still running — or has already
failed, with the red tick arriving after the bad code is live. That is worse than having no
CI at all, because it looks like something checked.

One consequence worth knowing: on a `workflow_run` event **`github.sha` is the default
branch's HEAD, not the commit that was tested.** Every ref in this workflow comes from
`github.event.workflow_run.head_sha` instead. The difference only shows up when two commits
land close together — and then it ships the wrong one.

### What gets deployed

| Trigger | Ships | Labelled |
|---|---|---|
| CI green on `main` | the tested commit | `main@<short sha>` |
| **Run workflow** with a version | that release tag | `v2.1.0` |

Both paths are still available. Cutting a release and dispatching it gives you a numbered,
re-deployable version; the automatic path gives you speed. `/health` reports whichever label
was deployed.

`dry_run` exists only on the manual path, so an automatic deploy is always a real one.

### If you want to pause it again

Delete the `workflow_run:` block from `deploy.yml`. `workflow_dispatch` remains, and deploys
go back to being a deliberate button press — which is where this started, and a defensible
end state.

### The first run

Before relying on it, in this order:

1. **Dispatch once with `dry_run` ticked.** Exercises the six secrets, the host key and the
   SSH config against the real server without changing it. It does open a connection — it is
   not an offline rehearsal.
2. **Confirm the gate works.** Merge something with a failing test and check the deploy
   *does not run*. Revert. If a red CI still deploys, the gate is decorative.
3. **Confirm the right commit shipped** — compare the SHA in the run summary with
   `git rev-parse HEAD` on the server. This is where the `head_sha` trap above would surface.
4. **Then walk the smoke matrix** at the end of this file.


---

## Branch flow

```
feature/*  →  PR into develop  →  testing  →  main  →  production
```

Each branch is a **stage**, and every test runs **once** on the way to production:

| Stage | Branch | Runs | Deploys |
|---|---|---|---|
| 1 · integration | PR into `develop` (and the push after merging) | lint, backend tests except `slow`/`proctor`, migrations, frontend tests + build | ✗ |
| 2 · pre-release | `testing` | backend `slow` + `proctor` tests, with the proctoring/ML extras (`requirements-ci-stage2.txt`) | ✗ |
| 3 · production | `main` | no tests of its own — stages 1 and 2 must already have passed on this exact code | ✅ **automatically, if CI passes** |

The two pytest selections are exact complements (`-m "not slow and not proctor"`
and `-m "slow or proctor"`), so every test belongs to exactly one stage. Before
stage 2 existed, the `slow` and `proctor` tests ran in no CI run at all.

**No silent skips.** Each stage fails if any test was skipped, unless it is listed
in `backend/test/ci_allowed_skips.txt` with the reason it cannot run in CI
(`backend/scripts/ci_check_skips.py`). A new test that needs opencv must be marked
`@pytest.mark.proctor` (every `test_proctor_*.py` file carries it module-wide);
left unmarked it skips in stage 1 and fails this check.

### How a stage is not repeated

When a stage passes, CI uploads an artifact named `ci-stage<N>-<tree>`, where
`<tree>` is `git rev-parse HEAD^{tree}` — identical for identical code, whatever
the commit or branch. Every run starts with a `plan` job that looks up the marker
for each stage the target branch needs:

- **found** → that stage already passed on this exact code and is skipped;
- **missing** → the stage runs in this workflow.

A normal release therefore tests once: the PR into `develop` runs stage 1, the push
after merging finds its marker, `develop → testing` adds stage 2, and `testing →
main` finds both and goes straight to deploy. Anything else — a hotfix pushed
straight to `main`, a merge whose base moved, a marker past its 90-day retention —
has no marker for its tree and is tested in full. The fallback is always "run it",
never "assume it passed".

Promote with plain merges of the whole branch (`develop` into `testing`, `testing`
into `main`) so the code, and therefore the tree, is unchanged; that is what lets
the markers match.

**Feature-branch pushes run no CI.** Their code is tested on the pull request into
`develop`. `workflow_dispatch` ignores markers and runs every stage the branch
needs, for a manual full run.

`ci-result` is the single job that summarises a run — require it in branch
protection. A stage job may be skipped only when `plan` found its marker.

**`testing` does not deploy anywhere.** There is exactly one environment —
`career.innomaxskills.com` on `66.116.207.71`. Deploying `testing` would replace the live
site that HR and candidates are using with unreviewed code. Until a second host exists,
`testing` is a review stage, not an environment.

`deploy.yml` refuses to run from anything but `main`, checked in the job itself rather than
left to the trigger. `workflow_dispatch` lets you pick any branch from the Actions UI, and
on the automatic path `github.ref` is the default branch whatever was actually tested — so
the branch has to be read from the triggering CI run, not from the event.

### If you add a staging server later

Three things, none of which need code changes here:

1. A `staging` GitHub Environment (no required reviewer — that is the point of staging).
2. A second set of secrets — `STAGING_HOST`, `STAGING_USER`, `STAGING_SSH_KEY`, and its own
   `DB_*` — since the deploy user and key must be per-host.
3. A `deploy-staging.yml` copying `deploy.yml` with the branch guard changed to `testing`
   and the paths pointed at the new docroot.

---

## What the pipeline does differently from the runbook

Two deliberate departures, both load-bearing.

**The frontend is built on the runner, not the server.** `DEPLOY_UPDATE.md:139` requires
`npm install` rather than `npm ci` because the lockfile drifts — but `npm install` rewrites
the tracked `package-lock.json`, leaving the working tree dirty, so the *next* `git pull`
conflicts mid-deploy ([`DEPLOYMENT_NOTES.md:461`](DEPLOYMENT_NOTES.md)). Building on a
disposable runner and rsyncing `dist/` sidesteps that entirely. It is safe because `dist/`
is environment-independent: `frontend/src/api/client.js` uses a relative `baseURL` and there
are no `VITE_` build variables.

**`scripts/prefetch_models.py` runs during deploy.** It is in the install runbook
(`CPANEL_DEPLOYMENT.md:661`) but missing from `DEPLOY_UPDATE.md`, and it already exits
non-zero on failure — exactly what a pipeline wants.

---

## 1. Create the deploy user

As root on the server:

```bash
useradd -m -s /bin/bash deploy
mkdir -p /home/deploy/.ssh && chmod 700 /home/deploy/.ssh
chown -R deploy:deploy /home/deploy/.ssh
```

It needs to read the repo (`git pull` runs as `deploy`) while the tree stays
`hireflow`-owned:

```bash
usermod -aG hireflow deploy
chmod -R g+rwX /opt/hireflow
chmod 600 /opt/hireflow/backend/.env
sudo -u deploy git config --global --add safe.directory /opt/hireflow
```

Two of those lines are not obvious, and both were found the hard way:

**The `chmod 600` is not optional.** `chmod -R g+rwX` walks the whole tree including
`backend/.env`, turning 0600 into 0660 — and `deploy` was just added to the `hireflow`
group. Without the re-lock, the deploy user can read `SECRET_KEY`, `EMAIL_ENCRYPTION_KEY`
and the database password, which is the exact access this whole design withholds. `.env` is
gitignored, so `git pull` never touches it and re-locking costs nothing.

While you are there, check for **copies**: `ls -la /opt/hireflow/backend/.env*`. Editor
backups (`.env.save`, `.env.bak`) carry the same secrets, are not matched by the `.env`
line in `.gitignore`, and the recursive chmod makes them group-readable too. Move them out
of the tree.

**`git config` must run as `deploy`, not root.** `--global` as root writes `/root/.gitconfig`,
which the deploy user never reads — `git pull` then fails with "detected dubious ownership"
because git checks the directory's owner UID and group membership does not satisfy it.

### Let it reach GitHub

`/opt/hireflow`'s remote is **SSH** — `git@github.com:innomax-it-solutions/Hiring_system_Cpanel.git`
— against a private repository. Creating the user is therefore not enough: the fetch runs as
`deploy`, so `deploy` needs its own key, that key has to be registered on the repo, and
github.com has to be a known host *for that user*.

```bash
sudo -u deploy ssh-keygen -t ed25519 -N '' -f /home/deploy/.ssh/id_ed25519
ssh-keyscan github.com > /home/deploy/.ssh/known_hosts
chown deploy:deploy /home/deploy/.ssh/known_hosts
cat /home/deploy/.ssh/id_ed25519.pub
```

Add that public key at **repo → Settings → Deploy keys → Add deploy key**, **read-only** — the
server only ever fetches. A deploy key is scoped to the one repository, which is why it is used
here rather than a personal access token sitting on disk. Verify before going further:

```bash
sudo -u deploy git -C /opt/hireflow fetch origin      # must exit silently
```

**`ssh-keyscan` is not optional, and neither line can be skipped because "SSH works".** Both
were missing on the live server and cost a week of deploys. The two failures look nothing alike
and neither names its cause:

- no `known_hosts` entry → `Host key verification failed`. Interactively this is the familiar
  "Are you sure you want to continue connecting?" prompt; in a pipeline there is nobody to
  answer it, so it is simply an error. Note `/home/deploy/.ssh` must exist first — the redirect
  above fails if the `mkdir` at the top of this section was skipped.
- key not on the repo → `git@github.com: Permission denied (publickey)`.

Neither reaches anyone. `deploy.yml` reports the raw git error, the run goes red, and unless
somebody opens the Actions tab the site simply keeps serving its last good build while every
merge appears to succeed. Seven consecutive deploys failed that way before anyone noticed, and
the `notify` job in `deploy.yml` now opens an issue precisely so that cannot repeat.

## 2. Grant it exactly six things

```bash
# Validate BEFORE installing — a bad sudoers file can lock out sudo entirely.
# Keep a second root shell open while you do this.
visudo -c -f /opt/hireflow/deploy/cpanel/deploy-sudoers

install -m 0440 -o root -g root \
  /opt/hireflow/deploy/cpanel/deploy-sudoers \
  /etc/sudoers.d/hireflow-deploy

# The root unit the pipeline starts to rebuild vhosts and restart Apache. Installed
# here, never by the pipeline (its header says why).
install -m 0644 -o root -g root \
  /opt/hireflow/deploy/cpanel/root/hireflow-apache-reload.service \
  /etc/systemd/system/
systemctl daemon-reload
```

Read [`deploy/cpanel/deploy-sudoers`](../deploy/cpanel/deploy-sudoers) — the comments say
what is granted and, more importantly, what is not. In particular the deploy user **cannot
read `/opt/hireflow/backend/.env`** (0600, `hireflow`-owned) and **cannot act as `hfjudge`**,
whose docker-group membership is root-equivalent on a shared host.

Verify:

```bash
sudo -u deploy sudo -l                                    # only the six aliases
sudo -u deploy sudo cat /opt/hireflow/backend/.env        # must be refused
sudo -u deploy sudo systemctl restart sshd                # must be refused
```

If any of the last two succeed, stop and fix the fragment before continuing.

## 3. Key pair

On your machine (never on the server, never with a passphrase — Actions cannot type one):

```bash
ssh-keygen -t ed25519 -C "github-actions-hireflow" -f ./hireflow-deploy -N ""
```

Authorise the public half:

```bash
ssh-copy-id -i hireflow-deploy.pub deploy@66.116.207.71
# or paste hireflow-deploy.pub into /home/deploy/.ssh/authorized_keys
```

Confirm, then destroy your local copy of the private key once it is in GitHub:

```bash
ssh -i hireflow-deploy deploy@66.116.207.71 "whoami"      # -> deploy
```

## 4. GitHub secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value | Used for |
|---|---|---|
| `DEPLOY_SSH_KEY` | the **private** key, whole file including header/footer | SSH |
| `DEPLOY_HOST` | `66.116.207.71` | SSH |
| `DEPLOY_USER` | `deploy` | SSH |
| `DB_NAME` | e.g. `innomaxskills_hfmig` | pre-migration dump |
| `DB_USER` | e.g. `innomaxskills_hf` | pre-migration dump |
| `DB_PASSWORD` | that user's password | pre-migration dump |

`DB_NAME` is whatever `curl -s localhost:8000/health` reports as `database`.

**All six must be present.** A missing one is what made the first deploy attempt fail with
an unrelated-looking DNS error. Confirm with:

```bash
gh secret list          # all six must appear
```

The deploy job's second step checks all six and fails naming the missing ones, so a partial
set gets you a clear error rather than a half-finished deploy.

Keep them at **repository** scope, matching the table above. Moving them to the `production`
**environment** scope later is stricter and worth doing; nothing in `deploy.yml` needs to
change if you do, because the check lives in the `deploy` job and that job declares
`environment: production`. The same check in any *other* job would stop being able to see
them.

## 5. The production environment

**Settings → Environments → New environment → `production`**

- Restrict **deployment branches** to `main` (Ref type: Branch, pattern `main`). Not
  optional: `workflow_dispatch`'s "Run workflow" dropdown offers every branch in the repo.
  The in-job guard catches this too, but the branch rule refuses earlier and makes the
  intent visible in the UI rather than buried in a workflow file.

> **Required reviewers are not available on this plan.** Environment protection rules are a
> paid feature for private repositories — the checkbox simply does not appear. The same
> limitation shows up on the secrets page for organization secrets. So there is **no human
> approval step**, and CI is the only thing standing between a merge and production. If you
> later upgrade the plan, tick Required reviewers here and the existing
> `environment: production` in `deploy.yml` will start honouring it with no code change.

**This step fails open, silently, if you skip it.** Referencing an environment that does not
exist does not error — GitHub creates it on first use with *no* protection rules, and the
deploy job runs unreviewed. An unattended deploy is therefore indistinguishable from a
correctly gated one until you look at Settings → Environments. Check that `production` is
listed and shows a reviewer.

---

## Cutting a release

Versions come from one place: the **`VERSION`** file at the repo root. `backend/main.py`
reads it at import, so `/health`, `/api/v1/info` and the OpenAPI docs all report the same
number and cannot drift apart. Before this there were three separate hardcoded copies of
`2.0.0` and a `frontend/package.json` stuck at `1.0.0`.

Actions → *Cut a release* → **Run workflow**, choose `patch`, `minor` or `major` (or type an
exact version). The workflow writes `VERSION`, bumps `frontend/package.json` and its
lockfile with `npm version`, commits, tags `vX.Y.Z` and publishes a GitHub Release with
generated notes.

Notes:

- **Releases are cut from `main` only**, checked in the job for the same reason `deploy.yml`
  checks it — the "Run workflow" dropdown offers every branch.
- **A tag is never moved.** If the computed version already exists the run fails; re-releasing
  a number would retroactively change what "v2.1.0 is in production" means.
- The series starts at **v2.0.0**, not v1.0.0, because the API has reported `2.0.0` since
  before tagging existed and a version that appears to go backwards is worse than one that
  starts high.
- **Cutting a release does not deploy it.** That is the next section.

### Which version is in production?

```bash
curl -s https://career.innomaxskills.com/api/v1/version
```

That is the whole picture in one call: the release tag, its commit, when it shipped and who
shipped it, the frontend bundle actually being served, and whether that bundle matches the
backend.

Unauthenticated on purpose — a release number is a public fact about a public site, and
needing to log in to read it defeats the point when the complaint is that logging in is
broken.

The same block appears under `release` in `/health`. Note that the `version` field there is
the **MySQL** server version and always has been; the application number is `app_version`.

**Where it is recorded on the server**

| Where | What it is |
|---|---|
| `VERSION` (in the checkout) | what this code claims to be |
| `/opt/hireflow/.release.json` | what the pipeline actually deployed, rewritten by a rollback |
| `/opt/hireflow/.release-history.jsonl` | one appended line per deploy and per rollback |
| `journalctl -u hireflow-api` | one line at every boot, so it survives the files being lost |

```bash
tail -5 /opt/hireflow/.release-history.jsonl          # recent deploys and rollbacks
journalctl -u hireflow-api | grep "| release "        # every boot, dated
```

**Frontend skew.** `frontend_version` is the bundle actually being served, which is not always
this release: a deploy that did not touch `frontend/` leaves the previous bundle in place, and
a rollback restores an older one. When it differs from the backend, the API reports the
mismatch and the service logs a warning at boot. That is the only automatic check in the
system for the stale-copy failure [`DEPLOY_UPDATE.md`](DEPLOY_UPDATE.md) is built around.

**After a rollback** the manifest reads `state: "rolled-back"`, `version: null` and names the
commit the server was reset to. Null rather than a guessed tag name: what is on the box is
whatever release that commit belonged to, and inventing a version string would be worse than
reporting the commit exactly.

---

## Running a deploy

**Merging to `main` deploys automatically once CI passes** — nothing to click. To ship a
specific numbered release instead, Actions → *Deploy to cPanel* → **Run workflow** → pick
`main`, type the version (e.g. `v2.1.0`). Tick `dry_run` there to rehearse without changing
anything.

A `validate` job runs first and refuses anything that is not an existing `vX.Y.Z` tag whose
commit is an ancestor of `main`. That last check matters: a tag can be cut from any commit,
so without it "only main reaches production" would hold for branches and be silently
bypassable with a tag. Validation runs **before** the build, so a typo costs seconds rather
than a full production build.

On the server the checkout moves to the tag (`git fetch --tags && git checkout --detach`)
rather than pulling a branch. That leaves it in **detached HEAD** — deliberate, and the
reason nothing in the pipeline may go back to using `git pull`. The rollback is unaffected:
the pre-deploy commit is captured as a SHA, and `git reset --hard <sha>` behaves the same
detached.

The run is named after the release, and its summary carries the version and commit, so the
Actions list answers "what shipped, and when" without opening anything.

**Dry run first.** The manual trigger has a `dry_run` input that prints the exact command
sequence for the current diff without changing anything. Use it the first time, and any time
the workflow itself changes.

---

## What the pipeline must never do

**Never write `/opt/hireflow/backend/.env`.** It is not in git and holds the only copy of
`SECRET_KEY` and `EMAIL_ENCRYPTION_KEY` (`CPANEL_DEPLOYMENT.md:1142`). A wrong
`EMAIL_ENCRYPTION_KEY` stops mail *silently* — `decrypt()` returns `""` instead of raising
(`DEPLOYMENT_NOTES.md:150`).

**Never put `PORT` or `WEB_CONCURRENCY` in that file.** systemd applies `EnvironmentFile=`
*after* `Environment=`, so `.env` overrides the unit files. That has already caused an
outage: the proctor bound 8000, the API crash-looped on "Connection in use", and the site
kept serving from the proctor process while proctoring was silently dead
(`DEPLOYMENT_NOTES.md:281-331`).

**Never `npm` on the server.** See above.

---

## When a deploy fails

The workflow rolls the **code** back to the pre-deploy commit, restores the **frontend**
bundle that was serving before the deploy, and restarts the services. It does **not** roll
back the schema — `DEPLOY_UPDATE.md:185-188` is explicit that a half-reverted migration is
worse than a newer schema under older code, which is normally harmless because migrations
here are additive.

The run's final warning states exactly which of those were rolled back, so you never have to
infer it from which steps ran.

Pre-migration dumps are at `/root/predeploy-<date>-<time>.sql`.

### How the frontend gets rolled back

`/opt/hireflow/.dist-prev/` holds the last frontend bundle that passed a health check. It is
written at the end of a **successful** deploy and nowhere else, so during a failed one it
still holds the previous good bundle — which is what the rollback restores.

It lives under `$APP_DIR` rather than `/tmp` for two reasons: the deploy user can write it
without sudo (it is in the `hireflow` group, and section 1 above does `chmod -R g+rwX`), and
it survives a reboot. It is untracked and gitignored, so it interferes with neither
`git pull --ff-only` nor `git reset --hard`.

The restore reuses the same three commands the publish step runs, so it needs no addition to
`deploy-sudoers`. If you ever change those three lines in the workflow, change them in the
`HF_WEB` alias too — sudo matches the command string exactly, and a single differing space
makes the restore fail at the worst possible moment.

**The one case it cannot handle** is a first-ever deploy, or a wiped `.dist-prev`: there is
no recorded bundle, so the site is left serving the new UI against rolled-back code. The run
says so with an explicit error rather than skipping quietly, and the fix is the manual
rebuild at `DEPLOY_UPDATE.md:181`.

If the run failed **before** the pre-deploy commit was recorded — a missing secret, an
unreachable host, the wrong branch — no rollback runs, and the workflow says so in a warning
rather than leaving a step silently skipped. Nothing reached the server, so there is nothing
to undo: fix the error and re-run.

If the schema gate failed, nothing was restarted and the site is still serving the old code
— that is the property the gate exists for. Fix the migration and redeploy.

## What the pipeline cannot check

After every deploy, still walk the smoke matrix at `DEPLOY_UPDATE.md:165-171`: HR login, one
real email, a proctoring WebSocket reaching `101`, Run Code, and opening a CV.

Proctoring and the judge have no CI coverage — `requirements-ci.txt` omits `opencv-python`,
`mediapipe` and `ultralytics` entirely, so those tests skip rather than run. Until that
changes, these five checks stay human.
