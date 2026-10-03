# Backup and restore

A nightly timer backs up the database **and every uploaded file** into one encrypted
bundle, copies it off the server, and keeps about five weeks of history. A monthly drill
restores a bundle into a scratch database and proves it is complete. The drill is the
only thing that shows the backups work, so don't skip it.

| Piece | Where |
|---|---|
| Backup script | `backend/scripts/backup.py` |
| Restore + checks | `backend/scripts/restore_drill.py` |
| Schedule | `deploy/cpanel/hireflow-backup.service` + `.timer` (02:30 nightly) |
| Tests of the scripts' logic | `backend/test/test_backup_scripts.py` |

Before this existed, the only dumps were the ones the deploy pipeline takes when a
release carries a migration (`/root/predeploy-*.sql`, same disk, no retention). No
resume, cover letter, logo or proctoring frame was backed up at all.

---

## What a backup contains

One file per night, `hireflow-<UTC stamp>.tar.gpg`, plus `<file>.sha256` next to it:

| Inside | What |
|---|---|
| `db.sql.gz` | `mysqldump --single-transaction --routines --triggers --no-tablespaces` of the `DATABASE_URL` database. A consistent snapshot; nothing is locked. |
| `files.tar.gz` | `resumes/ uploads/ cover_letters/ logos/ evidence_frames/` under `HIREFLOW_DATA_DIR`, stored under their storage keys (the paths the DB references). |
| `files.list` | Every key in `files.tar.gz`. |
| `manifest.json` | Exact `COUNT(*)` per table (in one consistent snapshot), `alembic_version`, database name, file count and bytes. |
| `SHA256SUMS` | Checksums of the four files above. |

The database name comes from `DATABASE_URL`, never hardcoded, because cPanel prefixes it
with the account name. Credentials go to `mysqldump` through a 0600 option file, never
the command line, where every user on the shared box could read them with `ps`.

`STORAGE_BACKEND=gcs`: the files live in the bucket, not on disk, so `files.tar.gz` is
empty. Turn on object versioning on the bucket; that is the file backup there.

## Setup (once, on the server)

`[root]` unless noted.

**1. Choose the encryption.** Prefer a public key: the server can then encrypt but never
decrypt, so a stolen disk or a compromised account doesn't expose the backups. Keep the
private key **off the server** (password manager, or a hardware key).

```bash
# On your own machine: make a key just for backups, export the public half.
gpg --quick-generate-key "HireFlow backups <ops@innomaxskills.com>" rsa4096 encr never
gpg --armor --export ops@innomaxskills.com > hireflow-backup-pub.asc
gpg --armor --export-secret-keys ops@innomaxskills.com > hireflow-backup-SECRET.asc  # store safely, NOT on the server

# On the server: import the public half for the hireflow user.
install -d -m 700 -o hireflow -g hireflow /opt/hireflow/.gnupg
sudo -u hireflow GNUPGHOME=/opt/hireflow/.gnupg gpg --import hireflow-backup-pub.asc
```

The alternative is a passphrase: `openssl rand -base64 32 > /opt/hireflow/.backup-passphrase`,
`chown hireflow: … && chmod 600 …`, and **also store the passphrase in the password
manager**. A backup encrypted with a passphrase that only existed on the dead server
cannot be restored.

**2. Choose where the off-site copy goes.** Anything `rclone`, `gsutil` or `aws` can write
to. Configure the tool as the `hireflow` user (its config must live under `/opt/hireflow`,
because the unit sets `ProtectHome=true`). Set a lifecycle rule on the bucket to delete
objects after ~40 days; the script prunes only the local copies.

**3. Add to `/opt/hireflow/backend/.env`:**

```ini
BACKUP_DIR=/opt/hireflow/backups
BACKUP_GPG_RECIPIENT=ops@innomaxskills.com           # or: BACKUP_PASSPHRASE_FILE=/opt/hireflow/.backup-passphrase
BACKUP_OFFSITE=rclone:offsite:hireflow-backups        # or gs://bucket/path, s3://bucket/path
# RCLONE_CONFIG=/opt/hireflow/.config/rclone/rclone.conf   # if using rclone
```

