# RBAC Reference — the LCMS pattern

Captured 2026-09-05 from the Yadadri LCMS codebase (Laravel 11 + React/TS), read-only, before
that codebase was removed from this workspace. **LCMS is not available to re-read.** Everything
needed to rebuild the pattern in the Hiring tool (FastAPI + React) is recorded here.

LCMS is a single-tenant district court-case system. The Hiring tool is multi-tenant. That
difference is the one real design fork — see [What does not transfer](#7-what-does-not-transfer).

---

## 1. The shape in one sentence

Roles and permissions are **rows in the database**, routes are gated on **permission slugs
rather than role names**, and the **frontend menu and route guard are both generated from the
grants the server returns** — so adding a role touches no route file and no navigation file.

---

## 2. Data model

Three tables (`2026_06_16_000003_create_rbac_tables.php`), plus a `user_roles` pivot created
alongside the users table. Seeded to a signed matrix: 16 roles, 31 permissions, 216 grants.

```
roles
  id, name, slug UNIQUE          -- super_admin, dlo, mro
  description
  is_system      BOOL   -- system roles cannot be deleted or recoded
  is_active      BOOL   -- an inactive role grants NONE of its permissions
  scope_level    STRING -- added later; how much of the register this role sees
  display_order  INT
  timestamps

permissions
  id, name, slug UNIQUE          -- cases.view, cases.approve_gp, users.manage_roles
  permission_group  STRING       -- "Cases", "Hearings" — drives the UI grouping ONLY
  description
  display_order INT
  timestamps

role_permissions
  role_id        FK -> roles        ON DELETE CASCADE
  permission_id  FK -> permissions  ON DELETE CASCADE
  UNIQUE(role_id, permission_id)

user_roles                         -- pivot, ON DELETE CASCADE (this cascade caused an incident)
  user_id, role_id
```

Slug convention: `resource.verb` — `cases.view`, `cases.view_all`, `users.manage_roles`.
`permission_group` is purely presentational: it is what the admin screen groups checkboxes by.

### The seeder is driven by a CSV matrix

`database/data/role_catalog_matrix.csv` is the source of truth, and it is a document the client
signed off on. Its shape:

```csv
Group,Permission,Slug,Description,"Super Admin",Admin,"District Collector",...
Cases,"View cases",cases.view,"See cases within the user's seat scope.",Y,Y,Y,...
Cases,"View ALL cases (district-wide)",cases.view_all,"Override scope...",Y,Y,Y,,,,
```

Columns 0–3 define the permission; every column after is one role, `Y` meaning granted.
`RbacSeeder` reads the header for role names, `updateOrCreate`s each role and permission by
slug, then syncs the grants.

**This is the single best idea to steal.** The authorization matrix is a spreadsheet a
non-developer can read, review and sign, and the code is generated from it rather than the other
way round.

---

## 3. Backend gating — on permissions, not roles

```php
// app/Http/Middleware/CheckPermission.php
//   usage:  ->middleware('check.permission:cases.view')
//   ANY-of: ->middleware('check.permission:cases.approve_gp,cases.approve_collector')
public function handle(Request $request, Closure $next, string ...$permissions): Response
{
    $user = $request->user();
    if (! $user) {
        return response()->json(['success' => false, 'message' => 'Unauthenticated.'], 401);
    }
    if ($user->hasAnyPermission($permissions)) {
        return $next($request);
    }
    return response()->json([
        'success' => false,
        'message' => 'Access denied. Insufficient permission.',
    ], 403);
}
```

A parallel `CheckRole` exists (`->middleware('check.role:super_admin,admin')`) but is used
sparingly — deliberately, for the few things that must never be widened by editing the
permission matrix. Example: `DELETE /users/{id}` is gated on the **role**, not a permission, so
no amount of matrix editing can hand user-deletion to anyone else.

Routes then read declaratively:

```php
Route::get('roles', [RoleController::class, 'index'])->middleware('check.permission:users.view');

Route::middleware('check.permission:users.manage_roles')->group(function () {
    Route::get('permissions',            [PermissionController::class, 'index']);
    Route::post('roles',                 [RoleController::class, 'store']);
    Route::put('roles/{id}/permissions', [RoleController::class, 'updatePermissions']);
    Route::delete('roles/{id}',          [RoleController::class, 'destroy']);
});
```

`PermissionController::index` returns the flat list **and** a `grouped` copy keyed by
`permission_group`, so the admin UI renders its sections without doing the grouping itself.

---

## 4. Frontend — the menu IS the permission model

Two structures in `src/lib/permissions.ts` do the work.

```ts
// slug -> UI capability flag
const FLAG_BY_SLUG: Record<string, keyof Permissions> = {
  "settings.manage":    "canManageSettings",
  "users.create":       "canCreateUser",
  "users.edit":         "canEditUser",
  "users.manage_roles": "canManageRoles",
  // ...
};

// capability flag -> the menu entry it unlocks
const MENU_BY_FLAG: [keyof Permissions, string][] = [
  ["canViewCases",    "/cases"],
  ["canViewCases",    "/cases/fresh"],
  ["canViewHearings", "/hearings"],
  ["canViewReports",  "/reports"],
  ["canManageRoles",  "/roles"],
  ["canCreateUser",   "/users"],
  ["canEditUser",     "/users"],
];

export function permissionsFromSlugs(slugs: string[]): Permissions {
  const granted: Partial<Permissions> = {};
  for (const slug of slugs) {
    const flag = FLAG_BY_SLUG[slug];
    if (flag) (granted as Record<string, boolean>)[flag] = true;
  }

  // every flag starts FALSE — a created role gets exactly what it was granted
  const perms = { ...makePerms({ /* all view flags explicitly false */ }), ...granted };

  const menu = new Set<string>(["/", "/profile", "/change-password"]);
  for (const [flag, href] of MENU_BY_FLAG) {
    if (perms[flag]) menu.add(href);
  }

  return {
    ...perms,
    visibleMenuItems: Array.from(menu),
    visibleSidebarSections: { main: true, admin: showsAdmin, account: true },
  };
}
```

Two rules make it safe:

- **Anything not in `FLAG_BY_SLUG` stays false.** A permission the frontend has never heard of
  must not switch something on by accident.
- **Every flag starts false**, not at a permissive default. A newly created role granted nothing
  sees nothing.

### The guard reuses the menu list — this is the key move

```tsx
// components/layout/RequirePermission.tsx
export function RequirePermission({ children }: { children: React.ReactNode }) {
  const { permissions } = useAuth();
  const { pathname } = useLocation();

  const allowed = permissions?.visibleMenuItems?.includes(pathname) ?? false;

  // `replace` so the refused URL leaves no history entry — Back returns the user
  // to where they actually were, not onto this guard again.
  if (!allowed) return <Navigate to="/" replace />;

  return <>{children}</>;
}
```

The sidebar renders `visibleMenuItems.includes(href)`; the route guard checks the same array.
They cannot drift, because they are one answer to one question rather than two.

The bug this closed: 29 admin pages were hidden from the sidebar but rendered fine if you typed
the URL. No data leaked — every endpoint refused — and that is exactly what made it worth fixing
anyway. The officer got an admin screen full of errors and empty tables, which reads as a broken
system rather than as a door they were never meant to open. A guard must also **fail closed**: a
user with no permissions object at all (still loading, or a role that resolved to nothing) is
refused, not admitted.

### Hybrid: hand-written roles + generated roles

The 15 built-in roles keep hand-written permission entries in a `switch` on the role's display
name, because they encode decisions no permission slug captures (the District Collector
deliberately not getting two recording desks, for one). `permissionsFromSlugs()` is used **only**
for roles the switch does not recognise — i.e. roles the client creates at runtime.

Before it existed, a newly created role was correct on the server (real grants, a declared scope,
cases it could see) and useless in the browser: its holder signed in to no menu at all.

---

## 5. SuperAdminGuard — copy this first, not last

`app/Support/SuperAdminGuard.php`. It exists because on 2026-08-03 the production Super Admin was
destroyed by **ordinary, permitted UI actions**. Nothing was hacked; no code was broken. Four
legitimate edits compounded:

1. Roles & Permissions — unticking the super_admin column and saving called
   `PUT roles/{id}/permissions`, which `sync()`s the new (empty) set. 36 grants → 0.
2. Roles & Permissions — renaming the role regenerated its slug, so `super_admin` no longer
   existed under that name.
3. `RbacSeeder`'s prune (`Role::whereNotIn('slug', $catalog)->delete()`) then treated the renamed
   role as obsolete and deleted it. `user_roles.role_id` is ON DELETE CASCADE, so every holder's
   link went with it.
4. User Management — Edit on the Super Admin row overwrote the account's name and email in place.

**The account cannot repair any of this itself**: once it holds no permissions it can no longer
reach the screens that would restore them. So the fix cannot be "be careful" — the invariants are
enforced below the UI, at the API, where a Postman request is bound by exactly the same rules as
a button click.

The three invariants:

- **A. The role is immutable** — it cannot be renamed, re-permissioned, deactivated, deleted or
  re-created. This holds for *everyone, the Super Admin included*: self-inflicted damage is the
  failure mode actually observed.
- **B. Exactly one login holds it** — the role can never be granted to another account, and never
  taken from the account that has it.
- **C. That login is editable only by itself** — any other user, whatever they hold, is refused on
  update / reset-password / activate / deactivate / assign-roles / assign-scopes. A null actor
  (console, queue) counts as "somebody else" and is refused rather than silently allowed.

Implementation details worth keeping:

- The reserved-slug check normalises the **name** too (`"Super Admin "` → `super_admin`), so a
  look-alike role cannot be created to collide with invariant B.
- Refusals are thrown as an explicit JSON 403 (`HttpResponseException`), never `abort(403)` or a
  `ValidationException` — those render an HTML page, or a 302 redirect, when the caller sends no
  `Accept` header (the Postman default), which reads as "it worked" to an API client.
- Deactivation is refused for `super_admin` **and** `admin`. Reactivation stays allowed, as the
  only way back if a database is restored with the role switched off.

### Other guards in RoleController worth copying

- **Delete refuses while anyone still holds the role.** `user_roles` cascades, so deleting used to
  silently strip every holder, who then kept signing in and landed on an empty "Unassigned"
  dashboard with no record of what they used to be. Now:
  *"N user(s) still hold X. Move them to another role first, then delete it."*
- **`scope_level` is required at create, not defaulted.** The column *has* a default — the
  narrowest level — so a role created without it would save happily and then show its holders
  almost nothing, with nothing on screen explaining why. Whoever creates a role is deciding who
  gets to see what; that is a question to be answered, not fallen into.
- **`scope_level` is deliberately absent from the update rule set**, and must stay absent. One
  save could otherwise widen a role to district-wide and hand every holder the entire register at
  once — no per-user review, no record of who was affected. Changing an existing role's scope is a
  migration, on purpose. (Because only validated keys reach `update()`, a `scope_level` in the
  request body is silently ignored rather than applied.)
- **Permission sync with an empty array is the most damaging call in the API** — hence the guard
  sits on `updatePermissions` before validation even runs.

---

## 6. Where the Hiring tool stands today (2026-09-05)

- `users.role` is a single `VARCHAR(50)` ([models.py:239](../backend/src/models/models.py#L239))
  with five values: `superadmin | admin | hr | interviewer | candidate`. No roles table, no
  permissions table.
- **169 call sites** hardcode role names — `Depends(require_role(["admin"]))`,
  `AuthMiddleware.get_current_hr`, and so on. Authorization is spread across route signatures.
- [Sidebar.jsx:11-56](../frontend/src/components/Sidebar.jsx#L11-L56) is a literal `NAV` dict of
  five arrays; [App.jsx](../frontend/src/App.jsx) uses `<ProtectedRoute allowedRoles={['hr']}>`.
  Menu and guard are two independently hand-maintained lists — exactly the drift
  `RequirePermission` was built to close.
- [auth.py:477](../backend/src/auth.py#L477) `get_user_permissions()` already returns a
  role→permission map. **Nothing imports it.** Dead code, and a reasonable seed for the catalogue.

---

## 7. What does not transfer

**LCMS is single-tenant; the Hiring tool is not.** Every Hiring table is `TenantScoped` with a
`company_id`. So the first decision has no LCMS precedent: are roles global to the platform, or
does each company define its own?

Per-company custom roles is the sellable feature (Acme's own "Recruiting Coordinator"), but it
means `roles.company_id`, defaults seeded per tenant at company creation, and `superadmin` sitting
outside tenancy entirely — which is already how [auth.py:242](../backend/src/auth.py#L242) treats
it (`set_current_company(None)` for superadmin).

Also LCMS-specific and **not** to be ported: `scope_level` / seat scoping (district, division,
mandal, assigned-only) exists because a court case is visible by geography. The Hiring equivalent,
if there is one, is department- or job-scoping, and should be designed fresh.

---

## 8. Migration path sketched for the Hiring tool

1. `roles`, `permissions`, `role_permissions` tables (roles carrying `company_id`; permissions
   global). Keep `users.role` as a denormalised fallback through the transition — do not drop it.
2. Build the permission catalogue as a CSV matrix, derived from what the 169 existing call sites
   actually gate today. Seed from the CSV.
3. Add `require_permission("jobs.create")` alongside the existing `require_role`. Migrate the 169
   sites in slices **by router**, not in one sweep, behind the still-live role checks.
4. Login response carries granted slugs; frontend gets `permissionsFromSlugs` + `MENU_BY_FLAG`;
   `ProtectedRoute` reads the generated menu list instead of literal `allowedRoles` arrays.
5. Admin Roles screen — the permission matrix, grouped by `permission_group`.

**Ship the SuperAdminGuard equivalent before any of the above.** The Hiring tool currently has no
protection against an admin re-permissioning themselves into a locked-out state; the moment roles
become editable, that becomes reachable. LCMS learned this in production.
