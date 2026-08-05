# Tenant Custom Domains

White-label HireFlow so each tenant company can serve its careers page, candidate
portal, logins, and dashboards from **its own domain** — e.g. `careers.acmecorp.com`
— instead of `hire.innomaxskills.com/jobs?key=<api_key>`.

> **Status legend used throughout this doc**
> - ✅ **Built** — already in the codebase.
> - 🚧 **Planned** — described in the implementation plan, not yet merged.
>
> Much of the *backend* is ✅ Built. The *frontend host-awareness* and the *GCP
> infrastructure + cert automation* are 🚧 Planned. Each section marks its state.

---

## 1. Why this exists

A tenant's public hiring presence should look like it belongs to *them*: their domain,
their logo, their colours — not a shared platform URL with an API key in the query
string. Custom domains give tenants a fully branded, professional candidate experience
and let them advertise a clean URL (`careers.acmecorp.com`) on job posts and social.

Two things make this cheap to build here:

1. **The tenant model already carries domain + branding fields**, and a Host-header
   → Company resolver already exists in the backend.
2. **Auth is a Bearer JWT in `localStorage`, not cookies** — so there is no cross-domain
   cookie problem. Serving the same SPA on a second domain "just works" once the domain
   resolves to a tenant and has TLS.

---

## 2. Concepts & terminology

| Term | Meaning |
|---|---|
| **Tenant / Company** | A customer org. Root record: `companies` table (`Company` model). Everything else hangs off `company_id`. |
| **Platform host** | Our own domain(s): `hire.innomaxskills.com` (prod), `localhost` (dev). The platform landing page and superadmin live here. |
| **Slug** | URL-safe company key (`Company.slug`, unique). Basis for an optional `slug.<base-domain>` subdomain model (not the chosen model, but supported by the resolver). |
| **API key** | `Company.api_key` (`sk_live_…`). Used by the legacy `?key=` iframe/embed flow. |
| **Custom domain** | A tenant's own hostname (`careers.acmecorp.com`) stored in `Company.custom_domain` once verified. This is the model we ship. |
| **Ingress** | The single GCP entry point (Application Load Balancer) that terminates TLS and forwards all tenant + platform traffic to the frontend service. |

---

## 3. Architecture

### 3.1 Request flow (target state)

```
                    ┌─────────────────────────────────────────────────────────┐
 Candidate visits   │  careers.acmecorp.com  (tenant's own DNS → CNAME →       │
 careers.acmecorp   │  ingress.hireflow.io)                                    │
        │           └─────────────────────────────────────────────────────────┘
        ▼
 ┌──────────────────────────┐   TLS terminated here (Google-managed cert,
 │  GCP Global External ALB │   auto-provisioned per tenant domain via
 │  + Certificate Manager   │   Certificate Manager DNS authorization)
 └──────────────────────────┘
        │  Host: careers.acmecorp.com  (preserved)
        ▼
 ┌──────────────────────────┐   Serves the React SPA build, and reverse-proxies
 │  hire-flow-web (nginx)    │   /api, /interviews, /mcq, /proctor, /logos…
 │                          │   to the backend. Adds:
 │                          │      X-Forwarded-Host: careers.acmecorp.com
 └──────────────────────────┘   (Host → backend's *.run.app for Cloud Run SNI)
        │
        ▼
 ┌──────────────────────────┐   TenantDomainMiddleware reads X-Forwarded-Host,
 │  hire-flow-api (FastAPI)  │   resolves it → Company, and stamps
 │                          │   request.state.company_id / slug / branding.
 └──────────────────────────┘
```

Key subtlety: **Cloud Run routes a service by its `*.run.app` Host/SNI**, so nginx must
keep `Host: <backend>.run.app` on the wire toward the backend. The tenant's real host is
carried alongside in **`X-Forwarded-Host`**, which the middleware reads.

### 3.2 The two resolution paths in the middleware

`backend/src/middleware/tenant_domain.py` resolves a host to a Company in this order:

1. **Exact custom-domain match** — `custom_domain == host AND domain_verified == True AND is_active == True`. → `careers.acmecorp.com`. **(the shipping model)**
2. **Slug subdomain** — if `host` ends with `.<HIREFLOW_BASE_DOMAIN>`, strip the base
   and look up `slug`. → `acme.hireflow.io`. *(supported but not the chosen product model.)*