**4. Install and take the first backup:**

```bash
\cp -f /opt/hireflow/deploy/cpanel/hireflow-backup.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now hireflow-backup.timer
systemctl start hireflow-backup.service
journalctl -u hireflow-backup.service -n 20 --no-pager
ls -lh /opt/hireflow/backups/
```

The last log line must be `wrote … (… rows, … files)` followed by `copied off-site to …`.

**5. Run the drill below once now.** The first backup counts only after it has been
restored.

## Behaviour worth knowing

- **It fails loudly.** No encryption configured → refuses to run. Off-site configured but
  the copy fails → the run fails. A dump without mysqldump's completion trailer → fails.
  A failed run shows as `failed` in `systemctl status hireflow-backup` and leaves no
  partial bundle behind.
- **No off-site configured** → the run succeeds but logs `WARNING: … exists only on this
  server` every night.
- **Retention** is local only: the newest 7 bundles, plus the newest bundle of each of the
  4 ISO weeks before them. Files that aren't bundles are never touched.
- `LAST_SUCCESS` in `BACKUP_DIR` holds the stamp of the last good run. Monitor it: older
  than 26 hours means backups have stopped.
- `.env` is **not** in the bundle (it holds the keys that decrypt the mailbox passwords
  and sign sessions). Keep it in the password manager, as CPANEL_DEPLOYMENT.md says.

## Recovery objectives

| | Target | What it rests on |
|---|---|---|
| **RPO** (data you can lose) | **24 hours** | One bundle a night at 02:30. Anything written since the last bundle is lost. `LAST_SUCCESS` older than 26 h means the RPO is already broken. |
| **RTO** (time to be back up) | **2 hours** | Fetch the off-site bundle, then the steps below. A restore of the drill-sized database (65 tables, 846 files) takes under a minute; the rest is people and downloads, so rehearse it in the monthly drill. |

A tighter RPO needs MySQL binary logging (point-in-time recovery), which cPanel shared
hosting does not offer; the nightly bundle is the floor, not a target to relax.

## Restoring for real

Don't restore over the live database first. Restore into a scratch database with the drill
(below) using `--keep`, check it, then:

