# HireFlow — end-to-end runbook

**New here? This is the file to start with.** It goes from an empty laptop to code running on
the production server, in order, with every command labelled by where you type it.

The other deployment docs are deeper on one topic each. This one is the spine:

| Doc | Read it when |
|---|---|
| `docs/CPANEL_DEPLOYMENT.md` | Installing on a brand new server for the first time |
| `docs/DEPLOY_UPDATE.md` | Deploying **by hand**, because the pipeline is unavailable |
| `docs/CICD_SETUP.md` | You need the full detail on one pipeline step |
| `docs/DEPLOYMENT_NOTES.md` | **Before changing anything about deployment.** The *why*: silent failure modes, which config file wins |

---

## Which terminal am I in?

Every command block below carries one of these banners. Nothing here is ambiguous about
where it runs — that is the whole point of the file.

| Banner | Where | How you get there |
|---|---|---|
| 💻 **LOCAL** | Your Windows machine | PowerShell, inside your clone |
| 🖥️ **CPANEL (root)** | The server, as root | WHM → Terminal, or `ssh root@66.116.207.71` |
| 🖥️ **CPANEL (deploy)** | The server, as `deploy` | `ssh -i hireflow-deploy deploy@66.116.207.71` |
| 🌐 **GITHUB** | github.com in a browser | Clicks, not commands |

**The system, in one line:** production is `career.innomaxskills.com` at `66.116.207.71`,
the code is in `/opt/hireflow`, the site is served from
`/home/innomaxskills/public_html/career`, and there is **exactly one server** — no staging.

---

# Part 0 — Health check: what state is it in right now?

Run this before you change anything, and again whenever something feels wrong.

> **Every command in Part 0 is read-only.** Nothing here modifies the server. You can run all
> of it safely on a live production system.

## 0.1 Is everything running?

🖥️ **CPANEL (root)**

```bash
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
systemctl list-timers hireflow-regrade.timer
ss -ltnp | grep -E ':800[01]'
curl -s localhost:8000/health
curl -s localhost:8001/health
```

**What the right answer looks like:**

- Four lines of `active`. A second check showing `activating` is a **restart loop** —
  `Restart=always` makes that look like a slow start.
- **Both 8000 and 8001 must be listening.** If only 8000 appears, one service took the
  other's port and is crash-looping on `Connection in use` — *while the site keeps working*,
  because both units run the same app. That is what a silent proctoring outage looks like,
  and 0.2 below tells you the usual cause.
- `/health` should report `"status": "healthy"` with `table_count` greater than zero.
  `table_count: 0` means the app is connected to the wrong database.

## 0.2 What is in `.env`?

> ### Read this before running anything in 0.2
>
> **Never `cat` this file. Never paste its output into a chat, a ticket, or a screenshot.**
>
> `/opt/hireflow/backend/.env` holds `SECRET_KEY`, `EMAIL_ENCRYPTION_KEY`, `SMTP_PASS` and
> the database password. Anyone who sees it can sign their own admin token and read every
> candidate's data.
>
> The commands below are written so you never have to look at a value. They answer *"is it
> set?"* without answering *"what is it?"*.

🖥️ **CPANEL (root)** — which variables are set, and how long each value is:

```bash
awk -F= '/^[A-Z_]+=/{printf "%-28s %d chars\n", $1, length($2)}' \
    /opt/hireflow/backend/.env
```

Output looks like `SECRET_KEY  64 chars`. A variable showing **`0 chars` is present but
empty**, which is a different problem from missing — and it is the exact bug that is live
right now: `GEMINI_API_KEY` is set to nothing, so every AI feature (question generation, JD
generation, resume gap-fill) silently does nothing.

🖥️ **CPANEL (root)** — which variables are *missing* compared to the template:

```bash
cd /opt/hireflow && diff \
  <(grep -oE '^[A-Z_]+' backend/.env.cpanel.example | sort) \
  <(grep -oE '^[A-Z_]+' backend/.env | sort)
```

Lines starting `<` are in the template but not in your `.env`. Cross-check them against
Appendix A — many are optional.

🖥️ **CPANEL (root)** — variables that must **not** be there:

```bash
grep -nE '^(PORT|WEB_CONCURRENCY|GOOGLE_CLOUD_PROJECT|VERTEX_LOCATION|GCS_BUCKET|JUDGE0_URL|JUDGE0_TOKEN|JUDGE_GCP_PROJECT|JUDGE_VM_NAME)=' \
     /opt/hireflow/backend/.env
```

**No output is the correct result.** Any hit is a problem, and `PORT` is the dangerous one.

systemd applies `EnvironmentFile=` **after** `Environment=`, so a `PORT=8000` line in `.env`
silently **overrides the service unit**. That is how `hireflow-proctor` once bound to 8000,
`hireflow-api` crash-looped on "Connection in use", the website carried on working from the
proctor process — and every candidate's face check hung on "Checking…" forever. This one
grep would have caught it. It is the same failure 0.1's port check looks for, from the other
end.

