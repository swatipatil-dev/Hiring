# HireFlow — cPanel / AlmaLinux 9 deployment guide

Step-by-step, including where to click and where to paste. Follow top to bottom.

**Target:** `hire.innomaxskills.com`
**Server:** `66.116.207.71` — AlmaLinux 9, 8 vCPU, 32 GB RAM
**cPanel account:** `innomaxskills`

Each step lists a **Check:** line. If it doesn't match, stop and fix it before
moving on — later steps assume the earlier ones worked.

> **Notation.** `[root]` = paste into the root terminal (Step 0.2).
> `[cPanel]` / `[WHM]` = do it in the browser.

---

## Contents

- [Step 0 — Get logged in](#step-0--get-logged-in)
- [Step 1 — Collect these before you start](#step-1--collect-these-before-you-start)
- [Step 2 — Install server software](#step-2--install-server-software)
- [Step 3 — MySQL 8.0 and the database](#step-3--mysql-80-and-the-database)
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

---

## How the finished system fits together

```
Browser → https://hire.innomaxskills.com
             │
             ├─ /                    → /home/innomaxskills/public_html/hire/   (React files)
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

**Decide now: fresh install, or migrating existing data?** Steps 10.2 and 10.3
are for migration only — skip them for a fresh install.

---

## Step 2 — Install server software

### 2.1 Apache WebSocket module

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

### 2.2 Python 3.11

`[root]`

```bash
dnf install -y python3.11 python3.11-devel
```

**Check:** `python3.11 --version` prints `Python 3.11.x`.

> Do **not** use cPanel's "Setup Python App" for this. That system cannot handle
> the live video connections proctoring needs.

### 2.3 Graphics and OCR libraries

`[root]`

```bash
dnf install -y epel-release
dnf install -y mesa-libGL mesa-libEGL glib2 libsndfile tesseract gcc gcc-c++ make
```

**Check:** `tesseract --version` prints a version number.

These are required by the AI vision libraries. Skip them and proctoring fails
later with `libGL.so.1: cannot open shared object file`.

### 2.4 Redis

`[root]`

```bash
dnf install -y redis
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
systemctl enable --now redis
```

**Check:** `redis-cli ping` replies `PONG`.

### 2.5 Docker

`[root]`

```bash
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io
systemctl enable --now docker
```

**Check:** `docker run --rm hello-world` prints "Hello from Docker!".

Docker runs candidate code safely isolated. No reboot is needed.

---

## Step 3 — MySQL 8.0 and the database

### 3.1 Switch to MySQL 8.0

Do this **before** creating any database.

1. `[WHM]` Search box → type `MySQL`
2. Click **MySQL/MariaDB Upgrade**
3. Select **MySQL 8.0**
4. Follow the prompts and let it finish

**Check:** `[root]` `mysql --version` shows `8.0` and **not** `MariaDB`.

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
3. **Domain:** `hire.innomaxskills.com`
4. **Untick** "Share document root"
5. **Document Root:** `/home/innomaxskills/public_html/hire`
6. **Submit**

<details>
<summary>Older cPanel versions</summary>

Search for **Subdomains** instead. Subdomain `hire`, Domain `innomaxskills.com`,
Document Root `public_html/hire`, then **Create**.
</details>

**Check:** `[root]` `ls -d /home/innomaxskills/public_html/hire` exists.

### 5.2 Issue the SSL certificate

1. `[cPanel]` Search box → `SSL`
2. Click **SSL/TLS Status**
3. Tick `hire.innomaxskills.com`
4. Click **Run AutoSSL**
5. Wait for a green padlock next to the domain (can take a few minutes)

**Check:**

```bash
curl -sI https://hire.innomaxskills.com | head -1
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

**Check:**

```bash
/opt/hireflow/venv/bin/pip list | grep -c nvidia
```

Must print `0`.

> Use `requirements-cpanel.txt`, **never** `requirements.txt`. The plain file
> downloads a graphics-card version of PyTorch — about 2 GB of files this server
> cannot use — and includes a database driver that fails to build here and aborts
> the whole install.

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
| `EMAIL_ENCRYPTION_KEY=` | carried over — migration only |

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

`[root]`

```bash
cd /opt/hireflow/backend
sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
```

**Check:**

```bash
sudo -u hireflow /opt/hireflow/venv/bin/alembic current
```

Shows `x4n5o6p7q8r9 (head)`.

### 10.2 Import existing data — *migration only*

Skip for a fresh install.

Before importing, open the dump and delete any `CREATE DATABASE` or `USE` lines —
the target database is `innomaxskills_hireflow`, not the old name.

`[root]`

```bash
mysql -u innomaxskills_hf -p innomaxskills_hireflow < /root/dump.sql
mysql -u innomaxskills_hf -p innomaxskills_hireflow -e "SELECT COUNT(*) FROM users;"
```

**Check:** the user count looks right.

### 10.3 Import uploaded files — *migration only*

`[root]`

```bash
gsutil -m rsync -r gs://YOUR-BUCKET-NAME/ /opt/hireflow/backend/
chown -R hireflow:hireflow /opt/hireflow/backend/
```

**Check:** `ls /opt/hireflow/backend/resumes | wc -l` roughly matches the old
storage.

---

## Step 11 — Start the services

`[root]`

```bash
cp /opt/hireflow/deploy/cpanel/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

**Wait 2–3 minutes.** The proctoring service loads large AI libraries on first
start — this is normal and only happens once.

**Check:**

```bash
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

Four lines of `active`. Then:

```bash
curl -s localhost:8000/health
curl -s localhost:8001/health
```

Both return JSON containing `"status": "healthy"`, and **`table_count` must be
greater than 0**.

> `table_count: 0` means the app is connected to the wrong database. Re-check
> `DATABASE_URL` in Step 7.

If a service is not active:

```bash
journalctl -u hireflow-api -n 50 --no-pager
```

> ### Never increase `WEB_CONCURRENCY` in `hireflow-proctor.service`
>
> It must stay at **1**. Each live proctoring session is held in one process's
> memory, so with more workers a candidate's video connection reaches the wrong
> process and drops with error 4004. To handle more candidates, raise
> `PROCTOR_INFERENCE_SLOTS` instead (see [Tuning](#tuning-after-real-load)).

---

## Step 12 — Connect Apache

`[root]`

```bash
mkdir -p /etc/apache2/conf.d/userdata/{std,ssl}/2_4/innomaxskills/hire.innomaxskills.com
for m in std ssl; do
  cp /opt/hireflow/deploy/cpanel/hireflow.conf \
     /etc/apache2/conf.d/userdata/$m/2_4/innomaxskills/hire.innomaxskills.com/
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

**Check:**

```bash
curl -s https://hire.innomaxskills.com/health
```

Returns the same healthy JSON as Step 11.

---

## Step 13 — Build and upload the website

`[root]`

```bash
cd /opt/hireflow/frontend
npm ci
npm run build
rsync -a --delete dist/ /home/innomaxskills/public_html/hire/
cp /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/hire/.htaccess
chown -R innomaxskills:innomaxskills /home/innomaxskills/public_html/hire
```

If `npm` is missing:

```bash
dnf install -y nodejs npm
```

**Check:** `ls /home/innomaxskills/public_html/hire` shows `index.html` and an
`assets` folder.

Open **https://hire.innomaxskills.com** — the login page should appear.

---

## Step 14 — Test everything

Work through these in order.

| # | Test | How | Expected |
|---|---|---|---|
| 1 | Website loads | Open `https://hire.innomaxskills.com` | Login page, no errors in browser console (`F12`) |
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
curl -s https://hire.innomaxskills.com/api/v1/proctor/warmup
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
> see connections with status **101**. A **4004** disconnect means
> `WEB_CONCURRENCY` is not `1` in `hireflow-proctor.service`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Service will not start | Missing/placeholder `SECRET_KEY` | Redo Step 7.1–7.3 |
| `table_count: 0` | Wrong database in `DATABASE_URL` | Check names include the `innomaxskills_` prefix |
| `libGL.so.1: cannot open...` | Graphics libraries missing | Redo Step 2.3, then `systemctl restart hireflow-proctor` |
| Proctoring video never connects | `mod_proxy_wstunnel` missing | Redo Step 2.1, then `/scripts/restartsrv_httpd` |
| Proctoring drops with 4004 | More than one proctor worker | `WEB_CONCURRENCY=1` in `hireflow-proctor.service` |
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

Edit `/opt/hireflow/backend/.env`, then
`systemctl restart hireflow-proctor`.

| Setting | Default | Change when |
|---|---|---|
| `PROCTOR_INFERENCE_SLOTS` | 6 | `waits` keeps climbing. Each slot uses ~115 MB and does **not** grow with candidate numbers |
| `PROCTOR_MAX_SESSIONS` | 9 | Candidates see "at capacity" while `peak_in_use` stays below `capacity` |
| `WEB_CONCURRENCY` (api) | 3 | The site feels slow under load and memory allows |
| `WEB_CONCURRENCY` (proctor) | **1** | **Never change this** |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 5 | `QueuePool limit` errors appear in the logs |

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

# Deploy updated code
cd /opt/hireflow && sudo -u hireflow git pull
sudo -u hireflow /opt/hireflow/venv/bin/pip install -r backend/requirements-cpanel.txt
cd backend && sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker

# Rebuild the website after a code update
cd /opt/hireflow/frontend && npm ci && npm run build
rsync -a --delete dist/ /home/innomaxskills/public_html/hire/
cp /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/hire/.htaccess
chown -R innomaxskills:innomaxskills /home/innomaxskills/public_html/hire
```
