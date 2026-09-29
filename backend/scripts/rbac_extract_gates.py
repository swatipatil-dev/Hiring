"""Extract the authorization gate on every registered endpoint.

Why this exists
---------------
We are building a permission catalogue from the authorization checks that already
exist, and the catalogue is only as good as the inventory it is derived from.
Grep cannot produce that inventory correctly in this codebase:

* ``require_roles(*roles)`` (``routes/advanced_features_routes.py:64``) is a
  varargs closure. A regex sees the literal ``*roles`` and learns nothing; the
  actual tuple lives in ``fn.__closure__``.
* ``require_superadmin`` appears ~19 times in the source but gates 32 endpoints,
  because role-sets that include ``superadmin`` arrive through other gates too.
* Router-level ``dependencies=[...]`` gate every endpoint in a file without
  appearing on any of them. ``routes/database_management_routes.py:22`` and
  ``routes/ats_routes.py:30`` are both invisible to a per-endpoint grep.
* Route files can exist without being registered (three such dead files --
  ``routes/enhanced_candidate_routes.py``, ``routes/enhanced_hr_routes.py`` and
  the root ``ats_routes.py`` -- have since been deleted). A file-walker would
  catalogue their endpoints; the live app never registered them.

So this walks what FastAPI actually built: ``app.routes`` -> ``APIRoute.dependant``,
recursively, reading role literals out of closures. Prefixes are already applied,
nesting is already resolved, and dead files are absent by construction.

Two jobs, one script:

1. ``--snapshot``  Emit a stable, sorted inventory for diffing. Run it BEFORE the
   RBAC change and commit the output; run it again after. **An empty diff is the
   proof that milestone 1 changed no authorization behaviour.**
2. ``--emit-csv``  Emit the draft permission matrix for human curation.

Usage::

    python scripts/rbac_extract_gates.py --snapshot > data/rbac_gates_before.txt
    python scripts/rbac_extract_gates.py --emit-csv data/rbac_permission_matrix.csv
    python scripts/rbac_extract_gates.py --summary

Read-only. Touches no database -- ``app.routes`` is built at import time.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

# Appended, never inserted: the backend contains a directory named `alembic`, and
# putting the root first makes `import alembic` resolve to those migration scripts
# as an empty namespace package instead of the library. Same reasoning as
# scripts/schema_diff.py:42-44.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR))


# ---------------------------------------------------------------------------
# Gate identification
# ---------------------------------------------------------------------------

# Gates whose role-set is fixed and cannot be read from a closure. Keyed by
# __qualname__. Values mirror the source exactly -- get_current_hr admits admin
# as well (src/auth.py:435), and get_current_admin deliberately EXCLUDES
# superadmin (src/auth.py:425), which is why superadmin cannot reach the admin
# surface at all.
STATIC_GATES = {
    "AuthMiddleware.get_current_admin":       ("admin",),
    "AuthMiddleware.get_current_hr":          ("hr", "admin"),
    "AuthMiddleware.get_current_interviewer": ("interviewer",),
    "AuthMiddleware.get_current_candidate":   ("candidate",),
    "require_superadmin":                     ("superadmin",),
}

# Gates built by a factory, whose roles live in the returned closure.
#   require_role(allowed_roles)  -> role_checker   (src/auth.py:248)
#   require_roles(*roles)        -> _check         (routes/advanced_features_routes.py:64)
CLOSURE_GATES = {
    "require_role.<locals>.role_checker",
    "require_roles.<locals>._check",
}

# Authenticates but authorizes nothing. An endpoint whose only gate is this one
# is reachable by ANY signed-in user, whatever their role.
AUTHN_ONLY = "get_current_user"

AUTHENTICATED = "AUTHENTICATED"
PUBLIC = "PUBLIC"


def _roles_from_closure(fn):
    """Pull the role list out of a factory-built checker's closure."""
    for cell in (fn.__closure__ or ()):
        try:
            val = cell.cell_contents
        except ValueError:          # empty cell
            continue
        if isinstance(val, (list, tuple)) and val and all(isinstance(v, str) for v in val):
            return tuple(val)
    return ()


def _walk(dependant, found, seen=None):
    """Collect every gate in a dependency tree, depth-first.

    Router-level and endpoint-level dependencies both land here -- FastAPI has
    already merged them into one tree by the time the app is built.
    """
    if seen is None:
        seen = set()
    if id(dependant) in seen:
        return
    seen.add(id(dependant))

    call = getattr(dependant, "call", None)
    if call is not None:
        qual = getattr(call, "__qualname__", "")
        if qual in STATIC_GATES:
            found.add(STATIC_GATES[qual])
        elif qual in CLOSURE_GATES:
            roles = _roles_from_closure(call)
            if roles:
                found.add(roles)
        elif qual == AUTHN_ONLY:
            found.add((AUTHENTICATED,))

    for sub in getattr(dependant, "dependencies", []):
        _walk(sub, found, seen)


