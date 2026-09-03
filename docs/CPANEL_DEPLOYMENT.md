# HireFlow — cPanel / AlmaLinux 9 deployment guide

Step-by-step, including where to click and where to paste. Follow top to bottom.

**Target:** `career.innomaxskills.com`
**Server:** `66.116.207.71` — AlmaLinux 9, 8 vCPU, 32 GB RAM
**cPanel account:** `innomaxskills`

Each step lists a **Check:** line. If it doesn't match, stop and fix it before
moving on — later steps assume the earlier ones worked.

> **Changing the deployment rather than performing it?** Read
> [DEPLOYMENT_NOTES.md](DEPLOYMENT_NOTES.md) first. It records the constraints
> that are easy to break unknowingly, the failures that produce no error message,
> and why several things are shaped the way they are.

> **Notation.** `[root]` = paste into the root terminal (Step 0.2).
> `[cPanel]` / `[WHM]` = do it in the browser.

---

## Contents

- [Step 0 — Get logged in](#step-0--get-logged-in)
- [Step 1 — Collect these before you start](#step-1--collect-these-before-you-start)
- [Step 2 — Install server software](#step-2--install-server-software)
- [Step 3 — The database](#step-3--the-database)
- [Step 4 — Create users and folders](#step-4--create-users-and-folders)
- [Step 5 — Create the subdomain and SSL](#step-5--create-the-subdomain-and-ssl)
- [Step 6 — Install the application](#step-6--install-the-application)
- [Step 7 — Configure the .env file](#step-7--configure-the-env-file)
- [Step 8 — Download AI models](#step-8--download-ai-models)
- [Step 9 — Build the coding-judge sandboxes](#step-9--build-the-coding-judge-sandboxes)
- [Step 10 — Set up the database schema](#step-10--set-up-the-database-schema)
- [Step 11 — Start the services](#step-11--start-the-services)
- [Step 12 — Connect Apache](#step-12--connect-apache)
- [Step 13 — Build and upload the website](#step-13--build-and-upload-the-website)
- [Step 14 — Test everything](#step-14--test-everything)
- [Troubleshooting](#troubleshooting)
- [Tuning after real load](#tuning-after-real-load)
- [If something breaks](#if-something-breaks)
- [After deployment](#after-deployment)
- [Deploying a code update](#deploying-a-code-update) — or go straight to [DEPLOY_UPDATE.md](DEPLOY_UPDATE.md)

---

## How the finished system fits together

```
Browser → https://career.innomaxskills.com
             │
             ├─ /                    → /home/innomaxskills/public_html/career/   (React files)
             ├─ /api/  /interviews/  → 127.0.0.1:8000   hireflow-api      (3 workers)
             └─ /proctor/ + sockets  → 127.0.0.1:8001   hireflow-proctor  (1 worker)

Background: hireflow-judge-worker (runs candidate code in Docker)
            hireflow-email-worker  (sends queued email)
```

**Why proctoring is a second service.** It stores each live session in the
memory of one process. If it ran with several workers, a candidate's video
connection could land on a different worker than the one holding their session,
and it would disconnect. So proctoring runs alone with exactly **one** worker.
Do not change that — capacity comes from `PROCTOR_INFERENCE_SLOTS` instead.

---

## What this deployment touches on a shared server

This box hosts five cPanel accounts. Here is exactly what is isolated and what
is not, so nothing comes as a surprise.

### Isolated to `innomaxskills` — cannot affect the others

| Thing | Why it is contained |
|---|---|
| Subdomain `career.innomaxskills.com` | Belongs to this account only |
| Database `innomaxskills_hireflow` | cPanel prefixes and permissions scope it per account |
| Apache config | Installed at `.../userdata/{std,ssl}/2_4/innomaxskills/career.innomaxskills.com/` — that path is read **only** for this account's vhost |
| Website files | `/home/innomaxskills/public_html/career/` |
| Application code | `/opt/hireflow` — outside every account's home directory |
| Service users | `hireflow`, `hfjudge` — new users owning nothing pre-existing |

### Server-wide — affects all five accounts

Marked **`[SERVER-WIDE]`** in the steps below.

| Action | What actually changes | Risk |
|---|---|---|
| Add Apache modules (2.1) | Adds `mod_proxy_wstunnel`; Apache restarts once | Low — purely additive; ~2 s blip for all sites |
| Install Python 3.11 (2.2) | New package alongside the system Python | Very low — nothing replaced |
| EPEL + graphics libraries (2.3) | Adds a repository and shared libraries | Low — additive, but EPEL is a third-party repo |
| Install Redis (2.4) | New service on `127.0.0.1:6379` | Low — verify the port is free first |
| Install Docker (2.5) | New daemon plus iptables rules | Moderate — can interact with CSF/firewalld |
| CPU, RAM and disk | Shared with every account | Managed — see below |

**Nothing in this guide removes, downgrades or reconfigures software the other
accounts already use.** The one step that would have — upgrading the database
engine — has been removed; see Step 3.1.

### How the other accounts are protected from resource starvation

The four service files in `deploy/cpanel/` set systemd limits:

| Service | Memory ceiling | CPU priority |
|---|---|---|
| `hireflow-proctor` | 10 GB | 40 |
| `hireflow-api` | 8 GB | 50 |
| `hireflow-judge-worker` | 2 GB | 30 |
| `hireflow-email-worker` | 1 GB | 30 |

Two deliberate choices:

- **`MemoryMax` is a hard ceiling.** If HireFlow leaks or spikes, systemd kills
  *our* service. Without it the kernel's OOM killer picks its own victim, which
  could be MySQL or another account's PHP workers.
- **`CPUWeight`, not `CPUQuota`.** A quota throttles even when the machine is
  idle, wasting capacity. Weight applies only under contention: the default is
  100, so at 40–50 our services yield to Apache, MySQL and the other accounts
  when the box is busy, while still using everything spare when it is not.

Worst case, all four services together are capped at 21 GB of 32 GB, leaving
11 GB for MySQL, Apache, cPanel and the other four accounts.

---

## Step 0 — Get logged in

### 0.1 Open WHM

In your browser go to:

```
https://66.116.207.71:2087
```

Log in as **root**. Your browser will warn about the certificate — that is normal
for an IP address. Click **Advanced → Proceed**.

### 0.2 Open a root terminal

Easiest option, no software needed:

1. `[WHM]` In the **search box at the top-left**, type `Terminal`
2. Click **Terminal** under *Server Configuration*
3. Accept the warning prompt

A black terminal opens. **This is where every `[root]` command goes.** Paste with
`Ctrl+Shift+V` (or right-click → Paste), then press `Enter`.

<details>
<summary>Prefer a real SSH client instead?</summary>

**Windows 10/11** — open PowerShell or Windows Terminal:

```
ssh root@66.116.207.71
```

**PuTTY** — Host `66.116.207.71`, Port `22`, Connection type `SSH` → **Open**,
then log in as `root`. In PuTTY you paste with a **right-click**.

Either works identically to the WHM Terminal.
</details>

**Check:** run `whoami` — it must print `root`.

### 0.3 Confirm the server is what we expect

`[root]`

```bash
cat /etc/redhat-release && nproc && free -g | head -2
```

**Check:** AlmaLinux 9.x, `8` CPUs, roughly 31 GB total memory.

### 0.4 Pre-flight checks — run these before changing anything

**This server is shared.** It hosts five cPanel accounts — `crminnomaxsol`,
`innomaxskills`, `innomax2`, `innomaxstartup`, `tropicaeco` — all on the same IP,
the same Apache, the same database engine and the same CPU and RAM. Most of this
guide only touches `innomaxskills`, but a few steps are server-wide (they are
marked **`[SERVER-WIDE]`**). Run these checks first so you know what you are
starting from.

`[root]` — paste the whole block:

```bash
echo "--- OS / CloudLinux ---";  cat /etc/redhat-release
                                 test -f /etc/cloudlinux-release && echo "CloudLinux: YES" || echo "CloudLinux: no"
echo "--- CPU / RAM / disk ---"; nproc; free -g | head -2; df -h / | tail -1
echo "--- database engine ---";  mysql --version
echo "--- databases in use ---"; mysql -e "SHOW DATABASES;" 2>/dev/null | wc -l
echo "--- ports we need ---";    ss -lntp | grep -E ':(8000|8001|6379)\b' || echo "8000, 8001, 6379 all free"
echo "--- firewall ---";         systemctl is-active csf firewalld docker 2>/dev/null
echo "--- apache modules ---";   httpd -M 2>/dev/null | grep -cE 'proxy_wstunnel'
echo "--- epel present? ---";    dnf repolist 2>/dev/null | grep -ci epel
```

Read the output against this table:

| Output | Meaning |
|---|---|
| `CloudLinux: no` | Expected. No per-account memory caps to work around. |
| RAM `total` ~31, `free` low | Normal — Linux caches aggressively. Check the `available` column, which is what matters. |
| `mysql --version` shows **MariaDB** | **Fine — do not upgrade.** See Step 3.1. |
| Ports listed as busy | Something already uses 8000/8001/6379. Stop and change the ports before continuing. |
| `csf` or `firewalld` active | Note it — Docker adds firewall rules in Step 2.5. |
| `docker` already active | Docker is installed; you can skip Step 2.5. |
| apache modules count `1` | `mod_proxy_wstunnel` is already there; Step 2.1 is a no-op. |

**Take a backup before the server-wide steps.** `[WHM]` search box → `Backup
Configuration`, and confirm backups are enabled and recent. If anything goes
wrong at Apache or Docker level, that is your way back.

### A note on editing files with `nano`

Several steps ask you to edit a file. We use `nano`, which is already installed:

| Action | Keys |
|---|---|
| Save | `Ctrl+O`, then `Enter` |
| Exit | `Ctrl+X` |
| Search | `Ctrl+W`, type text, `Enter` |

Arrow keys move the cursor. The mouse does **not** work in nano.

---

## Step 1 — Collect these before you start

Gather these now. **Two of them cannot be recovered later.**

| Item | Where to get it |
|---|---|
| Gemini API key | https://aistudio.google.com/apikey → **Create API key** |
| SMTP details | Your email provider (host, port, username, password) |
| `EMAIL_ENCRYPTION_KEY` | The **old** deployment — migration only |
| Database dump + file backup | The **old** deployment — migration only |

> ### Read this before deleting anything on the old server
>
> **`EMAIL_ENCRYPTION_KEY` cannot be regenerated.** It decrypts the mailbox
> passwords stored inside the database rows you are about to import. Import the
> data without the original key and every configured mailbox becomes permanently
> unreadable — there is no recovery. Get it from the old server first:
>
> ```
> gcloud run services describe hire-flow-api --region us-central1 --format export
> ```
>
> The same applies to the uploaded files. Once the old storage bucket is deleted,
> the resumes are gone.

**Decide now: fresh install, or migrating existing data?** This guide covers the
fresh install. Bringing real data across is a separate runbook —
[GCP_DATA_MIGRATION.md](GCP_DATA_MIGRATION.md) — and it is usually done *after*
the site is up and working, not during.

---

## Step 2 — Install server software

### 2.1 Apache WebSocket module  `[SERVER-WIDE]`

`[root]`

```bash
yum install -y ea-apache24-mod_proxy ea-apache24-mod_proxy_http ea-apache24-mod_proxy_wstunnel
```

**Check:**

```bash
httpd -M 2>/dev/null | grep -E 'proxy_wstunnel|proxy_http'
```

Both must be listed. `mod_proxy_wstunnel` is **not** installed by default, and
without it the live proctoring video will never connect.

### 2.2 Python 3.11  `[SERVER-WIDE]`

`[root]`

```bash
dnf install -y python3.11 python3.11-devel
```

**Check:** `python3.11 --version` prints `Python 3.11.x`.

> Do **not** use cPanel's "Setup Python App" for this. That system cannot handle
> the live video connections proctoring needs.

### 2.3 Graphics and OCR libraries  `[SERVER-WIDE]`

`[root]`

```bash
dnf install -y epel-release
dnf install -y mesa-libGL mesa-libEGL glib2 libsndfile tesseract gcc gcc-c++ make
```

**Check:** `tesseract --version` prints a version number.

These are required by the AI vision libraries. Skip them and proctoring fails
later with `libGL.so.1: cannot open shared object file`.

### 2.4 Redis  `[SERVER-WIDE]`

`[root]`

```bash
dnf install -y redis
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
systemctl enable --now redis
```

**Check:** `redis-cli ping` replies `PONG`.

### 2.5 Docker  `[SERVER-WIDE]`

`[root]`

```bash
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io
systemctl enable --now docker
```

**Check:** `docker run --rm hello-world` prints "Hello from Docker!".

Docker runs candidate code safely isolated. No reboot is needed.

---

## Step 3 — The database

### 3.1 Do NOT change the database engine

**Use whatever is already installed.** cPanel cannot run without MySQL or
MariaDB, so it is already there and the other four accounts already have live
databases on it.

> ### Why this guide no longer tells you to upgrade to MySQL 8.0
>
> An earlier draft did, to match the old Google Cloud setup exactly. That is the
> wrong call on a shared server:
>
> - The engine is **server-wide** — all five accounts share one instance.
> - cPanel's MariaDB → MySQL upgrade is **irreversible**; there is no downgrade.
> - It could break the other four sites if any depend on MariaDB behaviour.
>
> And HireFlow does not need it. The application stores JSON as whole documents
> that Python serialises, never querying into them with SQL JSON functions, and
> uses no MySQL-8-only syntax. It runs correctly on MariaDB 10.5+.
>
> **Skip the upgrade. There is nothing to install in this step.**

**Check:** `[root]` `mysql --version` prints *something* — MariaDB or MySQL, either
is fine. Note which, in case you need it for support later.

### 3.2 Create the database and user

1. `[WHM]` Search box → `List Accounts`
2. Find **innomaxskills** and click the orange **cPanel** icon — this opens
   cPanel for that account
3. `[cPanel]` Search box → `MySQL`, click **MySQL® Databases**

Then, on that page:

- **Create New Database** → name it `hireflow`
  → cPanel saves it as **`innomaxskills_hireflow`**
- **Add New User** → username `hf`, click **Password Generator**,
  **copy the password somewhere safe** → cPanel saves it as
  **`innomaxskills_hf`**
- **Add User To Database** → select the user and the database → **Add** →
  tick **ALL PRIVILEGES** → **Make Changes**

**Check:** the database now lists one privileged user.

> cPanel automatically prefixes names with the account. The full names are
> `innomaxskills_hireflow` and `innomaxskills_hf` — you need both in Step 7.

---

## Step 4 — Create users and folders

`[root]`

```bash
useradd -r -m -d /opt/hireflow -s /bin/bash hireflow
useradd -r -s /sbin/nologin -G docker hfjudge
mkdir -p /srv/judge_work && chown hfjudge:hfjudge /srv/judge_work
```

**Check:** `id hfjudge` lists `docker` among its groups.

> ### Security note — please read
>
> Membership of the `docker` group is equivalent to root access on this machine.
> This server also hosts `crminnomaxsol`, `innomax2`, `innomaxstartup` and
> `tropicaeco`.
>
> We give it to `hfjudge`, a locked-down user that runs a single background
> process and cannot log in — which is far safer than giving it to the user
> serving web traffic. It is still a real privilege on a shared box, so accept it
> deliberately. The alternative is running the code judge on a separate machine.

---

## Step 5 — Create the subdomain and SSL

### 5.1 Create the subdomain

1. `[cPanel]` (still in the `innomaxskills` account) Search box → `Domains`
2. Click **Domains** → **Create A New Domain**
3. **Domain:** `career.innomaxskills.com`
4. **Untick** "Share document root"
5. **Document Root:** `/home/innomaxskills/public_html/career`
6. **Submit**

<details>
<summary>Older cPanel versions</summary>

Search for **Subdomains** instead. Subdomain `hire`, Domain `innomaxskills.com`,
Document Root `public_html/career`, then **Create**.
</details>

**Check:** `[root]` `ls -d /home/innomaxskills/public_html/career` exists.

### 5.2 Issue the SSL certificate

1. `[cPanel]` Search box → `SSL`
2. Click **SSL/TLS Status**
3. Tick `career.innomaxskills.com`
4. Click **Run AutoSSL**
5. Wait for a green padlock next to the domain (can take a few minutes)

**Check:**

```bash
curl -sI https://career.innomaxskills.com | head -1
```

You should get an HTTP response line with **no certificate error**. A 403 or 404
is fine at this stage — nothing is installed yet.

> Confirm the certificate **now**. Secure video connections fail silently on a bad
> certificate, so a problem here shows up much later looking like "proctoring is
> broken" rather than an SSL error.

---

## Step 6 — Install the application

### 6.1 Download the code

`[root]`

```bash
git clone https://github.com/innomax-it-solutions/Hiring_system_Cpanel.git /opt/hireflow
chown -R hireflow:hireflow /opt/hireflow
```

**Check:** `ls /opt/hireflow` shows `backend`, `frontend`, `deploy`, `docs`.

### 6.2 Create the Python environment

`[root]`

```bash
sudo -u hireflow python3.11 -m venv /opt/hireflow/venv
sudo -u hireflow /opt/hireflow/venv/bin/pip install -U pip wheel
```

### 6.3 Install dependencies

**This takes 5–15 minutes.** Leave the terminal open.

`[root]`

```bash
sudo -u hireflow /opt/hireflow/venv/bin/pip install -r /opt/hireflow/backend/requirements-cpanel.txt
```

**Check:** confirm the CPU build of PyTorch was installed, not the graphics-card one:

```bash
/opt/hireflow/venv/bin/python -c "import torch; print(torch.__version__, '| CUDA build:', torch.version.cuda)"
du -sh /opt/hireflow/venv
```

**Expected:** `2.1.1+cpu | CUDA build: None`, and a venv of roughly **2–3 GB**.

| Result | Meaning |
|---|---|
| ends `+cpu`, `CUDA build: None` | Correct. Continue. |
| a CUDA version number, venv 6–8 GB | The graphics-card build got in — see below. Not fatal; it still runs on CPU. |

> Do not use `pip list \| grep nvidia` to judge this. `ultralytics` pulls in
> `nvidia-ml-py`, a ~50 KB pure-Python package for querying GPU stats that
> contains no CUDA libraries at all — so its presence is normal and means
> nothing. `torch.version.cuda` is the real answer.

> Use `requirements-cpanel.txt`, **never** `requirements.txt`. The plain file
> downloads a graphics-card version of PyTorch — about 2 GB of files this server
> cannot use — and includes a database driver that fails to build here and aborts
> the whole install.

<details>
<summary>If the CUDA build was installed anyway</summary>

`requirements-cpanel.txt` uses `--extra-index-url` for the CPU wheels. *Extra*
means additional, not exclusive — pip may still choose PyPI's CUDA build, since
`torch==2.1.1` exists on both. Everything works either way; the cost is a few GB
of disk and a little memory. To reclaim it:

```bash
sudo -u hireflow /opt/hireflow/venv/bin/pip uninstall -y torch
sudo -u hireflow /opt/hireflow/venv/bin/pip install torch==2.1.1 \
  --index-url https://download.pytorch.org/whl/cpu
```

`--index-url` replaces the index rather than adding to it, so the CPU wheel is
the only candidate.
</details>

---

## Step 7 — Configure the .env file

### 7.1 Generate a security key

`[root]`

```bash
/opt/hireflow/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Copy the output.** You need it in a moment.

### 7.2 Create the file

`[root]`

```bash
sudo -u hireflow cp /opt/hireflow/backend/.env.cpanel.example /opt/hireflow/backend/.env
sudo -u hireflow chmod 600 /opt/hireflow/backend/.env
nano /opt/hireflow/backend/.env
```

### 7.3 Fill in these values

Edit each line (see the nano keys in Step 0). Use `Ctrl+W` to jump to a setting.

| Setting | Value |
|---|---|
| `SECRET_KEY=` | the key from 7.1 |
| `DATABASE_URL=` | `mysql+pymysql://innomaxskills_hf:YOURPASSWORD@localhost:3306/innomaxskills_hireflow` |
| `GEMINI_API_KEY=` | your key from Step 1 |
| `SMTP_HOST=` `SMTP_PORT=` `SMTP_USER=` `SMTP_PASS=` `FROM_EMAIL=` | your email provider details |
| `SUPERADMIN_EMAIL=` `SUPERADMIN_PASSWORD=` | **required on a fresh install** — see below |
| `PLATFORM_ADMIN_EMAIL=` `PLATFORM_ADMIN_PASSWORD=` | **required on a fresh install** — see below |
| `EMAIL_ENCRYPTION_KEY=` | migration only — **read the box below, it may not exist under that name** |

> ### Without the admin accounts you cannot log in at all
>
> Self-registration is candidate-only — HR, interviewer and admin accounts are
> normally created by an existing admin. On an empty database that leaves no way
> in. `src/bootstrap.py` seeds the first two accounts from these variables at
> startup; both are no-ops if the user already exists.
>
> Generate passwords with:
>
> ```bash
> /opt/hireflow/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(18))"
> ```
>
> Use addresses you can receive mail at — password resets go there. After the
> accounts exist you may blank the passwords, but leave the email values: the
> seeding is skipped entirely when they are empty.
>
> Skip this and you get a deployment that passes every health check and that you
> cannot administer. Migrating existing data instead? Your old admin accounts
> come across with the dump, so these are optional in that case.

> ### `EMAIL_ENCRYPTION_KEY` — the trap when migrating
>
> This decrypts the per-mailbox SMTP passwords inside the rows you import. Get it
> wrong and nothing errors: `decrypt()` returns an empty string
> (`src/security.py:42`), so mail just stops working with no message anywhere.
>
> **It may never have existed under that name.** `src/security.py` resolves the
> key in this order:
>
> 1. `EMAIL_ENCRYPTION_KEY`, used as-is
> 2. **`SECRET_KEY`**, hashed SHA-256 → base64
> 3. the literal `hireflow-dev-key-change-me`
>
> So if the old deployment only ever set `SECRET_KEY`, that is what your data is
> encrypted against — and the fresh key you generated in 7.1 produces a different
> result. **Leaving this blank is exactly how the data becomes unreadable.**
>
> Retrieve both values from the old service:
>
> ```bash
> gcloud run services describe hire-flow-api --region us-central1 \
>   --format="yaml(spec.template.spec.containers[0].env)"
> ```
>
> Or in the browser: Cloud Run → `hire-flow-api` → the **YAML** tab (read-only —
> do not use "Edit & Deploy"). If a value reads `valueFrom: secretKeyRef`, open
> Secret Manager → the secret → the version → **View secret value**.
>
> | What the old service had | What to set here |
> |---|---|
> | `EMAIL_ENCRYPTION_KEY` with a value | that value, verbatim |
> | only `SECRET_KEY` | derive it, below |
> | neither | derive it from `hireflow-dev-key-change-me` |
>
> ```bash
> python3 -c "import hashlib,base64; \
>   print(base64.urlsafe_b64encode(hashlib.sha256(b'OLD_SECRET_KEY').digest()).decode())"
> ```
>
> Setting the derived value explicitly is the better outcome either way: it lets
> this server keep a brand-new `SECRET_KEY` while still reading the old passwords.

Leave everything else as-is. Confirm these two are present and unchanged:

```
DB_BOOTSTRAP=0
STORAGE_BACKEND=local
```

Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`.

**Check:**

```bash
grep -c "^SECRET_KEY=.\+" /opt/hireflow/backend/.env
```

Must print `1`. The application deliberately refuses to start with a blank or
example security key.

> If your database password contains `@`, `:`, `/` or `#`, it must be
> percent-encoded inside `DATABASE_URL` (`@` → `%40`). Simplest fix: regenerate
> the password in cPanel without those characters.

---

## Step 8 — Download AI models

Do this now, or the first candidate to upload a CV waits for a large download.

`[root]`

```bash
sudo -u hireflow HF_HOME=/opt/hireflow/hf-cache \
  /opt/hireflow/venv/bin/python /opt/hireflow/backend/scripts/prefetch_models.py
```

**Check:** the last line says `All required models present and loadable.` and you
see:

```
engines={'landmarker': True, 'sface': True, 'pose': True, 'yolo': True}
```

**All four must be `True`.** If `landmarker` or `pose` is `False`, the vision
libraries did not install — go back to Step 6.3.

A warning about `en_core_web_trf` is expected and harmless.

---

## Step 9 — Build the coding-judge sandboxes

`[root]`

```bash
bash /opt/hireflow/backend/scripts/build_judge_images.sh
```

Takes a few minutes on first run.

**Check:**

```bash
docker images | grep hireflow-judge
```

Four entries: `python`, `cpp`, `node`, `java`.

---

## Step 10 — Set up the database schema

### 10.1 Create the tables

> **Do not run `alembic upgrade head` on an empty database — it cannot work.**
> The first revision (`2a077fcbf6c4`) has no parent but its first statement is
> `CREATE INDEX ... ON interviews`: it *alters* tables it expects to exist. There
> is no `create_table` for the base schema anywhere in the chain, because Alembic
> was adopted after the schema already existed. It only records changes made
> *since* then. On an empty database it fails immediately with
> `Table '...interviews' doesn't exist`.
>
> The schema comes from the ORM instead. The models already reflect every one of
> those migrations, so `create_all()` lands in exactly the same place — it just
> gets there in one step.

**Build the schema from the models:**

`[root]`

```bash
cd /opt/hireflow/backend

sudo -u hireflow /opt/hireflow/venv/bin/python -c "
import sys; sys.path.insert(0, '.')
import main
from src.config.database_config_mysql import init_db, db_manager
ok = init_db()
info = db_manager.get_database_info()
print('created:', ok, '| db:', info.get('database'), '| tables:', info.get('table_count'))
"
```

**Expected:** `created: True | db: innomaxskills_hireflow | tables: 44` (approximately).

`import main` is required, not decorative. `create_all()` only creates tables for
models that have been imported, and the judge and question tables live in
separate modules. Importing the app pulls them all in; importing only
`src.models.models` silently skips them.

**Then record that the schema is current**, so future upgrades start from the
right point instead of replaying history:

```bash
sudo -u hireflow /opt/hireflow/venv/bin/alembic stamp head
sudo -u hireflow /opt/hireflow/venv/bin/alembic current
```

**Expected:** `x4n5o6p7q8r9 (head)`

> `stamp` only writes a version marker — it creates no tables. Both commands are
> needed, in either order.

> **This leaves you exposed to one specific trap later.** `create_all` + `stamp
> head` means the version table now claims every migration ran, when none did.
> That is fine *today* — the models already reflect all of them. But a future
> release that adds a **column** to a table `create_all` already built will be
> skipped by `alembic upgrade head`, silently, because Alembic thinks it is
> already applied. `scripts/schema_diff.py` is the check for that; run it after
> any upgrade rather than trusting `alembic current`.

Do not use `setup_db.py`. It hardcodes `root`/`recruitment_db` credentials and
**drops tables**.

### 10.2 Importing existing data — *see the separate runbook*

**→ [GCP_DATA_MIGRATION.md](GCP_DATA_MIGRATION.md)**

Migrating real data is not a step in this guide, for two reasons.

If you are doing it **as part of a fresh install**, the target is empty and a
straight `mysql < dump.sql` works — but the dump still needs inspecting first
(`CREATE DATABASE`/`USE` lines, `DEFINER=`, `GTID_PURGED`, syntax newer than the
target server), and a dump can be stamped at head while its schema is not.

If you are doing it **into a deployment that is already running** — which is the
common case, because you usually stand the site up first and move the data once it
works — then importing into the production database means the site is broken from
the first `DROP TABLE` until verification passes, with no way back except a
restore. The runbook imports into a separate database and switches with one line in
`.env` instead.

Either way, uploaded files need no database rewriting: storage keys are identical
across backends, so it is a copy to matching relative paths.

---

## Step 11 — Start the services

`[root]`

```bash
cp /opt/hireflow/deploy/cpanel/*.service /etc/systemd/system/
cp /opt/hireflow/deploy/cpanel/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
systemctl enable --now hireflow-regrade.timer
```

**Wait 2–3 minutes.** The proctoring service loads large AI libraries on first
start — this is normal and only happens once.

`hireflow-regrade.timer` is the re-grade sweep. When the judge is unreachable at
submit time the candidate's coding answer is saved but left ungraded, and its marks
are taken out of the denominator; this timer is the only thing that puts them back.
It needs `JUDGE_REGRADE_TOKEN` set in `/opt/hireflow/backend/.env` — without it every
run gets a 401 and the recovery silently never happens. There is no
`hireflow-regrade` service to *enable*: it is a `Type=oneshot` the timer triggers.

**Check:**

```bash
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
systemctl list-timers hireflow-regrade.timer     # NEXT/LEFT columns populated
```

Four lines of `active`. `activating` on a repeat check means a restart loop —
`Restart=always` makes that look almost identical to starting up. Then:

```bash
curl -s localhost:8000/health
curl -s localhost:8001/health
```

Both return JSON containing `"status": "healthy"`, and **`table_count` must be
greater than 0**.

> `table_count: 0` means the app is connected to the wrong database. Re-check
> `DATABASE_URL` in Step 7.

**Then check that the two ports belong to the two different services.** Do not
skip this. The API and the proctor run the same application, so if the proctor
binds 8000 the site works perfectly while proctoring is silently absent:

```bash
ss -ltnp | grep -E ':800[01]'
for s in hireflow-api hireflow-proctor; do
  printf '%s: ' "$s"
  tr '\0' '\n' < /proc/$(systemctl show $s -p MainPID --value)/environ \
    | grep -E '^(ROLE|PORT|WEB_CONCURRENCY)=' | tr '\n' ' '; echo
done
```

Expected: **8000 and 8001 both listening**, `hireflow-api` with `ROLE=web
PORT=8000`, and `hireflow-proctor` with `ROLE=proctor`. Three specific wrong
answers:

| What you see | What happened |
|---|---|
| Only 8000 listening | One service took the other's port; the loser is crash-looping on `Connection in use` |
| `hireflow-proctor` reporting `ROLE=web` | Stale unit file — re-copy from `deploy/cpanel/` and `daemon-reload` |
| `PORT` or `WEB_CONCURRENCY` present in the proctor's environment | They are set in `.env`, where they override the units. Remove them — see [Deployment notes §4](DEPLOYMENT_NOTES.md#4-configuration-which-file-wins) |

If a service is not active:

```bash
journalctl -u hireflow-api -n 50 --no-pager
```

> ### Proctoring capacity is not a worker count
>
> Proctoring runs exactly **one** worker, hardcoded in `entrypoint.sh` under
> `ROLE=proctor`. Each live session is held in that one process's memory, so with
> more workers a candidate's video connection reaches the wrong process and drops
> with error 4004. `WEB_CONCURRENCY` is ignored for this service by design. To
> handle more candidates, raise `PROCTOR_INFERENCE_SLOTS` instead (see
> [Tuning](#tuning-after-real-load)).

---

## Step 12 — Connect Apache

`[root]`

```bash
mkdir -p /etc/apache2/conf.d/userdata/{std,ssl}/2_4/innomaxskills/career.innomaxskills.com
for m in std ssl; do
  cp /opt/hireflow/deploy/cpanel/hireflow.conf \
     /etc/apache2/conf.d/userdata/$m/2_4/innomaxskills/career.innomaxskills.com/
done
/scripts/ensure_vhost_includes --user=innomaxskills
/scripts/restartsrv_httpd
```

Both `std` and `ssl` are needed — cPanel rebuilds its own configuration
periodically and would otherwise discard this.

Then, only if security-enforcing mode is on:

```bash
getenforce
```

If that prints `Enforcing`, run:

```bash
setsebool -P httpd_can_network_connect 1
```

**Check 1 — plain HTTP proxying:**

```bash
curl -s https://career.innomaxskills.com/health
```

Returns the same healthy JSON as Step 11.

**Check 2 — WebSocket proxying.** This is a *separate* path through Apache and it
has failed silently before, so check it directly rather than assuming Check 1
covers it. Proctoring needs a real WebSocket upgrade; without one the candidate's
face badge sits on "Checking…" forever, zero frames are analysed, and every result
comes back clean:

```bash
curl -sS --http1.1 -o /dev/null -w '%{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  'https://career.innomaxskills.com/api/v1/proctor/ws/video/probe'
```

**Expected: `101`.** The app calls `ws.accept()` before it validates the session
(`proctoring/routes.py:1146`), so a made-up session id still completes the
handshake and is then closed with code 4004 — which is the correct outcome here.

> **`--http1.1` is required, and is not paranoia.** This server runs `mod_http2`,
> so curl negotiates HTTP/2 over TLS by default — and `Connection: Upgrade` is not
> a legal HTTP/2 header. Apache drops it, `mod_proxy_wstunnel` never sees an
> upgrade request, and the probe reports a **`404`** from a perfectly healthy
> deployment. (404 rather than 400 because Starlette matches routes by scope: an
> ordinary HTTP GET does not match a WebSocket-only route.) Real candidates are
> unaffected — browsers use HTTP/1.1 for `wss://` unless the server advertises
> RFC 8441, which Apache does not by default.

Any other status — `200`, `400`, `404` *with* `--http1.1`, `502` — means the
upgrade was forwarded as ordinary HTTP by `mod_proxy_http`. Two causes, in order
of likelihood:

1. **Nothing is listening on 8001.** A `503` says this outright. Go back to the
   port-ownership check in Step 11 — this is the more common fault, and no amount
   of Apache work fixes it.
2. **Directive precedence.** **Apache evaluates `ProxyPass` before mod_rewrite's
   `[P]` flag**, so a `RewriteRule … [P]` for a path that a later `ProxyPass` also
   matches never runs. `hireflow.conf` therefore declares the WebSocket paths as
   `ProxyPass … ws://` entries *above* the general `ProxyPass`.

To tell those apart, probe the app directly, bypassing Apache entirely:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  http://127.0.0.1:8001/api/v1/proctor/ws/video/probe
```

`101` here but not through the domain isolates the fault to Apache. Anything else
here means the service, not the proxy. Then confirm the rules got installed:

```bash
grep -c '^ProxyPass.*ws://127.0.0.1' \
  /etc/apache2/conf.d/userdata/ssl/2_4/innomaxskills/career.innomaxskills.com/hireflow.conf
grep -rn 'career.innomaxskills.com' /etc/apache2/conf/httpd.conf | head
```

The first prints `4`. The second shows the `Include` lines cPanel generated —
note the main config is `/etc/apache2/conf/httpd.conf`; `/etc/apache2/conf.d/` is
the *includes* directory, not a config file. cPanel includes the userdata files by
reference, so the rules never appear inside `httpd.conf` itself. If the `Include`
lines are missing, `/scripts/ensure_vhost_includes --user=innomaxskills` did not
take — re-run it and restart Apache.

---

## Step 13 — Build and upload the website

`[root]`

```bash
cd /opt/hireflow/frontend
npm ci
npm run build
rsync -a --delete dist/ /home/innomaxskills/public_html/career/
cp /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/career/.htaccess
chown -R innomaxskills:innomaxskills /home/innomaxskills/public_html/career
```

If `npm` is missing:

```bash
dnf install -y nodejs npm
```

**Check:** `ls /home/innomaxskills/public_html/career` shows `index.html` and an
`assets` folder.

Open **https://career.innomaxskills.com** — the login page should appear.

---

## Step 14 — Test everything

Work through these in order.

| # | Test | How | Expected |
|---|---|---|---|
| 1 | Website loads | Open `https://career.innomaxskills.com` | Login page, no errors in browser console (`F12`) |
| 2 | Sign-up + email | Register a test account | OTP email arrives; you can log in |
| 3 | Migrated files | Open a candidate with an old CV | The PDF opens |
| 4 | CV scoring | Upload a CV against a job | A score appears |
| 5 | AI questions | Generate interview questions for a job | Questions appear |
| 6 | Code judge | In a test, Run Code in Python, C++, JavaScript, Java **and SQL** | Each returns a result |
| 7 | Proctoring | Start a test with camera + microphone | Video preview works and stays connected |
| 8 | Capacity | See below | Numbers recorded |

For test 4, confirm the good scoring engine is active:

```bash
journalctl -u hireflow-api | grep "sentence-transformers loaded"
```

If instead you see a TF-IDF fallback warning, Step 8 did not complete.

For test 6, watch containers appear while code runs:

```bash
docker ps
```

For test 8, with real sessions running:

```bash
curl -s https://career.innomaxskills.com/api/v1/proctor/warmup
free -g
```

Record `capacity`, `in_use`, `peak_in_use`, `waits`, `active_sessions`.

> ### Test 7 is the one to budget time for
>
> It is the only test that exercises the WebSocket module, the Apache rules and
> the SSL certificate all at once, and none of that could be tested before the
> server existed. Expect to need a round or two of adjustment.
>
> To confirm properly: press `F12` → **Network** tab → filter **WS**. You should
> see connections with status **101**. Anything else — including a **503** — means
> the request never reached the proctor service; check the port ownership test in
> Step 11 before touching the Apache rules. A **4004** disconnect means the socket
> reached a process that does not hold the session, i.e. more than one proctor
> worker, which `ROLE=proctor` should now make impossible.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Service will not start | Missing/placeholder `SECRET_KEY` | Redo Step 7.1–7.3 |
| `table_count: 0` | Wrong database in `DATABASE_URL` | Check names include the `innomaxskills_` prefix |
| `libGL.so.1: cannot open...` | Graphics libraries missing | Redo Step 2.3, then `systemctl restart hireflow-proctor` |
| Proctoring stuck on "Checking…", results all clean | Nothing listening on 8001 — usually the proctor bound 8000 instead | Run the port-ownership check in Step 11 |
| Proctoring WebSocket returns 503 | Proctor service down, or on the wrong port | `ss -ltnp \| grep :8001`, then `journalctl -u hireflow-proctor` |
| Proctoring WebSocket returns 200/404 | Upgrade forwarded as plain HTTP — `ProxyPass` beat `RewriteRule [P]` | Redo Step 12; the `ws://` entries must precede the general `ProxyPass` |
| Proctoring video never connects | `mod_proxy_wstunnel` missing | Redo Step 2.1, then `/scripts/restartsrv_httpd` |
| Proctoring drops with 4004 | Socket reached a process without the session | Confirm `ROLE=proctor` is in effect (Step 11) |
| API restarts every few seconds | Port 8000 taken by another service | `ss -ltnp \| grep :8000`; remove `PORT` from `.env` |
| Code judge returns PENDING | Docker down or images not built | `systemctl status docker`, redo Step 9 |
| AI features fail | Missing/invalid `GEMINI_API_KEY` | Check the key in `.env`, restart `hireflow-api` |
| Site loads, API 404s | Apache config not applied | Redo Step 12 |
| SPA routes 404 on refresh | `.htaccess` missing | Redo the `cp ... .htaccess` line in Step 13 |

**Read the logs** — they usually name the problem directly:

```bash
journalctl -u hireflow-api -n 50 --no-pager
journalctl -u hireflow-proctor -n 50 --no-pager
```

**Restart a service after changing `.env`:**

```bash
systemctl restart hireflow-api hireflow-proctor
```

---

## Tuning after real load

**Where a setting lives decides how you change it.** systemd applies
`EnvironmentFile=` *after* `Environment=`, so **`.env` overrides the `.service`
file** — regardless of the order the directives appear in the unit. Verify what
actually took effect rather than trusting either file:

```bash
tr ' ' '
' < /proc/$(systemctl show hireflow-proctor -p MainPID --value)/environ
```

| Setting | Default | Edit in | Change when |
|---|---|---|---|
| `PROCTOR_INFERENCE_SLOTS` | 6 | **`.env`** | `waits` keeps climbing. Each slot uses ~115 MB and does **not** grow with candidate numbers |
| `PROCTOR_MAX_SESSIONS` | 9 | **`.env`** | Candidates see "at capacity" while `peak_in_use` stays below `capacity` |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 5 | **`.env`** | `QueuePool limit` errors appear in the logs |
| `WEB_CONCURRENCY` (api) | 3 | `hireflow-api.service` — **never `.env`**, which would also hit the proctor | The site feels slow under load and memory allows |
| proctor worker count | **1** | — hardcoded in `entrypoint.sh` | **Never.** Correctness constraint; raise `PROCTOR_INFERENCE_SLOTS` instead |
| `MemoryMax` / `CPUWeight` | see units | the `.service` files | Another account is being starved, or ours is being killed |

After editing **`.env`**:

```bash
systemctl restart hireflow-proctor
```

After editing a **`.service`** file:

```bash
systemctl daemon-reload && systemctl restart hireflow-proctor
```

Forgetting `daemon-reload` is the usual reason a `.service` edit seems to have
no effect.

Expected capacity is roughly **8–10 simultaneous proctored candidates**. That is
an estimate based on processing cost, not a measurement on this hardware — test 8
is what replaces it with a real number.

---

## If something breaks

Each part can be switched off without taking down the rest.

| Problem | Action | Still works |
|---|---|---|
| Proctoring unstable | `systemctl stop hireflow-proctor` | Tests, CV scoring, code judge |
| Code judge unstable | `systemctl stop hireflow-judge-worker` | Everything else |
| Email queue stuck | Set `EMAIL_SYNC=1` in `.env`, restart | Email sends immediately instead of queued |
| Server short on memory | `pip uninstall sentence-transformers` | CV scoring falls back to a lighter method |

---

## After deployment

- [ ] **Rotate the old Gmail app password.** It still exists in the previous
      repository's history, and anyone with access to that repository can read it.
- [ ] **Back up `/opt/hireflow/backend/.env`** to a password manager. It holds the
      only copy of `SECRET_KEY` and `EMAIL_ENCRYPTION_KEY`, and is not in Git.
- [ ] Schedule database backups (`BACKUP_DIR` defaults to `/opt/hireflow/backups`).
- [ ] Only after several clean days: shut down the old GCP services.

### Useful commands

```bash
# Status of everything
systemctl status hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker --no-pager

# Live logs
journalctl -u hireflow-api -f

# Restart after a config change
systemctl restart hireflow-api hireflow-proctor

# Deploy updated code -> follow DEPLOY_UPDATE.md, not a snippet from here
```

### Deploying a code update

**→ [DEPLOY_UPDATE.md](DEPLOY_UPDATE.md)**

There used to be a four-line recipe here. It was wrong in three ways — `npm ci`
fails on this repo's lockfile, and it never re-copied the systemd units or the
Apache config, both of which `git pull` leaves stale. Worse, it ended at
`alembic upgrade head` with nothing to confirm the schema actually matched the
code, which is precisely the gap that took out the HR dashboard after one release.

The separate document is ordered, says which steps are conditional on what
changed, and ends in a verification you can fail.
