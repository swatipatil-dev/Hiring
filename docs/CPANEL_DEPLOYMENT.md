# HireFlow — cPanel / AlmaLinux 9 deployment

Target: WHM box at `66.116.207.71`, account `innomaxskills`, served at
`https://hire.innomaxskills.com`. 8 vCPU (AMD EPYC, KVM), 32 GB RAM, AlmaLinux 9.

Everything in this document assumes **root via SSH**.

---

## Architecture

```
hire.innomaxskills.com  (Apache vhost + AutoSSL)
  /                         → /home/innomaxskills/public_html/hire/   (React SPA, static)
  /api/ /interviews/ /mcq/  → 127.0.0.1:8000   hireflow-api      (3 workers)
  /api/v1/proctor/ /proctor → 127.0.0.1:8001   hireflow-proctor  (1 worker, + WebSocket)
  /logos/ /uploads/ /resumes/ /cover_letters/ → 127.0.0.1:8000

/opt/hireflow            owned by `hireflow`   — code, venv, uploads, HF cache
/srv/judge_work          owned by `hfjudge`    — judge scratch
innomaxskills_hireflow   MySQL 8.0
127.0.0.1:6379           Redis
docker                   hireflow-judge-{python,cpp,node,java}
```

**Why the API and proctoring are separate services.** Proctoring keeps active
sessions in a process-local dict of live MediaPipe handles
(`proctoring/routes.py`). That state cannot be serialised to Redis or shared
between workers, so a session's WebSocket must reach the same process that
created it — which is only guaranteed with **one** worker. The API has no such
constraint and runs three. This is a correctness requirement, not tuning.

---

## 1. Server preparation

```bash
# EasyApache — mod_proxy_wstunnel is NOT in the default profile and proctoring
# will not work without it.
yum install -y ea-apache24-mod_proxy ea-apache24-mod_proxy_http \
               ea-apache24-mod_proxy_wstunnel

# Python 3.11 (AppStream — no source build needed on AlmaLinux 9)
dnf install -y python3.11 python3.11-devel

# Native libs. Without mesa-libGL/glib2, `import cv2` fails with
# "libGL.so.1: cannot open shared object file" — the classic minimal-RHEL failure.
dnf install -y epel-release
dnf install -y mesa-libGL mesa-libEGL glib2 libsndfile tesseract gcc gcc-c++ make

# Redis
dnf install -y redis
sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
systemctl enable --now redis

# Docker CE (for the coding judge). AlmaLinux 9 defaults to cgroup v2, which the
# judge sandbox supports natively — no GRUB change and no reboot required.
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io
systemctl enable --now docker
```

**MySQL 8.0, not MariaDB.** Set this in WHM → MySQL/MariaDB Upgrade before
creating anything. Cloud SQL was MySQL 8.0; MariaDB differs in JSON handling and
would need a schema-compatibility audit that switching avoids entirely.

Then in cPanel create the database and user (both carry the account prefix):
`innomaxskills_hireflow` / `innomaxskills_hf`.

**Users and directories:**

```bash
useradd -r -m -d /opt/hireflow -s /bin/bash hireflow
useradd -r -s /sbin/nologin -G docker hfjudge
mkdir -p /srv/judge_work && chown hfjudge:hfjudge /srv/judge_work
```

> **Security note, stated plainly:** the `docker` group is root-equivalent — a
> member can bind-mount the host filesystem into a container. This box hosts four
> other cPanel accounts. Confining that to `hfjudge`, which runs exactly one
> process, is meaningfully safer than granting it to the web user. If that is
> still unacceptable, run Judge0 on a separate machine and set
> `JUDGE_BACKEND=judge0` with `JUDGE0_URL`.

Finally, create the `hire.innomaxskills.com` subdomain in cPanel with document
root `/home/innomaxskills/public_html/hire`, then run AutoSSL. **Confirm the
certificate covers the subdomain before testing proctoring** — `wss://` fails
closed on an invalid cert.