## 0.3 What commit is live?

🖥️ **CPANEL (root)**

```bash
git -C /opt/hireflow rev-parse HEAD
git -C /opt/hireflow log -1 --oneline
cat /opt/hireflow/.release.json
```

`.release.json` is written by the pipeline's `Record what was deployed` step. The field to
notice is `"state"` — if it says `"rolled-back"`, a deploy failed and the server is running
the *previous* code, with the schema from the failed one still applied.

You can also ask the live site: `curl -s https://career.innomaxskills.com/api/v1/version`.

Note the server sits on a **detached HEAD** (the pipeline does `git checkout --detach`), so
`git pull` will not work there by hand. That is intended.

## 0.4 Are there stray copies of `.env`?

🖥️ **CPANEL (root)**

```bash
ls -la /opt/hireflow/backend/.env*
```

Only `.env` (mode `-rw-------`, owner `hireflow`) and the two `.example` files should be
there. Editor backups — `.env.bak`, `.env.save`, `.env.save.1` — hold the **same secrets**,
are **not** matched by the `.env` line in `.gitignore`, and become group-readable the moment
anyone runs a recursive `chmod` on the tree.

Three such files were found on this server and moved to `/root/env-archive`. If you find
more, move them out of the app directory; do not just delete them until you are sure they
are not the only copy of something.

## 0.5 Is my local clone current?

💻 **LOCAL**

```powershell
git status
git fetch
git status -sb
git log --oneline -5
```

`git status -sb` prints something like `## my-branch...origin/my-branch [behind 12]`. Behind
means pull before you work; ahead means you have unpushed commits.

---

# Part 1 — Local development on Windows

## 1.1 Get the code

💻 **LOCAL**

```powershell
git clone <repo-url> Hiring_system_Cpanel
cd Hiring_system_Cpanel
```

## 1.2 Backend

💻 **LOCAL**

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**If `Activate.ps1` fails** with "running scripts is disabled on this system", PowerShell is
blocking local scripts. Allow them for your user only:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then create your local config:

```powershell
copy .env.example .env
```

Open `backend\.env` and set at minimum `DATABASE_URL` (MySQL — see below) and `SECRET_KEY`.
**The app refuses to start** if `SECRET_KEY` is unset or still a placeholder; that is
deliberate, so nobody ever signs real JWTs with a key that is published on GitHub.

Create the schema:

```powershell
python setup_db.py
```

> ⚠️ `setup_db.py` is **local development only**. It hardcodes `root` / `recruitment_db`
> and it **drops tables**. Never run it against the server.

## 1.3 Run it

💻 **LOCAL** — two terminals.

Terminal A, backend:

```powershell
cd backend
.venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --port 8000
```

Terminal B, frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**.

> ### Use `uvicorn --port 8000`, not `python main.py`
>
> `python main.py` binds port **8001**. But `frontend/vite.config.js` proxies `/api`, both
> WebSocket paths, and every upload route to `localhost:8000`.
>
> Start it the wrong way and the UI loads perfectly while nothing works — every API call
> 502s. It is a confusing hour if you do not know this.

## 1.4 Things that will confuse you locally

- **Uploads resolve against `HIREFLOW_DATA_DIR`**, defaulting to `backend/` — *not* the
  directory you ran the command from. systemd, cron and your shell all start with different
  working directories, which is why this is pinned rather than relative.
- **Background workers need special launchers on Windows.** RQ's job timeout uses
  `signal.SIGALRM`, which does not exist on Windows, so a stock `rq worker` crashes on every
  job. Use these instead (Redis must be running):
  ```powershell
  python scripts\run_judge_worker_windows.py
  python scripts\run_email_worker_windows.py
  ```
- Without Redis, both queues fall back to running inline on the request thread. Fine for
  development; it means email sends are unthrottled and synchronous.
- **`README.md`'s Quick Start is out of date** — it says PostgreSQL. The project is MySQL.

## 1.5 Tests

💻 **LOCAL**

```powershell
cd backend
pytest                          # everything
pytest -m "not slow"            # what CI runs
pytest test\test_ats_scoring.py # one file

cd ..\frontend
npm test                        # vitest, 9 files / 109 tests
```

CI runs `pytest -m "not slow"`. The `slow` marker means "needs Ollama or takes over 5s".

---

# Part 2 — Git: the branch flow

```
feature/*  ──►  develop  ──►  testing  ──►  main
    CI            CI            CI          CI + DEPLOY
```

| Branch | Purpose | On push |
|---|---|---|
| `feature/*` | Day-to-day work | CI only |
| `develop` | Integration | CI only |
| `testing` | Pre-release / QA | CI only |
| `main` | **Production** | CI, then **automatic deploy if CI passes** |

