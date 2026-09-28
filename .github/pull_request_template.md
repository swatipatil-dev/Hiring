## What changed, and why

<!-- The problem, not just the diff. A reviewer should understand the "why" without
     reading the code first. -->

## How it was verified

<!-- What you actually ran or clicked, not what you assume works. -->

---

## Checks CI cannot do for you

Tick what applies. Leave the rest — an untouched box is information too.

- [ ] **Model changed → migration added.** The `migrations` job runs `alembic upgrade head`
      and `schema_diff.py` against a real MySQL, so it should catch a miss — but a schema
      change that ships without one takes out every request that touches the table.

- [ ] **Proctoring or the coding judge touched → verified by hand.** These have *no*
      meaningful CI coverage: `requirements-ci.txt` omits `opencv-python`, `mediapipe` and
      `ultralytics`, so those tests **skip rather than run**, and a green suite says nothing
      about them.

- [ ] **`deploy/cpanel/*` touched.** systemd units and `hireflow.conf` are not updated by
      `git pull`. The deploy workflow copies them only when it sees them in the diff — check
      the deploy log actually did.

- [ ] **New environment variable → added to `.env.cpanel.example` and documented.** Adding it
      only to `.env.example` means the production template never learns about it. Never add
      `PORT` or `WEB_CONCURRENCY` to `.env`: systemd applies `EnvironmentFile=` *after*
      `Environment=`, so `.env` silently overrides the unit files — that caused a real
      outage.

- [ ] **Email path touched → sent a real one.** `_send` reports failure by return value
      rather than raising, so "no exception" is not evidence anything was delivered.

## Target branch

- [ ] Correct for the stage — `develop` for integration, `testing` for pre-release, `main`
      only for what is ready to go live. **Merging to `main` deploys to production by
      itself** as soon as CI goes green: no button, no reviewer. CI is the only gate.

<!-- Merging to main deploys to production automatically once CI is green. See docs/CICD_SETUP.md. -->