No match → `request.state.company_id = None` and the request proceeds unchanged, so
platform pages and existing routes are unaffected.

---

## 4. Data model

`Company` (`backend/src/models/models.py`) — the domain-relevant columns:

| Column | Type | Purpose |
|---|---|---|
| `slug` | `String(100)` unique | URL-safe key / optional subdomain |
| `api_key` | `String(64)` unique | Legacy `?key=` embed flow |
| `custom_domain` | `String(255)` indexed | Verified vanity domain (live) |
| `domain_verified` | `Boolean` | Gate for exact-domain resolution |
| `domain_pending` | `String(255)` | Domain submitted, not yet verified |
| `domain_verify_token` | `String(64)` | DNS-TXT ownership token |
| `logo_url`, `favicon_url`, `primary_color`, `secondary_color`, `accent_color`, `font_family`, `tagline` | — | Per-tenant branding returned to the SPA |

> ⚠️ **Schema note:** the ORM declares `custom_domain`, but the idempotent provisioning
> script `backend/scripts/apply_tenancy_columns.py` historically added only
> `domain_verified/pending/token`. On an **existing** production DB the `custom_domain`
> column may be missing until the M0 migration (below) is applied. Per project policy,
> **Alembic migrations are applied manually before deploying the code that needs them.**

---

## 5. How it's implemented

### 5.1 Backend — tenant resolution ✅ Built (one M0 tweak 🚧)

- **Middleware** `backend/src/middleware/tenant_domain.py` (registered in `main.py`, before
  CORS) stamps `request.state.company_id / company_slug / company_branding` on every request.
  - 🚧 **M0 change:** `_bare_host()` must prefer `X-Forwarded-Host` over `Host`, because
    after the nginx hop the raw `Host` is the backend's `*.run.app` name:
    ```python
    def _bare_host(request):
        fwd = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        return fwd.split(",")[0].split(":")[0].lower().strip()
    ```
- **Request-scoped dependencies** `backend/src/dependencies/tenant.py`:
  `get_request_company()` / `require_request_company()` (raises 404 when no tenant resolves).
- **Query scoping** `backend/src/tenancy/*` — a `ContextVar` + SQLAlchemy `before_compile` /
  `before_flush` events auto-filter and auto-stamp `company_id` on every `TenantScoped` model.
  Bound in `backend/src/auth.py` (`get_current_user`), which **also enforces** that a logged-in
  user's `company_id` matches the tenant the host resolved to (else `403`) — this is the
  white-label security guard.

### 5.2 Backend — public careers API ✅ Built

`backend/routes/public_routes.py`:

- `GET /api/v1/public/careers` — active jobs for the **host-resolved** tenant (no `?key=`).
- `GET /api/v1/public/careers/{job_id}` — one job, validated against the tenant.
- Legacy embed (kept for third-party iframes): `GET /api/v1/public/jobs?key=<api_key>`.

Both careers and jobs return the same shape: `{ company: {…branding}, jobs: [...] }`.

### 5.3 Backend — domain lifecycle (superadmin) ✅ Built (message + hook 🚧)

`backend/routes/superadmin_routes.py`:

| Endpoint | Does |
|---|---|
| `POST /companies/{id}/custom-domain` | Store `domain_pending`, generate a TXT token, return the record to add: host `_hireflow.<domain>`, value `hireflow-verify=<token>`. |
| `POST /companies/{id}/custom-domain/verify` | Live DNS-TXT lookup (via `dnspython`); on success promote `domain_pending → custom_domain`, set `domain_verified=True`. |
| `DELETE /companies/{id}/custom-domain` | Remove the domain and reset the verification state. |

- 🚧 **M2 change:** the verify success message currently says *"point your DNS A/CNAME to
  this server's IP"* — **wrong for Cloud Run/ALB**. It must return the real targets: a traffic
  **CNAME `<domain> → ingress.hireflow.io`**, the **DNS-authorization CNAME**, and
  `cert_status: "provisioning"`.