**`testing` deliberately does not deploy.** There is exactly one server. Deploying `testing`
would replace the live site with unreviewed code. If a staging server is ever added, that is
when `testing` gets a deploy target — not before.

## 2.1 The normal loop

💻 **LOCAL**

```powershell
git checkout develop
git pull
git checkout -b feature/my-change

# ... make changes ...

git add -A
git commit -m "Short description of what changed"
git push -u origin feature/my-change
```

🌐 **GITHUB** — open a Pull Request into `develop`. Wait for CI to go green. Merge.

When `develop` is ready: PR `develop` → `testing`. When QA passes: PR `testing` → `main`.
**That last merge deploys to production.**

## 2.2 "If I push to my branch, will it go to production?"

**No.** Two independent things stop it, and either alone would be enough:

1. `deploy.yml`'s trigger filters `branches: [main]`, so a CI run on any other branch never
   starts the deploy workflow.
2. Even if it somehow started, the first step — `Only main reaches production` — re-reads
   `github.event.workflow_run.head_branch` and fails the job if it is not `main`.

Only a merge into `main` deploys. Nothing else does.

## 2.3 If your branch is behind `main`

💻 **LOCAL**

```powershell
git fetch origin
git merge origin/main
# resolve conflicts, then
git push
```

> **Never `git push --force` to a shared branch.** Forcing a branch that is behind destroys
> whatever was merged into it in the meantime — on this repo that would have discarded five
> merged PRs. Merge `main` in first, always.

---

# Part 3 — GitHub setup

Done once. Skip to Part 6 if the repo is already configured.

## 3.1 The six repository secrets

🌐 **GITHUB** — Settings → Secrets and variables → Actions → **New repository secret**.

| Secret | What it is | Where to get it |
|---|---|---|
| `DEPLOY_SSH_KEY` | The **private** half of the deploy key | The `hireflow-deploy` file from Part 4.3 |
| `DEPLOY_HOST` | `66.116.207.71` | The server's IP |
| `DEPLOY_USER` | `deploy` | The user created in Part 4.1 |
| `DB_NAME` | e.g. `innomaxskills_hfmig` | `curl -s localhost:8000/health` reports it as `database` |
| `DB_USER` | e.g. `innomaxskills_hf` | The `DATABASE_URL` in `.env` |
| `DB_PASSWORD` | The database password | The `DATABASE_URL` in `.env` |

**For `DEPLOY_SSH_KEY`, paste the entire file** — including the
`-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END-----` lines, and the trailing newline.
The pipeline checks for that header specifically, to catch the common mistake of pasting the
`.pub` file instead.

**Reading `DB_USER` and `DB_NAME` off the server** without exposing the password:

🖥️ **CPANEL (root)**

```bash
grep -oP 'mysql\+pymysql://\K[^:]+' /opt/hireflow/backend/.env   # DB_USER
grep -oP '/\K[^/?]+$'              /opt/hireflow/backend/.env    # DB_NAME
```

For `DB_PASSWORD` you need the value itself. Open the file in an editor on the server, copy
the segment between `:` and `@`, and paste it **straight into the GitHub secret field**.
Do not paste it into a chat, a note, or a terminal. If it contains `@ : / #` those will be
percent-encoded in the URL — decode them for the secret.

Verify all six landed:

💻 **LOCAL**

```powershell
gh secret list
```

## 3.2 The production environment

🌐 **GITHUB** — Settings → Environments → **New environment** → name it exactly `production`.

Under **Deployment branches**, select *Selected branches*, add a rule: Ref type **Branch**,
pattern `main`.

This matters more than it looks: the manual "Run workflow" dropdown offers **every branch in
the repo**. The in-job guard catches a wrong branch too, but the environment rule refuses
earlier and makes the intent visible in the UI instead of buried in a YAML file.

> **Required reviewers are not available on this plan.** Environment protection rules are a
> paid feature for private repositories — the checkbox simply does not appear. So there is
> **no human approval step**, and CI is the only thing between a merge and production.
>
> If the plan is ever upgraded, tick Required reviewers here. `deploy.yml` already declares
> `environment: production`, so it will start honouring the rule with no code change.

Also worth knowing: referencing an environment that does not exist **fails open** — GitHub
creates it silently with no rules at all. Confirm `production` is actually listed.

## 3.3 A cosmetic quirk you will notice immediately

Deploy logs say `Every *** secret is present`, not `Every deploy secret is present`.

GitHub masks secret *values* in logs, and `DEPLOY_USER` is the word `deploy` — a common
English word — so every occurrence gets starred out everywhere. Harmless, permanent, and
mildly annoying. Renaming the server user to something like `ghdeploy` would fix it.

---

# Part 4 — cPanel server setup

Done once, as root. If the pipeline already works, you do not need this part.

## 4.1 Create the deploy user

🖥️ **CPANEL (root)**