def collect_routes():
    """Every registered endpoint with its effective role-set.

    Returns a list of (method, path, frozenset_of_roles, endpoint_qualname).
    """
    from fastapi.routing import APIRoute
    from main import app

    rows = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue                      # Mount, WebSocket, static, docs
        found = set()
        _walk(route.dependant, found)

        # A real role gate beats the bare authenticated marker: an endpoint
        # carrying both is gated by the role, not merely by being signed in.
        real = {g for g in found if g != (AUTHENTICATED,)}
        if real:
            # Multiple gates on one endpoint are ANDed by FastAPI, so the
            # effective set is the INTERSECTION of what each admits.
            roles = frozenset.intersection(*(frozenset(g) for g in real))
        elif found:
            roles = frozenset({AUTHENTICATED})
        else:
            roles = frozenset({PUBLIC})

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            rows.append((method, route.path, roles,
                         getattr(route.endpoint, "__qualname__", "?")))

    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


# ---------------------------------------------------------------------------
# Slug proposal
# ---------------------------------------------------------------------------

# The first path segment names the router's AUDIENCE, not the resource:
# /api/v1/hr/jobs, /api/v1/admin/jobs and /api/v1/advanced/jobs are all the jobs
# resource. Dropping it is the single highest-value rule in the derivation --
# without it you get three separate "jobs" catalogues that must be merged by hand.
AUDIENCE_SEGMENTS = {
    "hr", "admin", "candidate", "interviewer", "superadmin",
    "advanced", "evaluation", "enhanced",
}

VERB_BY_METHOD = {
    "GET": "view", "POST": "create", "PUT": "update",
    "PATCH": "update", "DELETE": "delete",
}

# Trailing segments that are actions rather than sub-resources.
_PARAM = re.compile(r"^\{.*\}$")

# Trailing segments that are VERBS even though no {param} precedes them.
# Curated by inspecting every write path whose last segment follows a collection
# rather than an id. Without this list, POST /advanced/candidates/bulk-action and
# POST /advanced/candidates/compare both collapse to candidates.create -- two
# genuinely different operations, with different role-sets, merged silently.
# Everything NOT in this list is treated as a sub-resource noun and collapses
# into the parent resource's create/update/delete.
ACTION_SEGMENTS = {
    "action", "apply", "auto-create-job", "backup", "bulk-action",
    "clear-connections", "compare", "draft", "from-ai", "generate", "generate-jd",
    "grade-question", "hybrid", "import-registered", "optimize", "parse", "query",
    "read-all", "reap", "reconnect", "regrade-pending", "run", "score",
    "score-backlog", "submit", "test", "test-connection", "upload-logo", "verify",
    "warmup",
}


def _slug_for(method, path):
    """Propose a `resource.verb` slug for one endpoint."""
    parts = [p for p in path.strip("/").split("/") if p]
    if parts[:2] == ["api", "v1"]:
        parts = parts[2:]

    # Rule 2: drop a leading audience segment when a real segment follows.
    while (len(parts) > 1 and parts[0] in AUDIENCE_SEGMENTS
           and not _PARAM.match(parts[1])):
        parts = parts[1:]

    concrete = [p for p in parts if not _PARAM.match(p)]
    if not concrete:
        return None

    # Resource is ALWAYS the first concrete segment. Sub-resources are never
    # concatenated into it: doing so produced 194 slugs, 66 of them one-offs
    # like analytics_age_distribution.view, database_backups.view and
    # candidates_scorecard.view -- distinctions nobody would ever grant
    # separately, and a catalogue too long for anyone to actually review.
    resource = concrete[0].replace("-", "_")

    verb = VERB_BY_METHOD.get(method, method.lower())

    # A trailing segment on a WRITE is an action verb only when it follows a
    # {param}: /jobs/{id}/approve is an action ON a job, while /profile/education
    # is a sub-resource OF the profile. Without the {param} test, every
    # POST /candidate/profile/<noun> became its own verb -- profile.education,
    # profile.skills, profile.projects and 14 more, when "a candidate edits
    # their own profile" is plainly one permission.
    # Reads always collapse to <resource>.view, whatever they read.
    if method != "GET" and len(parts) > 1 and parts[-1] == concrete[-1]:
        after_id = len(parts) > 2 and _PARAM.match(parts[-2])
        if after_id or parts[-1] in ACTION_SEGMENTS:
            verb = concrete[-1].replace("-", "_")

    return f"{resource}.{verb}"