---

## 2. Application install

```bash
git clone <repo> /opt/hireflow && chown -R hireflow:hireflow /opt/hireflow
sudo -u hireflow python3.11 -m venv /opt/hireflow/venv
sudo -u hireflow /opt/hireflow/venv/bin/pip install -U pip wheel
sudo -u hireflow /opt/hireflow/venv/bin/pip install -r /opt/hireflow/backend/requirements-cpanel.txt
```

Use `requirements-cpanel.txt`, not `requirements.txt`. The former pulls CPU-only
torch (the plain one drags in ~2 GB of unusable CUDA wheels), drops packages with
no import sites, and adds `dnspython`, which the code needs but `requirements.txt`
never declared.

**Environment file:**

```bash
sudo -u hireflow cp /opt/hireflow/backend/.env.cpanel.example /opt/hireflow/backend/.env
sudo -u hireflow chmod 600 /opt/hireflow/backend/.env
# generate a real signing key — the app refuses to start with a placeholder
/opt/hireflow/venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Fill in `SECRET_KEY`, `GEMINI_API_KEY` (from https://aistudio.google.com/apikey),
`DATABASE_URL`, SMTP settings, and `EMAIL_ENCRYPTION_KEY`.

> **`EMAIL_ENCRYPTION_KEY` must be carried over verbatim** from the old
> deployment if you are migrating data. It decrypts the per-mailbox SMTP
> passwords stored in the rows you import. Migrate without it and every
> configured mailbox becomes permanently undecryptable.

**Prefetch models** — otherwise the first resume upload triggers a HuggingFace
download on the request path:

```bash
sudo -u hireflow HF_HOME=/opt/hireflow/hf-cache \
  /opt/hireflow/venv/bin/python /opt/hireflow/backend/scripts/prefetch_models.py
```

Exits non-zero if a required model is missing. Expect
`engines={'landmarker': True, 'sface': True, 'pose': True, 'yolo': True}`.

**Build the judge sandboxes:**

```bash
bash /opt/hireflow/backend/scripts/build_judge_images.sh
docker images | grep hireflow-judge   # expect python, cpp, node, java
```

---

## 3. Database

```bash
# Schema — a single head (x4n5o6p7q8r9), so this applies cleanly.
cd /opt/hireflow/backend
sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
```

Do not rely on `init_db()`'s `create_all()` for schema changes — it adds missing
tables but silently ignores column and constraint changes, so a half-applied
schema looks healthy and fails at runtime.

**Migrating existing data** (skip for a fresh install):

```bash
# 1. Rows. Strip CREATE DATABASE / USE from the dump — the target schema is
#    innomaxskills_hireflow, not recruitment_db.
mysql innomaxskills_hireflow < dump.sql

# 2. Files. Bucket object keys (resumes/, logos/, uploads/, cover_letters/) map
#    directly onto the local layout, so this lands them where the app looks.
gsutil -m rsync -r gs://<bucket>/ /opt/hireflow/backend/
chown -R hireflow:hireflow /opt/hireflow/backend/{resumes,logos,uploads,cover_letters}
```

---

## 4. Services

```bash
cp /opt/hireflow/deploy/cpanel/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hireflow-api hireflow-proctor \
                       hireflow-judge-worker hireflow-email-worker
systemctl status hireflow-api hireflow-proctor --no-pager
```

`hireflow-proctor` takes a while on first start — it imports mediapipe,
ultralytics and torch (~102 s measured) before building its first model set.
`TimeoutStartSec=600` accounts for that.

---

## 5. Apache

```bash
mkdir -p /etc/apache2/conf.d/userdata/{std,ssl}/2_4/innomaxskills/hire.innomaxskills.com
for m in std ssl; do
  cp /opt/hireflow/deploy/cpanel/hireflow.conf \
     /etc/apache2/conf.d/userdata/$m/2_4/innomaxskills/hire.innomaxskills.com/