```bash
useradd -m -s /bin/bash deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chown -R deploy:deploy /home/deploy/.ssh

usermod -aG hireflow deploy
chmod -R g+rwX /opt/hireflow
chmod 600 /opt/hireflow/backend/.env
sudo -u deploy git config --global --add safe.directory /opt/hireflow
```

**Two of those lines are not obvious, and both were found the hard way.**

**`chmod 600` is not optional.** The `chmod -R g+rwX` on the line above walks the whole tree
*including* `backend/.env`, turning `0600` into `0660` — and `deploy` was just added to the
`hireflow` group. Without the re-lock, the deploy user can read `SECRET_KEY`,
`EMAIL_ENCRYPTION_KEY` and the database password, which is precisely the access this entire
design exists to withhold. `.env` is gitignored, so `git pull` never touches it and
re-locking costs nothing. While you are there, run the `ls -la .env*` check from Part 0.4.

**`git config` must run as `deploy`, not root.** `--global` as root writes
`/root/.gitconfig`, which the deploy user never reads — so `git pull` later fails with
"detected dubious ownership", because git checks the directory's owner UID and group
membership does not satisfy it.

## 4.2 Grant it exactly six things

> ⚠️ **A malformed sudoers file locks out `sudo` entirely, for everyone.**
> Keep a second root shell open while you do this, and validate before installing.

🖥️ **CPANEL (root)**

```bash
visudo -c -f /opt/hireflow/deploy/cpanel/deploy-sudoers
install -m 0440 -o root -g root \
  /opt/hireflow/deploy/cpanel/deploy-sudoers \
  /etc/sudoers.d/hireflow-deploy
```

What the six groups allow:

| Group | Allows |
|---|---|
| `HF_SERVICES` | `systemctl restart / is-active / status` on the four HireFlow units, and `daemon-reload` |
| `HF_APP` | Three venv binaries as the `hireflow` user: `pip`, `alembic`, `python` |
| `HF_CONFIG` | Copy `*.service` / `*.timer` / `hireflow.conf` **from the repo tree only** into `/etc` |
| `HF_APACHE` | `ensure_vhost_includes` and `restartsrv_httpd` |
| `HF_WEB` | rsync into the career docroot, copy the SPA `.htaccess`, chown that directory |
| `HF_BACKUP` | `mysqldump` only |

Plus `Defaults:deploy noexec` — refuses to run any of the above through a shell or an editor
escape.

**Deliberately withheld:** any shell, `su`, `sudo -i`; reading or writing
`/opt/hireflow/backend/.env`; unrestricted `cp` / `rm` / `systemctl` / `mysql`; and anything
as `hfjudge` (which is in the `docker` group, and docker group membership is equivalent to
root on a server shared with four other accounts).

Verify — the last two **must be refused**:

```bash
sudo -u deploy sudo -l
sudo -u deploy sudo systemctl is-active hireflow-api    # works
sudo -u deploy sudo cat /opt/hireflow/backend/.env      # MUST fail
sudo -u deploy sudo systemctl restart sshd              # MUST fail
```

## 4.3 Create the key pair

💻 **LOCAL** — generate on your machine, never on the server.

```powershell
ssh-keygen -t ed25519 -C "github-actions-hireflow" -f hireflow-deploy -N '""'
```

No passphrase: GitHub Actions cannot type one.

This gives you two files — `hireflow-deploy` (private, goes into the GitHub secret) and
`hireflow-deploy.pub` (public, goes on the server).

## 4.4 Install the public key

💻 **LOCAL**

```powershell
type hireflow-deploy.pub
```

🖥️ **CPANEL (root)** — paste that single line in:

```bash
echo 'ssh-ed25519 AAAA... github-actions-hireflow' >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
```

## 4.5 Verify the key works

💻 **LOCAL**

```powershell
ssh -i hireflow-deploy deploy@66.116.207.71 "whoami"
ssh -i hireflow-deploy deploy@66.116.207.71 "sudo systemctl is-active hireflow-api"
```

Expected: `deploy`, then `active`, **with no password prompt**. Both matter — the first
proves the key, the second proves sudo works through it, which is what the deploy actually
needs.

Once `DEPLOY_SSH_KEY` is saved in GitHub, delete your local copy of the private key.

---

# Part 5 — How the pipeline works

Two workflows. `ci.yml` tests every branch; `deploy.yml` ships `main`.

## 5.1 CI — `.github/workflows/ci.yml`

Runs on **every** push to any branch, and on PRs into `main` / `testing` / `develop`. Four
jobs in parallel:

| Job | What it does |
|---|---|
| `lint` | `flake8 routes/ src/`. Split out on purpose — a pytest failure used to hide lint entirely |
| `backend` | `pytest -m "not slow"` against a MySQL 8 service container, with cached judge sandbox images |
| `migrations` | Proves the schema the code expects matches the schema the migrations produce |
| `frontend` | `npm install`, `vitest`, and a production `vite build` |

### Why the `migrations` job checks out the repo twice

This is the job that failed on you, and the reason is genuinely subtle.