GROUP_BY_RESOURCE = {
    "jobs": "Jobs", "careers": "Jobs",
    "candidates": "Candidates", "candidate": "Candidates",
    "applications": "Applications",
    "resume_pool": "Talent Pool", "talent_pool": "Talent Pool", "resume_match": "Talent Pool",
    "interviews": "Interviews", "schedule_interview": "Interviews", "calendar": "Interviews",
    "tests": "Tests & Assessments", "test": "Tests & Assessments", "mcq": "Tests & Assessments",
    "questions": "Questions", "question_bank": "Questions", "skills": "Questions",
    "judge": "Coding Judge", "problems": "Coding Judge", "submissions": "Coding Judge",
    "proctor": "Proctoring", "sessions": "Proctoring",
    "offers": "Offers",
    "feedback": "Feedback & Evaluation", "evaluation": "Feedback & Evaluation",
    "users": "Users", "hr_profiles": "Users", "interviewers": "Users", "panel_staff": "Users",
    "departments": "Departments",
    "email": "Email & Mailboxes", "mailboxes": "Email & Mailboxes",
    "mail_templates": "Templates", "templates": "Templates", "jd_templates": "Templates",
    "ats": "ATS",
    "support": "Support",
    "analytics": "Analytics", "stats": "Analytics", "dashboard": "Analytics",
    "companies": "Companies", "own_company": "Companies", "upload_logo": "Companies",
    "database": "System & Database", "system": "System & Database", "config": "System & Database",
    # Resources that surfaced once sub-resource concatenation was dropped.
    "profile": "Candidates", "applications": "Applications",
    "candidate_requests": "Candidates", "cover_letter": "Candidates",
    "gender_stats": "Analytics", "analytics": "Analytics", "department_stats": "Analytics",
    "calendar": "Interviews", "schedule_interview": "Interviews", "my_assignments": "Interviews",
    "my_candidate_requirements": "Candidates",
    "test_results": "Tests & Assessments",
    "email_config": "Email & Mailboxes", "mail_preview": "Email & Mailboxes",
    "send_mail": "Email & Mailboxes", "notifications": "Email & Mailboxes",
    "pipeline_notifications": "Email & Mailboxes",
    "hr_profiles": "Users", "department_chat": "Departments",
    "resume_match": "Talent Pool",
}

GROUP_ORDER = [
    "Jobs", "Candidates", "Applications", "Talent Pool", "Interviews",
    "Tests & Assessments", "Questions", "Coding Judge", "Proctoring", "Offers",
    "Feedback & Evaluation", "Users", "Departments", "Email & Mailboxes",
    "Templates", "ATS", "Support", "Analytics", "Companies", "System & Database",
    "Unclassified",
]

ROLE_COLUMNS = ["superadmin", "admin", "hr", "interviewer", "candidate"]

# slug -> the role-set that KEEPS the base slug. Every other role-set on that
# slug is moved to "<slug>_all". See the comment at the use site.
SPLIT_RULES = {
    "jobs.view":       frozenset({"candidate"}),
    "interviews.view": frozenset({"candidate"}),
    "candidates.view": frozenset({"admin", "hr", "interviewer"}),
}


# Human-readable labels. These are DEFAULTS for review, not final copy -- the
# Permission and Description columns are the two things a person is expected to
# rewrite. Generating them beats leaving 136 blank cells: a reviewer corrects
# wording far faster than they invent it.
VERB_LABELS = {
    "view": "View", "view_all": "View all", "create": "Create",
    "update": "Edit", "delete": "Delete",
}

RESOURCE_LABELS = {
    "ats": "ATS", "mcq": "MCQ", "jd_templates": "JD templates",
    "hr_profiles": "HR profiles", "resume_pool": "talent pool",
    "talent_pool": "talent pool", "mail_templates": "mail templates",
}


def _label(slug, endpoints):
    """(Permission name, Description) defaults for one slug."""
    resource, verb = slug.split(".", 1)
    res = RESOURCE_LABELS.get(resource, resource.replace("_", " "))
    if verb in VERB_LABELS:
        name = f"{VERB_LABELS[verb]} {res}"
    else:
        name = f"{verb.replace('_', ' ').capitalize()} {res}"
    name = name[0].upper() + name[1:]

    prefixes = sorted({"/" + "/".join(e.split(" ", 1)[1].strip("/").split("/")[:3])
                       for e in endpoints})
    n = len(endpoints)
    desc = (f"{n} endpoint{'s' if n != 1 else ''} under "
            + ", ".join(prefixes[:3])
            + ("..." if len(prefixes) > 3 else ""))
    return name, desc