- 🚧 **M2 change:** on verify success, call a new `provision_managed_cert(domain)` service
  (`backend/src/services/domain_provisioning.py`, new) that uses the
  `google-cloud-certificate-manager` Python client to create a DNS authorization + a
  single-domain Google-managed cert + a cert-map entry. Add
  `GET /companies/{id}/custom-domain/status` to poll until the cert is `ACTIVE`.

### 5.4 Frontend — host-awareness 🚧 Planned (M1)

The SPA already uses a **relative** API base (`frontend/src/api/client.js` → `/api/v1`), so
API calls are same-origin on any domain. The remaining work is making the *public entry pages*
resolve the tenant from the **hostname** instead of requiring `?key=`.

- **New helper** `frontend/src/utils/tenantHost.js` → `isTenantHost()`: true when
  `window.location.hostname` is not a platform host and there is no `?key=`.
- **`JobBoard.jsx`** (`/jobs`, plus a new `/careers` route): when on a tenant host with no key,
  load the keyless `GET /api/v1/public/careers`; response shape is identical so the render +
  branding code is unchanged. "Apply" / "Sign In" links drop `?key=` and use same-origin
  `/portal` and `/app`.
- **`App.jsx`**: host-aware redirects — on a tenant host with no key, `/` and `*` route to
  `/careers`; `ProtectedRoute` sends unauthenticated visitors to `/app` (keyless).
- **`CompanyPortal.jsx` (`/portal`)** and **`CompanyLogin.jsx` (`/app`)**: resolve the tenant
  by Host when no `?key=` (login already supports Host-based tenant resolution server-side via
  `backend/routes/auth_routes.py`).
- **Branding** on host-resolved pages comes from the `company` block of the `/careers` response;
  post-login pages already brand from JWT claims (`AuthContext.applyCompanyBranding`).

### 5.5 nginx — forward the tenant host 🚧 Planned (M0)

`frontend/nginx.conf.template` currently sets `proxy_set_header Host $proxy_host;` in every
proxy block (correct for Cloud Run routing). Add to **each** proxy block, keeping the existing
lines:

```nginx
proxy_set_header X-Forwarded-Host $host;
```

### 5.6 Infrastructure — GCP ingress + TLS 🚧 Planned (M2)

**One ingress for everything: a Global External Application Load Balancer + Certificate
Manager + a Serverless NEG → `hire-flow-web`.** Chosen over Cloud Run domain mappings because
mappings are preview/not-for-production and require per-domain Google Search Console ownership
verification; the ALB makes each tenant a clean two-CNAME onboarding with an auto-renewing cert.

One-time setup (full `gcloud` sequence lives in the plan file
`.claude/plans/as-the-project-system-silly-cosmos.md`, appendix):
static global IP → Serverless NEG → backend service + URL map → Certificate Manager map →
HTTPS proxy + forwarding rule → HTTP→HTTPS redirect. Migrate `hire.innomaxskills.com` onto the
LB and publish a stable `ingress.hireflow.io A <LB IP>` — that hostname is the CNAME target
handed to tenants (so the LB IP can change without tenants re-doing DNS).

**Cost:** ALB ~$18/mo flat + ~$0.01/GiB; Certificate Manager 100 free certs, then $0.20/cert/mo
(quota ~1000/project). For a handful of tenants this is effectively the flat LB cost.

---

## 6. How it's used

### 6.1 Operator (superadmin) — onboard a tenant domain

1. In the superadmin UI, open the tenant company and choose **Add custom domain**; enter
   `careers.acmecorp.com`.
   → API: `POST /api/v1/superadmin/companies/{id}/custom-domain`.
2. The UI shows the tenant the DNS records to add (see 6.2). Wait for the tenant to add them.
3. Click **Verify**. → `POST …/custom-domain/verify` runs the DNS-TXT check; on success it
   promotes the domain and (🚧 M2) kicks off cert provisioning.
4. Watch the domain status until **Active** (🚧 `GET …/custom-domain/status`, cert `ACTIVE`
   within minutes of the DNS-auth CNAME resolving; allow up to ~24h for first issuance).
5. Done — `https://careers.acmecorp.com` now serves that tenant's white-labeled app.

To retire a domain: **Remove** → `DELETE …/custom-domain`. The tenant falls back to the
platform URL (and, if used, the slug subdomain).