**`alembic upgrade head` on an empty database cannot work here.** The first revision
(`2a077fcbf6c4`) has no parent, yet its first statement is `CREATE INDEX ... ON interviews` —
it *alters* tables it expects to already exist. There is no `create_table` for the base
schema anywhere in the chain, because Alembic was adopted **after** the schema existed. It
only records changes made since. On an empty database it fails immediately with
`Table '...interviews' doesn't exist`. This is stated at `docs/CPANEL_DEPLOYMENT.md:703`.

So the job mirrors a real deploy instead, using two working copies side by side:

- `base/` — a checkout of `main`, the schema as production has it
- `pr/` — a checkout of your branch

1. In `base/backend`: build the schema from `main`'s models (`import main`, then `init_db()`),
   then `alembic stamp head`. This is the "already-deployed server" state.
2. In `pr/backend`: `alembic upgrade head`. Only the revisions **your branch adds** actually
   run — exactly what happens on the server.
3. In `pr/backend`: `python scripts/schema_diff.py`.

The `import main` in step 1 is load-bearing: `create_all()` only builds tables for models
that have been imported, and the judge and question tables live in separate modules.

The job also fails if there is more than one Alembic head, which means two branches added
migrations in parallel.

### `schema_diff.py` fails on missing indexes too

Not just missing tables and columns. A `WARN: indexes are missing` still exits `1`, because
a missing `UNIQUE` constraint means duplicate rows are already possible — the app will not
500, it will quietly accept data it should reject. Types are deliberately **not** compared;
MySQL reflection produces mostly false positives there, and a noisy check gets ignored.

## 5.2 Deploy — `.github/workflows/deploy.yml`

### Why it triggers on CI finishing, not on the push

```yaml
on:
  workflow_run:
    workflows: ["HireFlow CI"]
    types: [completed]
    branches: [main]
```

A plain `on: push` would start `deploy.yml` and `ci.yml` at the **same instant**. The deploy
would race the tests and could ship code whose suite is still running — or has already
failed, with the red tick arriving after the bad code is live.

That is worse than having no CI at all, because it *looks* like something checked.

So the trigger is CI **completing**, and the job's `if:` inspects the conclusion:

```yaml
if: >-
  github.event_name == 'workflow_dispatch' ||
  github.event.workflow_run.conclusion == 'success'
```

A red CI run deploys nothing.

### The `head_sha` trap

On a `workflow_run` event, **`github.sha` is the default branch's HEAD, not the commit that
was tested.** Every ref in this workflow comes from `github.event.workflow_run.head_sha`
instead.

The difference only shows up when two commits land close together — and then it ships the
wrong one. If you ever edit this workflow, that is the mistake to watch for.

### The deploy job, step by step

Eighteen steps. This table is here because *"why was that step grey?"* is the question you
will actually have.

| # | Step | Runs when |
|---|---|---|
| 1 | Say what is being deployed | always |
| 2 | Every deploy secret is present | always |
| 3 | Download the frontend artifact | always |
| 4 | Open an SSH session | always |
| 5 | Pull and compute the change set | always |
| 6 | Back up the database | a migration changed |
| 7 | Install Python dependencies | `requirements-cpanel.txt` changed |
| 8 | Prefetch ML models | `requirements-cpanel.txt` changed |
| 9 | Migrate and verify the schema | always |
| 10 | Update systemd units | a `.service` or `.timer` changed |
| 11 | Update the Apache vhost include | `hireflow.conf` changed |
| 12 | Publish the frontend | anything under `frontend/` changed |
| 13 | Restart services and verify | always |
| 14 | Record the rollback point | the frontend published successfully |
| 15 | Record what was deployed | the deploy succeeded |
| 16 | Roll back on failure | the deploy failed |
| 17 | Note that no rollback was needed | failed before anything changed |
| 18 | Smoke checks a pipeline cannot do | the deploy succeeded |

**Every one of those 18 also carries `!inputs.dry_run`** — which is why a dry run shows a
wall of grey. That is the feature, not a fault.

Steps 6–12 are conditional on a real `git diff --name-only` between the old and new commits.
This is the pipeline automating `docs/DEPLOY_UPDATE.md` including its conditionals: `git pull`
leaves four things stale — the database, the systemd units, the Apache config, and the built
frontend — and **each of those four has an outage behind it**.

---

# Part 6 — Running a deploy

Three ways. Most days you use the first and never think about it.

| Path | How | Ships |
|---|---|---|
| **Automatic** | Merge to `main`; CI goes green | The tested commit, labelled `main@<sha>` |
| **Dry run** | Actions → Deploy → tick `dry_run` | Nothing — a rehearsal |
| **Tagged release** | Cut `vX.Y.Z`, then dispatch it | That exact tag |

## 6.1 Automatic — the normal path

🌐 **GITHUB** — merge the PR into `main`. That is the entire procedure.