def build_matrix(rows):
    """Collapse endpoints into draft permission slugs.

    Grants for a slug are the UNION of the role-sets of the endpoints that
    collapsed into it. The union is the one real hazard in this derivation -- it
    can silently widen access -- so every slug whose endpoints did NOT all share
    one role-set is flagged Conflict=Y for human adjudication.
    """
    by_slug = defaultdict(lambda: {"endpoints": [], "rolesets": set(), "gates": set()})
    undocumented = []

    for method, path, roles, _ in rows:
        if roles == frozenset({PUBLIC}) or roles == frozenset({AUTHENTICATED}):
            # Not a permission -- recorded as a documentation-only row so the
            # signer sees the currently-ungated surface.
            undocumented.append((method, path, next(iter(roles))))
            continue
        slug = _slug_for(method, path)
        if slug is None:
            continue
        # Scope-override split. Collapsing these by union would grant strictly
        # more than any endpoint grants today -- jobs.view would merge the public
        # job board (/candidate/jobs) with the APPLICANT LIST
        # (/hr/jobs/{id}/candidates), so a candidate holding jobs.view would see
        # applicants. Endpoints matching the named role-set keep the base slug;
        # every other endpoint moves to <slug>_all, held only by the wider roles.
        # This is the cases.view / cases.view_all pattern from
        # docs/RBAC_REFERENCE_FROM_LCMS.md section 2.
        if slug in SPLIT_RULES and roles != SPLIT_RULES[slug]:
            slug = slug + "_all"
        e = by_slug[slug]
        e["endpoints"].append(f"{method} {path}")
        e["rolesets"].add(roles)
        e["gates"].add(",".join(sorted(roles)))

    out = []
    for slug, e in by_slug.items():
        resource = slug.split(".")[0]
        group = GROUP_BY_RESOURCE.get(resource, "Unclassified")
        granted = set().union(*e["rolesets"])
        name, desc = _label(slug, e["endpoints"])
        out.append({
            "Group": group,
            "Permission": name,                     # default; rewrite in review
            "Slug": slug,
            "Description": desc,                    # default; rewrite in review
            "Display_Order": 0,
            **{r: ("Y" if r in granted else "") for r in ROLE_COLUMNS},
            "Endpoints": " | ".join(sorted(e["endpoints"])),
            "Current_Gate": " / ".join(sorted(e["gates"])),
            "Conflict": "Y" if len(e["rolesets"]) > 1 else "",
            "Notes": "",
        })

    out.sort(key=lambda r: (GROUP_ORDER.index(r["Group"]), r["Slug"]))
    for i, r in enumerate(out, start=1):
        r["Display_Order"] = i * 10
    return out, undocumented


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def cmd_snapshot(rows):
    """Stable inventory for before/after diffing."""
    for method, path, roles, endpoint in rows:
        gate = "|".join(sorted(roles))
        print(f"{method:7} {path:70} {gate:45} {endpoint}")
    print(f"# {len(rows)} endpoints")


def cmd_summary(rows):
    counts = defaultdict(int)
    for _, _, roles, _ in rows:
        counts["|".join(sorted(roles))] += 1
    print(f"{len(rows)} endpoints, {len(counts)} distinct effective role-sets\n")
    for gate, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4}  {gate}")


def cmd_emit_csv(rows, dest):
    matrix, undocumented = build_matrix(rows)
    fields = (["Group", "Permission", "Slug", "Description", "Display_Order"]
              + ROLE_COLUMNS + ["Endpoints", "Current_Gate", "Conflict", "Notes"])

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in matrix:
            w.writerow(row)

        # Documentation-only rows: blank Slug, so the seeder skips them. This is
        # how the currently-ungated surface reaches the document that gets signed.
        if undocumented:
            w.writerow({f: "" for f in fields})
            w.writerow({**{f: "" for f in fields},
                        "Group": "UNGATED ENDPOINTS",
                        "Permission": "-- rows below are documentation only, no Slug, not seeded --"})
            for method, path, gate in sorted(undocumented, key=lambda t: (t[2], t[1])):
                w.writerow({**{f: "" for f in fields},
                            "Group": "UNGATED ENDPOINTS",
                            "Permission": f"{method} {path}",
                            "Current_Gate": gate,
                            "Notes": "no permission -- triage separately"})

    conflicts = sum(1 for r in matrix if r["Conflict"] == "Y")
    print(f"wrote {dest}")
    print(f"  {len(matrix)} draft permission slugs")
    print(f"  {conflicts} CONFLICT rows needing adjudication")
    print(f"  {len(undocumented)} ungated endpoints recorded as documentation-only")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", action="store_true",
                   help="stable per-endpoint inventory, for before/after diffing")
    g.add_argument("--summary", action="store_true",
                   help="counts per effective role-set")
    g.add_argument("--emit-csv", metavar="PATH",
                   help="write the draft permission matrix CSV")
    args = ap.parse_args()

    rows = collect_routes()
    if args.snapshot:
        cmd_snapshot(rows)
    elif args.summary:
        cmd_summary(rows)
    else:
        cmd_emit_csv(rows, args.emit_csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