```bash
# 1. Stop everything that writes.
systemctl stop hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
# 2. Decrypt and unpack (private key or passphrase needed — bring it to the server
#    temporarily, and remove it afterwards).
gpg --decrypt hireflow-<stamp>.tar.gpg > b.tar && tar xf b.tar && cd hireflow-<stamp> && sha256sum -c SHA256SUMS
# 3. Load the dump into the live database (take a dump of the current state first).
gunzip -c db.sql.gz | mysql -u <db user> -p <live db>
# 4. Put the files back under HIREFLOW_DATA_DIR.
tar xzf files.tar.gz -C /opt/hireflow/backend && chown -R hireflow: /opt/hireflow/backend/{resumes,uploads,cover_letters,logos,evidence_frames}
# 5. Bring the schema up to the code, verify, start.
cd /opt/hireflow/backend && sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/schema_diff.py
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/verify_tenant_isolation.py
systemctl start hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

## The monthly restore drill

`restore_drill.py` restores one bundle into a scratch database and a scratch directory,
then checks it. It **refuses** to restore into the `DATABASE_URL` database, into any name in
`RESTORE_PROTECTED_DBS` (comma-separated), or into a name that doesn't contain `drill` or
`restore`.

On cPanel, create the scratch database once in **cPanel → MySQL Databases** (e.g.
`innomaxskills_hireflow_drill`) and give the app's database user ALL on it. The drill
empties it before each run, and again afterwards unless you pass `--keep`.

**Checklist, first working day of each month:**

- [ ] Pick last night's bundle **from the off-site copy**, not the local one. Restoring
      the off-site copy is the point.
- [ ] Put the decryption key where the drill can use it: import the private key for the
      run (`gpg --import`, then `gpg --delete-secret-keys` afterwards), or set
      `BACKUP_PASSPHRASE_FILE`.
- [ ] Leave `--files-dir` unset (a temp dir) or point it at a new, empty folder. The
      drill refuses the live `HIREFLOW_DATA_DIR`, anything inside it or containing it,
      and any folder that already holds files.
- [ ] Run:
      ```bash
      cd /opt/hireflow/backend
      sudo -u hireflow GNUPGHOME=/opt/hireflow/.gnupg /opt/hireflow/venv/bin/python \
        scripts/restore_drill.py /path/to/hireflow-<stamp>.tar.gpg \
        --target-db innomaxskills_hireflow_drill --report /opt/hireflow/backups/drill-$(date +%F).json
      ```
- [ ] Every line reads `PASS` and the last one reads `RESULT: PASS`:
  | Check | Fails when |
  |---|---|
  | `outer_checksum`, `inner_checksums` | The copy is corrupted |
  | `row_counts` | Any table's row count differs from the manifest (exact by default; `--count-tolerance 1` allows 1%) |
  | `alembic` | The restore's migration version isn't the code's head. Expected if the backup predates a release: run `alembic upgrade head` on the scratch DB and re-check |
  | `tenant_isolation` | `verify_tenant_isolation.py` finds cross-company drift in the restored data |
  | `files` | A sampled DB file reference is missing from the restore but was in `files.list` (the backup lost it), or the restored file count differs from the manifest. References missing from **both** are listed as `dangling_in_production`: already broken in production, not a backup fault. |
- [ ] Remove the private key or passphrase you brought in.
- [ ] Record the date, the bundle name and the result (keep the `--report` JSON).
- [ ] Anything not `PASS`: treat it as an incident. The backups aren't working.

### Drills that were run

**2026-09-30**, development machine (Windows, MySQL 8.0.45, Git's GnuPG 2.4.9), against
the real development database (`recruitment_db`, 65 tables, 2,428 rows, 846 files):

- `backup.py`: encrypted bundle (gpg symmetric AES256), 0.5 MB.
- `restore_drill.py` into `recruitment_restore_drill`:
  - `outer_checksum`, `inner_checksums`, `row_counts` (65 tables, 2,428 rows exact) and
    `files` (846 of 846, none broken; 91 references sampled) all `PASS`.
  - `alembic`: `FAIL`, as the table above predicts. The dev database was still at
    `j6z7a8b9c0d1`. `alembic upgrade head` on the restored copy then ran all four newer
    migrations cleanly to `r1w2f3l4o5w6`, the same path "Restoring for real" takes.
  - `tenant_isolation`: `FAIL`, but not a backup fault. The live dev database gives the
    identical result (12 mismatched rows, 1 NULL), so the restore reproduced the data
    faithfully, drift included. That drift is fixed with the backfill scripts the check
    names.
- The encrypted bundle and passphrase were deleted afterwards.

Since then `test/test_backup_restore_roundtrip.py` runs a real mysqldump → restore on
every CI run: scratch databases, non-ASCII text and binary blobs compared byte for byte,
files compared byte for byte. It is unencrypted, so the gpg step stays in the monthly
drill.

**2026-09-29**

On a development machine (MySQL 8.0.45, GnuPG 2.4.9), against a seeded
database of 58 tables in two companies, with 7 files and one deliberately dangling
resume reference:

- `backup.py`: 58 tables, 17 rows, 7 files, encrypted with gpg symmetric AES256, 1.2 s.
  The bundle contained no plaintext (`gpg --list-packets` shows AES256 data; `grep` for a
  seeded company name found nothing).
- `restore_drill.py` into `recruitment_restore_drill`: all six checks `PASS`. The
  dangling reference was reported as `dangling_in_production`, not as a broken backup.
- It refused, with no side effects: the live database as the target, a name without
  `drill`/`restore`, a bundle with one flipped byte (outer checksum), and the wrong
  passphrase. `backup.py` refused to run with no encryption configured.