CI starts. If it passes, Deploy starts on its own. Watch it under the **Actions** tab.

## 6.2 Dry run — rehearse without changing anything

🌐 **GITHUB** — Actions → **Deploy to cPanel** → **Run workflow** → tick **`dry_run`** → Run.

Use it after changing anything about the pipeline, the secrets, or the server's SSH setup.
It exercises the six secrets, the host key, the SSH config and sudo against the **real
server** — it is a rehearsal, not an offline simulation.

**Reading the result correctly:** almost every step will be grey. That is `!inputs.dry_run`
working. What you are checking is that these four are green:

```
✅ Every *** secret is present     ← all six found and well-formed
✅ Open an SSH session             ← GitHub reached your server
✅ Pull and compute the change set
✅ build                           ← the frontend compiled and tests passed
```

One genuinely useful detail: **the read-only commands are not skipped.** Step 5 still runs
`git rev-parse` and `git diff --name-only` for real, so expanding *"Pull and compute the
change set"* tells you exactly what a real deploy would do — which files changed, and
therefore which of steps 6–12 would fire.

## 6.3 Tagged release

🌐 **GITHUB** — Actions → **Cut a release** → choose `patch` / `minor` / `major` → Run.

This writes the `VERSION` file, syncs `frontend/package.json`, tags the commit and publishes
release notes. **It does not deploy.** Then Actions → **Deploy to cPanel** → Run workflow →
enter the tag, e.g. `v2.1.0`.

Use this when you want a numbered, re-deployable version — `/health` will report `v2.1.0`
rather than a commit SHA.

---

# Part 7 — Verify the deploy landed

## 7.1 The automated checks

The pipeline already restarted the services, waited 25 seconds, confirmed all four report
`active`, and required `"status":"healthy"` from `/health`. If any of that failed, it rolled
back — see Part 8.

Confirm the **right** commit shipped:

🖥️ **CPANEL (root)**

```bash
git -C /opt/hireflow rev-parse HEAD
```

Compare with the SHA in the run summary. This is where the `head_sha` trap from 5.2 would
show itself.

## 7.2 The five things the pipeline cannot check

From `docs/DEPLOY_UPDATE.md:172-178`. Every one of these fails **quietly** — that is exactly
why they are on the list, and why they are still manual:

| Check | Why it is on this list |
|---|---|
| Log in as an **HR** user with jobs assigned | The role most exposed to schema drift |
| Send one real email | Encryption-key and mailbox failures produce no error |
| Start a test, `F12` → Network → **WS** shows `101` | Proctoring failures show as "Checking…" forever |
| Run Code with `print("hello")` | Proves the judge worker and its queue |
| Open a candidate CV | Proves storage keys still resolve |

Auto-deploy makes these **more** important, not less: with a manual deploy you were prompted
to run them. Now nobody is.

You can check the WebSocket from the command line too:

🖥️ **CPANEL (root)**

```bash
curl -s -o /dev/null -w '%{http_code}\n' --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://career.innomaxskills.com/api/v1/proctor/ws/video/probe
```

Expected: **`101`**.

> **`--http1.1` is mandatory.** `mod_http2` makes curl negotiate HTTP/2, where
> `Connection: Upgrade` is illegal — so a perfectly healthy deployment returns a misleading
> `404` and you spend an afternoon debugging nothing.

---

# Part 8 — Rollback

## 8.1 What happens automatically

If any deploy step fails, the pipeline rolls back on its own:

1. `git reset --hard` to the commit that was live before (captured in step 5, and validated
   as a real 40-character SHA before anything destructive runs).
2. Restores the previous frontend from `/opt/hireflow/.dist-prev` — **re-copying `.htaccess`
   *after* the rsync**, because `rsync --delete` removes it and then every deep link 404s.
3. Restarts the four services and re-checks `/health`.
4. Rewrites `.release.json` with `"state": "rolled-back"` and appends to
   `.release-history.jsonl`.

## 8.2 What it deliberately does not do

**The database schema is not rolled back.** A half-applied migration is worse than a newer
schema running under older code, and the migrations here are additive — old code ignores new
columns.

Pre-deploy dumps are at `/root/predeploy-<date>-<time>.sql`. Restoring one is a deliberate,
human decision. If you need it, read `docs/DEPLOY_UPDATE.md` first.

## 8.3 Rolling back by hand

🖥️ **CPANEL (root)**

```bash
cd /opt/hireflow
git log --oneline -10
git checkout --detach <good-sha>
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
sleep 25
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
curl -s localhost:8000/health
```

The frontend is separate — it lives in `public_html/career`, not in the repo. Restore it from
`/opt/hireflow/.dist-prev`, or re-run the deploy for a known-good commit.

---

# Part 9 — Troubleshooting

Every row here is something that actually happened on this project.

