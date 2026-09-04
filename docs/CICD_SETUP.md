# CI/CD setup

One-time setup so `.github/workflows/deploy.yml` can release to the cPanel box.

Deployment is otherwise nine hand-typed steps from [`DEPLOY_UPDATE.md`](DEPLOY_UPDATE.md),
each conditional on what changed. That runbook stays the source of truth — the pipeline
automates it, it does not replace it. **Read it before changing the workflow.**

The pipeline does **not** deploy on merge. Merging to `main` runs the tests and builds the
frontend; the deploy job then waits for a human to approve it in the Actions tab.

---

## Branch flow

```
feature/*  →  develop  →  testing  →  main  →  production
```

| Branch | Purpose | CI | Deploys |
|---|---|---|---|
| feature branches | day-to-day work | ✅ full suite | ✗ |
| `develop` | integration | ✅ full suite | ✗ |
| `testing` | pre-release / QA | ✅ full suite | ✗ |
| `main` | production | ✅ full suite | ✅ **after approval** |

**`testing` does not deploy anywhere.** There is exactly one environment —
`career.innomaxskills.com` on `66.116.207.71`. Deploying `testing` would replace the live
site that HR and candidates are using with unreviewed code. Until a second host exists,
`testing` is a review stage, not an environment.

CI runs on **every** branch (`branches: ['**']`), not just these three. A fixed list is what
previously left real work branches untested — the old trigger named `staging` and `develop`
when neither existed, so a branch could sit 16 commits behind `main` with nothing ever
running against it.

`deploy.yml` refuses to run from anything but `main`, checked in the job itself rather than
left to the trigger: `workflow_dispatch` lets you pick any branch from the Actions UI, and
the approving reviewer sees a deploy request, not a branch name.

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
git config --global --add safe.directory /opt/hireflow
```

## 2. Grant it exactly six things

```bash
# Validate BEFORE installing — a bad sudoers file can lock out sudo entirely.
# Keep a second root shell open while you do this.
visudo -c -f /opt/hireflow/deploy/cpanel/deploy-sudoers

install -m 0440 -o root -g root \
  /opt/hireflow/deploy/cpanel/deploy-sudoers \
  /etc/sudoers.d/hireflow-deploy
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

## 5. The approval gate

**Settings → Environments → New environment → `production`**

- Tick **Required reviewers** and add yourself (and ideally one other person).
- Optionally restrict deployment branches to `main`.

Without this the deploy job runs unattended the moment anything merges. The gate is the
point: proctoring and the coding judge have **no CI coverage at all**, so a human still has
to look before a release goes out.

---

## Running a deploy

**Normal:** merge to `main` → CI and the build run → open **Actions**, find the run, click
**Review deployments → Approve**.

**Manual:** Actions → *Deploy to cPanel* → **Run workflow**.

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

The workflow rolls the **code** back to the pre-deploy commit and restarts the services. It
does **not** roll back the schema — `DEPLOY_UPDATE.md:185-188` is explicit that a
half-reverted migration is worse than a newer schema under older code, which is normally
harmless because migrations here are additive.

Pre-migration dumps are at `/root/predeploy-<date>-<time>.sql`.

If the schema gate failed, nothing was restarted and the site is still serving the old code
— that is the property the gate exists for. Fix the migration and redeploy.

## What the pipeline cannot check

After every deploy, still walk the smoke matrix at `DEPLOY_UPDATE.md:165-171`: HR login, one
real email, a proctoring WebSocket reaching `101`, Run Code, and opening a CV.

Proctoring and the judge have no CI coverage — `requirements-ci.txt` omits `opencv-python`,
`mediapipe` and `ultralytics` entirely, so those tests skip rather than run. Until that
changes, these five checks stay human.