done
/scripts/ensure_vhost_includes --user=innomaxskills
/scripts/restartsrv_httpd

# If SELinux is enforcing, Apache cannot reach the local ports until:
setsebool -P httpd_can_network_connect 1
```

Both `std` and `ssl` matter — cPanel regenerates `httpd.conf` on rebuild, and
anything edited there directly is lost.

---

## 6. Frontend

```bash
cd /opt/hireflow/frontend && npm ci && npm run build
rsync -a --delete dist/ /home/innomaxskills/public_html/hire/
cp /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/hire/.htaccess
chown -R innomaxskills:innomaxskills /home/innomaxskills/public_html/hire
```

**No frontend source changes are needed.** `src/api/client.js` uses
`baseURL: '/api/v1'` and `OnlineTest.jsx` derives WebSocket URLs from
`window.location`, so the same-origin layout works as-is.

---

## 7. Verification

Run in order — each step depends on the previous.

1. **Services up** — `systemctl status hireflow-*`, then
   `curl -s localhost:8000/health`. `table_count` must be non-zero (this is the
   direct test that the schema-name handling works with a cPanel-prefixed DB).
2. **Through Apache** — `curl -sI https://hire.innomaxskills.com/health`, then load
   the SPA and check devtools for 404s on `/assets/*`.
3. **Auth** — register, receive the OTP (exercises SMTP + the email worker +
   `EMAIL_ENCRYPTION_KEY`), log in.
4. **Migrated files** — open a candidate whose resume came from the bucket and
   confirm the PDF renders at `/resumes/<uuid>.pdf`.
5. **ATS** — upload a resume; logs should say
   `NLP: sentence-transformers loaded`, not the TF-IDF fallback warning.
6. **Gemini** — generate interview questions. Failure surfaces as a clear
   exception naming the missing credential or package.
7. **Judge** — Run Code in each of Python, C++, JavaScript, Java **and SQL**.
   Watch `docker ps` during a run. SQL is worth testing explicitly: it is
   unsupported on the Judge0 backend but works on this one.
8. **Proctoring** — start a test with camera and mic. Both WebSockets should
   reach status 101 at `wss://hire.innomaxskills.com/api/v1/proctor/ws/...`.
   This single test validates `mod_proxy_wstunnel`, the rewrite rules and AutoSSL
   together, and is the step most likely to need iteration.
9. **Capacity** — `curl -s https://hire.innomaxskills.com/api/v1/proctor/warmup`
   returns `capacity`, `in_use`, `peak_in_use`, `waits`, `active_sessions`.
   Under real load, a climbing `waits` means frames are queueing for an inference
   slot and you are at the box's ceiling.

---

## Tuning

| Variable | Default | Meaning |
|---|---|---|
| `PROCTOR_INFERENCE_SLOTS` | `cpu_count - 2` | Frames in inference at once. Resident model memory ≈ slots × ~115 MB and does **not** grow with candidate count (~45 KB per extra session). |
| `PROCTOR_MAX_SESSIONS` | `slots × 1.5` | Concurrent sessions before `/sessions/start` returns 503. |
| `WEB_CONCURRENCY` | 3 (api) / **1** (proctor) | Never raise the proctor value — see the architecture note. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 5 / 5 | Per worker. The upstream defaults (20+30) would be 50 connections per worker. |
| `DB_BOOTSTRAP` | `0` | Must stay 0 — the bootstrap path needs root DDL you do not have. |

Estimated capacity on 8 vCPU is **8–10 concurrent proctored candidates**. That is
reasoned from per-frame cost, not measured on this hardware — calibrate against
step 9.

---

## Rollback

Services are independent: `systemctl stop hireflow-proctor` disables proctoring
while leaving tests, ATS and the judge running. `JUDGE_BACKEND=judge0` moves code
execution to an external Judge0 if the local Docker sandbox is a problem. Both
degrade rather than fail.