| Symptom | Real cause | Fix |
|---|---|---|
| Deploy fails with a **DNS error inside the rollback step** | `DEPLOY_HOST` secret was missing, so ssh tried to resolve the literal alias `target`. The visible error was 100 lines past the real one | Set all six secrets; the preflight step now names any that are missing |
| CI: `Table 'interviews' doesn't exist` | `alembic upgrade head` on an empty database — it cannot work here | The two-checkout bootstrap in the `migrations` job (5.1) |
| `git pull`: **detected dubious ownership** | `git config --global` was run as root, writing `/root/.gitconfig`, which `deploy` never reads | `sudo -u deploy git config --global --add safe.directory /opt/hireflow` |
| `deploy` can read `.env` | `chmod -R g+rwX` turned it `0660` and `deploy` is in that group | `chmod 600 /opt/hireflow/backend/.env` |
| Required reviewers checkbox is missing | Paid feature, private repo | Not fixable on this plan. CI is the only gate — see 3.2 |
| Frontend and backend disagree after a deploy | `build` checked out the wrong ref, shipping one commit's frontend with another's backend | `ref: ${{ needs.validate.outputs.ref }}` |
| `git push` rejected, "behind" | Someone else merged first | `git merge origin/main`, resolve, push. **Never force** |
| Proctoring hangs on "Checking…" forever | `PORT` in `.env` overrode the systemd unit; both services fought for 8000 | Part 0.2's forbidden-variable grep |
| A multi-line paste mangles itself in the terminal | Line wrapping in the WHM terminal | `scp` the file, or `git show origin/main:path > /root/tmp` |
| `npm ci` fails with `EUSAGE` on the server | The lockfile has drifted | You should not be running npm on the server — the pipeline builds the frontend on the runner |

## Where to look when something is wrong

🖥️ **CPANEL (root)**

```bash
journalctl -u hireflow-api -n 100 --no-pager
journalctl -u hireflow-proctor -n 100 --no-pager
journalctl -u hireflow-api -f                     # follow live
systemctl status hireflow-api
```

---

# Appendix A — Environment variables

Two templates, both in `backend/`. **`.env.cpanel.example` is the authoritative one for the
server**; `.env.example` is older, GCP-oriented, and kept for local reference.

Names and purpose only. No values appear in this document, and none should be added to it.

## A.1 Server — `backend/.env.cpanel.example`

**Database**
| Variable | Notes |
|---|---|
| `DATABASE_URL` | cPanel prefixes every database and user with the account name |
| `DB_BOOTSTRAP` | **Must be `0`** on a managed host. At `1` the app tries `CREATE DATABASE` as root on every worker boot |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | Defaults are 50 connections *per worker*, which exhausts MySQL |
| `DB_POOL_RECYCLE` | |

**Security**
| Variable | Notes |
|---|---|
| `SECRET_KEY` | Carry over when migrating. The app **refuses to start** without it |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | |

**Gemini** — `GEMINI_API_KEY` (selects the Developer API; leave `GOOGLE_CLOUD_PROJECT` unset),
`GEMINI_MODEL`. **One key covers every AI feature.** It is currently blank in production.

**Storage** — `STORAGE_BACKEND` (`local`), optionally `HIREFLOW_DATA_DIR`, `BACKUP_DIR`.

**Coding judge** — `JUDGE_BACKEND` (`docker`), `JUDGE_WORK_ROOT`, `JUDGE_REGRADE_TOKEN`
(unset means deferred coding marks are never folded back in), `JUDGE_AUTOSTART` (must stay
`0` off GCP).

**Queues** — `REDIS_URL`, `EMAIL_HOURLY_LIMIT`, `EMAIL_MAX_RETRIES`.

**Email** — `EMAIL_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`,
`FROM_EMAIL`, and `EMAIL_ENCRYPTION_KEY`.

> `EMAIL_ENCRYPTION_KEY` is **critical when migrating** and cannot be regenerated. It
> decrypts per-mailbox SMTP passwords. A wrong value makes `decrypt()` return `""` rather
> than raise — so mail silently stops with no error anywhere.

**Proctoring** — `PROCTOR_PREWARM`, `PROCTOR_INFERENCE_SLOTS` (the capacity knob: memory ≈
slots × 115 MB, and does **not** grow with candidate count), `PROCTOR_MAX_SESSIONS`,
`OMP_NUM_THREADS` (must be `1`), `HF_HOME`.

**First accounts** — `SUPERADMIN_EMAIL` / `_PASSWORD`, `PLATFORM_ADMIN_EMAIL` / `_PASSWORD`.
Self-registration is candidate-only, so an empty database has no other way in.

**Application** — `ENVIRONMENT`, `DEBUG`, `LOG_LEVEL`, `FRONTEND_URL`, `PLATFORM_BASE_URL`.

### Must NOT appear in the server's `.env`

`PORT` · `WEB_CONCURRENCY` · `GOOGLE_CLOUD_PROJECT` · `VERTEX_LOCATION` · `GCS_BUCKET` ·
`JUDGE0_URL` · `JUDGE0_TOKEN` · `JUDGE_GCP_PROJECT` · `JUDGE_GCP_ZONE` · `JUDGE_VM_NAME` ·
`JUDGE_REAP_TOKEN`