### 6.2 Tenant — DNS records to add

At their DNS provider, the tenant adds:

| Record | Type | Value | Purpose |
|---|---|---|---|
| `_hireflow.careers.acmecorp.com` | TXT | `hireflow-verify=<token>` | Ownership proof for our verify step |
| *(🚧 M2)* the DNS-auth host from `status` | CNAME | *(value Certificate Manager gives)* | Lets Google issue/renew the TLS cert |
| `careers.acmecorp.com` | CNAME | `ingress.hireflow.io` | Routes traffic to our load balancer |

> **Subdomains only** (`careers.…`, `jobs.…`). A **bare apex** (`acmecorp.com`) can't be a
> CNAME — that would need an A record to the LB IP (or an ALIAS/ANAME if their provider supports
> it). Steer apex requests to a subdomain.
>
> **"careers on our marketing site" (`www.acmecorp.com/careers`)** is *not* this feature — we
> can't serve a path on a domain we don't host. For that, the tenant either reverse-proxies
> `/careers` to us from their own web server, or embeds our widget
> (`/api/v1/public/jobs?key=…`) into their existing page.

### 6.3 End user (candidate) experience

1. Visits `https://careers.acmecorp.com` → the branded careers board (logo, colours, favicon,
   font all Acme's). No API key in the URL.
2. Clicks **Apply** → `/portal` on the same domain → registers/logs in. The backend resolves
   the tenant from the host, so the candidate is scoped to Acme automatically.
3. After login, the candidate dashboard, tests, and everything else stay on
   `careers.acmecorp.com`, fully branded.
4. HR / interviewer / admin for Acme can also log in at `careers.acmecorp.com/app` and land on
   their dashboards — a user who belongs to a *different* company is rejected with `403` on
   Acme's domain (enforced in `backend/src/auth.py`).

### 6.4 For developers — consuming tenant context in new code

When you add a route that must be tenant-aware, **do not parse the host yourself**. Use the
already-resolved context:

- **Public (unauthenticated) host-resolved route** — depend on the tenant:
  ```python
  from src.dependencies.tenant import require_request_company
  @router.get("/api/v1/public/something")
  async def handler(company = Depends(require_request_company), db = Depends(get_db)):
      # company is the tenant resolved from the request host (404 if none)
      ...
  ```
- **Authenticated route** — you usually need nothing. `get_current_user` binds the tenant
  `ContextVar`, so any query over a `TenantScoped` model is auto-filtered to the caller's
  company, and new rows are auto-stamped with `company_id`. Only add an explicit
  `.filter(Model.company_id == …)` for defense-in-depth on sensitive reads.
- **Cross-tenant work** (superadmin/back-office) — wrap in `tenant_bypass()` from
  `src/tenancy` to opt a query out of the auto-filter. Superadmin JWTs already bind `None`.
- **Reading branding** for emails/UI — `request.state.company_branding` is populated by the
  middleware (name, colours, logo, favicon, font, support email).

Rule of thumb: **the host is resolved once, at the edge; everything downstream reads
`request.state` / the `ContextVar`.** Never re-derive the tenant from headers in a handler.

---

## 7. API reference

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /api/v1/public/careers` | none (host-resolved) | Tenant's active jobs + branding |
| `GET /api/v1/public/careers/{job_id}` | none (host-resolved) | One job for the tenant |
| `GET /api/v1/public/jobs?key=<api_key>` | none (key) | Legacy embed job list |
| `GET /api/v1/public/company-info?key=<api_key>` | none (key) | Legacy embed branding |
| `POST /api/v1/superadmin/companies/{id}/custom-domain` | superadmin | Start domain setup; returns TXT record |
| `POST /api/v1/superadmin/companies/{id}/custom-domain/verify` | superadmin | Verify TXT; promote to live |
| `GET /api/v1/superadmin/companies/{id}/custom-domain/status` 🚧 | superadmin | Poll cert provisioning state |
| `DELETE /api/v1/superadmin/companies/{id}/custom-domain` | superadmin | Remove custom domain |

---

## 8. Configuration & environment

| Setting | Where | Notes |
|---|---|---|
| `HIREFLOW_BASE_DOMAIN` | env on `hire-flow-api` | Base for the *optional* slug-subdomain model (default `hireflow.io`). Set to the real base if that model is ever used. |
| `BACKEND_URL` | env on `hire-flow-web` (nginx) | Backend Cloud Run URL the SPA proxies to. |
| CORS | `backend/main.py` | `allow_origins=["*"]`, `allow_credentials=False` — fine because auth is a Bearer token (no cookies). Optionally scope `*` to `/api/v1/public/*`. |
| `dnspython` | `backend/requirements.txt` | Required for DNS-TXT verification. |

---

## 9. Security model

- **Tenant isolation** is enforced at three layers: the edge middleware resolves the host; the
  `ContextVar` auto-filter scopes every tenant query; and `get_current_user` rejects a JWT whose
  `company_id` doesn't match the host's tenant (`403`).
- **`X-Forwarded-Host` trust:** once the middleware trusts this header, anyone hitting the
  backend's public `*.run.app` URL directly could forge a tenant. **Mitigation (🚧 M3):**
  restrict `hire-flow-api` ingress to *"internal and Cloud Load Balancing"* (reachable only via
  `hire-flow-web`), or have nginx inject a shared secret the middleware requires. Do **not** add
  a restrictive `TrustedHostMiddleware` (you'd have to enumerate every tenant domain).
- **TLS** is Google-managed per domain via Certificate Manager DNS authorization — auto-renewing.
- **Embed keys:** the legacy `?key=sk_live_…` flow puts a live key in URLs/`localStorage`. Prefer
  the keyless `/careers` path for first-party pages; consider a separate read-only public key for
  third-party embeds (🚧 follow-up).

---

## 10. Rollout milestones

| Milestone | Contents | State |
|---|---|---|
| **M0** | nginx `X-Forwarded-Host`; middleware prefer it; add `custom_domain` column + Alembic migration | 🚧 |
| **M1** | Frontend host-awareness: `/careers` route, keyless careers/portal/login, host-aware redirects | 🚧 |
| **M2** | GCP ALB + Certificate Manager; `provision_managed_cert` + status endpoint; fix verify message | 🚧 |
| **M3** | Harden `X-Forwarded-Host`; CORS scoping; rotate leaked secret; read-only public key | 🚧 |

Backend tenant-resolution scaffolding, the domain lifecycle endpoints, and the host-resolved
careers API are ✅ Built today; the milestones above make them reachable end-to-end on a real
tenant domain.

---

## 11. Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Tenant domain serves the SPA but shows platform/default branding | Host not reaching the middleware | Confirm nginx sends `X-Forwarded-Host` and the middleware reads it; log the resolved host. |
| `404` on `/api/v1/public/careers` | No tenant resolved for the host | `custom_domain` matches exactly **and** `domain_verified = True` **and** `is_active = True`? Column exists on the DB? |
| Verify step fails | TXT record missing/mismatched or `dnspython` not installed | `dig TXT _hireflow.<domain>`; check the value equals `hireflow-verify=<token>`. |
| Cert stuck "provisioning" | DNS-auth CNAME not resolving yet | Confirm the DNS-authorization CNAME is present; allow propagation (up to ~24h first time). |
| A user gets `403` on a tenant domain | Their JWT `company_id` ≠ the host's tenant | Expected white-label guard — they belong to another company. |

---

## 12. Related files

- Plan (with the full `gcloud` appendix): `.claude/plans/as-the-project-system-silly-cosmos.md`
- Middleware: `backend/src/middleware/tenant_domain.py`
- Public careers API: `backend/routes/public_routes.py`
- Domain lifecycle: `backend/routes/superadmin_routes.py`
- Tenant scoping: `backend/src/tenancy/`, `backend/src/dependencies/tenant.py`, `backend/src/auth.py`
- Company model: `backend/src/models/models.py`
- Schema/columns helper: `backend/scripts/apply_tenancy_columns.py`
- Frontend careers/routing: `frontend/src/pages/public/JobBoard.jsx`, `frontend/src/App.jsx`
- nginx: `frontend/nginx.conf.template`
- GCP topology: `DEPLOYMENT_STRATEGY_GCP.md`