`PORT` and `WEB_CONCURRENCY` are per-service and belong in the systemd units. See Part 0.2
for the check, and the outage it prevents.

## A.2 Local — `backend/.env.example`

Groups: Database · Security (`SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`) ·
Vertex AI (`GOOGLE_CLOUD_PROJECT`, `VERTEX_LOCATION`, `GEMINI_MODEL`) · Resume extraction
(`RESUME_LLM_EXTRACTION`, `RESUME_LLM_CONCURRENCY`, `RESUME_LLM_TEXT_CHARS`) · Embedding
cache (`ATS_EMBEDDING_CACHE_SIZE`) · File upload · Email/SMTP · Redis · Coding judge ·
Email queue · Application (`HOST`, `PORT`, `FRONTEND_URL`) · CORS · Rate limiting · Monitoring · Celery · S3 · Webhooks · Feature flags.

Its `DATABASE_URL` example still shows `postgresql://` — that is stale. Use MySQL.
The `ATS_*_WEIGHT` block in that file is also inert: nothing reads it. The scoring
rubric lives in `DEFAULT_ATS_WEIGHTS` in `backend/src/services/enhanced_ats_service.py`.

---

# Appendix B — Command index by terminal

The page to open when you are mid-task and just need the command.

### 💻 LOCAL (Windows PowerShell)

```powershell
# Setup
python -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python setup_db.py

# Run
python -m uvicorn main:app --reload --port 8000     # backend  (NOT python main.py)
npm run dev                                          # frontend, port 3000

# Test
pytest ; pytest -m "not slow"
npm test

# Git
git fetch ; git status -sb
git checkout -b feature/my-change
git merge origin/main

# Keys and secrets
ssh-keygen -t ed25519 -C "github-actions-hireflow" -f hireflow-deploy -N '""'
ssh -i hireflow-deploy deploy@66.116.207.71 "whoami"
gh secret list
```

### 🖥️ CPANEL (root)

```bash
# Health — all read-only
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
ss -ltnp | grep -E ':800[01]'
curl -s localhost:8000/health
git -C /opt/hireflow rev-parse HEAD
cat /opt/hireflow/.release.json

# .env inspection — never `cat` this file
awk -F= '/^[A-Z_]+=/{printf "%-28s %d chars\n", $1, length($2)}' /opt/hireflow/backend/.env
grep -nE '^(PORT|WEB_CONCURRENCY|GOOGLE_CLOUD_PROJECT)=' /opt/hireflow/backend/.env
ls -la /opt/hireflow/backend/.env*

# Logs
journalctl -u hireflow-api -n 100 --no-pager
journalctl -u hireflow-api -f

# One-time setup
useradd -m -s /bin/bash deploy
usermod -aG hireflow deploy
chmod -R g+rwX /opt/hireflow && chmod 600 /opt/hireflow/backend/.env
sudo -u deploy git config --global --add safe.directory /opt/hireflow
visudo -c -f /opt/hireflow/deploy/cpanel/deploy-sudoers
install -m 0440 -o root -g root /opt/hireflow/deploy/cpanel/deploy-sudoers /etc/sudoers.d/hireflow-deploy
sudo -u deploy sudo -l

# Manual restart
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

### 🌐 GITHUB (web UI)

| Task | Path |
|---|---|
| Add the six secrets | Settings → Secrets and variables → Actions |
| Create the environment | Settings → Environments → New environment → `production` |
| Dry run | Actions → Deploy to cPanel → Run workflow → tick `dry_run` |
| Deploy a tag | Actions → Deploy to cPanel → Run workflow → enter `vX.Y.Z` |
| Cut a release | Actions → Cut a release → choose the bump |
| Watch a deploy | Actions → the running job |

---

# Known gaps

Recorded here so they stay decisions rather than surprises.

- **`GEMINI_API_KEY` is blank in production.** Every AI feature is inactive until it is set.
  Part 0.2 shows how to confirm this yourself.
- **Proctoring and the coding judge have no CI coverage at all.** `requirements-ci.txt` omits
  `opencv-python`, `mediapipe` and `ultralytics`, so those tests *skip* rather than run. A
  regression in either reaches candidates mid-exam with nothing having checked it. Closing
  this is the highest-value improvement available to this pipeline.
- **There is no human approval gate** — required reviewers are unavailable on this plan. CI
  is the only thing between a merge to `main` and production.
- **Nothing runs the smoke matrix.** With a manual deploy you were prompted to. Now you are
  not, so Part 7.2 has to be a habit.
- **Open security findings** in registration/OTP: unthrottled brute force on `/verify-otp`
  and `/reset-password`, OTPs written to logs in plaintext and stored unhashed, and `random`
  used where `secrets` is required.
