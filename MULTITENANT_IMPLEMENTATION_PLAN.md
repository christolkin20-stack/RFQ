# MULTITENANT_IMPLEMENTATION_PLAN.md

## RFQ Enterprise Upgrade Blueprint

Status: **Completed (core scope delivered)**  
Date: 2026-02-15  
Scope owner: RFQ app (Django + JS)

### Implementation Progress Snapshot

- ✅ PR1: Core schema (Company/Profile/ACL/Locks/Audit) + backfill migrations
- ✅ PR2: Project API company scoping
- ✅ PR3: Quotes + buyer Supplier API company scoping
- ✅ PR4: Project visibility ACL enforcement + `project access` API
- ✅ PR5: Lock API (acquire/heartbeat/release/status/force unlock)
- ✅ Lock UI integration implemented (item-detail + key edit views)
- ✅ Admin API + admin UI panels implemented (users/companies/audit logs/locks)
- ✅ Audit coverage implemented (locks, ACL updates, quote mutations, supplier decisions)
- ✅ ACL hardening pass completed for quote project binding/import paths
- ✅ Non-null company hardening migration implemented (`0009_enforce_company_not_null`)
- ✅ Regression tests green (`supplier_flow`, `quote_roundtrip`, `rc1_dryrun`)
- ℹ️ Remaining is optional polish only (UI ergonomics + extra tests)

---

## 0) Confirmed Product Decisions

1. **Tenant model:** 1 user = 1 company
2. **Roles:** Admin, Editor, Viewer
3. **Data isolation:** strict per company (including Quotes)
4. **Project visibility:** role-based + selected users + management access
5. **Locking scope:** item detail + other editing contexts
6. **Lock timeout:** 3 minutes
7. **On lock expiry/conflict:** soft warning + read-only
8. **Audit trail:** required
9. **Force unlock:** required (Admin + SuperAdmin)
10. **SSO:** future phase (not now)
11. **SuperAdmin:** required, including admin UI
12. **Current sync:** keep current localStorage + /api/projects/bulk for now
13. **Future direction:** move toward server-first + DB migration to Supabase

---

## 1) Current-State Summary (from code audit)

- Current app is effectively single-tenant.
- `Project` is JSON blob storage (`Project.data`) synced by `/api/projects/bulk`.
- API auth currently allows unauth in DEBUG; production uses Django auth checks.
- No company-level scoping in models/endpoints.
- Quotes are structured (`Quote`, `QuoteLine`) but not company-scoped.
- No collaborative lock service exists.
- No in-app administration for users/roles/company/project visibility.

---

## 2) Target Architecture

### 2.1 Core entities

- `Company`
- `UserCompanyProfile` (1:1 user mapping to one company, role, flags)
- `ProjectVisibility` / `ProjectAccess` (explicit project permissions)
- `EditLock` (resource lock with TTL)
- `AuditLog` (append-only event ledger)

### 2.2 Role model

- **Viewer**: read-only access to permitted projects
- **Editor**: edit access to permitted projects
- **Admin**: manage users/access/locks within own company
- **SuperAdmin**: cross-company global control

### 2.3 Permission layers

1. **Tenant boundary** (company isolation) – mandatory
2. **Role capability** (viewer/editor/admin)
3. **Project visibility ACL** (allowed users + management role access)

---

## 3) Data Model Changes

## 3.1 New models

### `Company`
- `id` (uuid or short string)
- `name`
- `is_active`
- `created_at`, `updated_at`

### `UserCompanyProfile`
- `user` (OneToOne Django user)
- `company` (FK Company)
- `role` (`admin|editor|viewer|superadmin`)
- `is_management` (bool)
- `is_active`
- timestamps

### `ProjectAccess`
- `project` (FK Project)
- `user` (FK User)
- `can_view` (bool)
- `can_edit` (bool)
- `granted_by` (FK User nullable)
- timestamps
- unique `(project,user)`

### `EditLock`
- `resource_key` (indexed string, e.g. `project:{id}:item:{item_id}:view:{context}`)
- `company` (FK Company)
- `project` (FK Project nullable)
- `locked_by` (FK User)
- `locked_by_display`
- `context` (item-detail/rfq-planner/supplier-interaction/quotes/etc)
- `expires_at`
- `created_at`, `updated_at`
- unique active lock by `resource_key`

### `AuditLog`
- `company` (FK Company nullable for superadmin events)
- `actor` (FK User nullable)
- `actor_role`
- `action` (enum string)
- `entity_type` (`project|quote|supplier_access|user|role|lock|company`)
- `entity_id`
- `project` (FK nullable)
- `metadata_json` (JSON)
- `ip`, `user_agent`
- `created_at`

## 3.2 Existing models to extend with `company`

Add `company = ForeignKey(Company, ...)` to:
- `Project`
- `Attachment`
- `SupplierAccess`
- `SupplierAccessRound`
- `SupplierInteractionFile`
- `Quote`

`QuoteLine` remains indirectly scoped by `Quote.company`.

---

## 4) API & Authorization Plan

## 4.1 Security foundation

Create shared resolver in `api_common.py`:
- `get_request_actor(request)` -> user + profile + company + role
- `require_auth_and_profile(request)`
- `require_role(min_role)`
- `require_project_view(project)`
- `require_project_edit(project)`
- `filter_queryset_by_company(qs, company)`

## 4.2 Endpoint hardening (all RFQ APIs)

Every endpoint must:
1. Resolve actor
2. Enforce company scope
3. Enforce project-level permission
4. Emit audit log for mutations

Critical endpoints:
- `/api/projects`, `/api/projects/bulk`, `/api/projects/<id>`
- Supplier interaction endpoints
- Quotes endpoints

## 4.3 Lock endpoints (new)

- `POST /api/locks/acquire`
  - payload: `resource_key`, `project_id`, `context`
  - returns: `acquired`, `owner`, `expires_at`
- `POST /api/locks/heartbeat`
  - renew lock if held by caller (extends expires_at +180s)
- `POST /api/locks/release`
- `GET /api/locks/status?resource_key=...`
- `POST /api/locks/force_unlock` (admin/superadmin)

---

## 5) Frontend UX Plan

## 5.1 Identity & permissions
- Add actor bootstrap endpoint (whoami + role + company + feature flags)
- Hide/disable UI actions by role
- Enforce backend checks regardless of UI

## 5.2 Project visibility UX
- Users see only allowed projects
- Management users auto-see management-visible projects
- Admin page controls explicit per-project assignments

## 5.3 Locking UX

On entering edit context:
1. call acquire lock
2. if conflict:
   - show who is editing + until when
   - switch view to read-only mode

During editing:
- heartbeat every 45–60 sec
- if heartbeat fails/conflict -> warning + read-only

On leave/unload:
- release best-effort

Soft-warning examples:
- "Item právě upravuje Petr Novák (lock do 16:42). Otevírám read-only režim."
- "Tvůj lock vypršel. Pro pokračování znovu otevři editaci."

---

## 6) Admin UI (in-app, not Django admin)

### 6.1 SuperAdmin console
- Companies list/create/deactivate
- Company user overview
- Global lock monitor + force unlock
- Global audit search

### 6.2 Company Admin page
- User CRUD (within company)
- Role assignment (admin/editor/viewer)
- Management flag assignment
- Project visibility matrix (role-based + selected users)
- Lock monitor (company scope)
- Audit viewer (company scope)

---

## 7) Migration Strategy (Safe, Incremental)

## Phase M1 — Schema prep
- Add new tables (`Company`, `UserCompanyProfile`, `ProjectAccess`, `EditLock`, `AuditLog`)
- Add nullable `company` FKs to target models

## Phase M2 — Data backfill
- Create default company (e.g. `Default Company`)
- Backfill all existing rows with default company
- Create initial SuperAdmin profile for chosen admin user

## Phase M3 — Enforce constraints
- Make `company` non-null on scoped tables
- Add indexes for company + updated_at/common filters

## Phase M4 — Permission enforcement switch
- Roll out API guards behind feature flag (optional)
- Verify no cross-company leaks

## Phase M5 — Locking rollout
- Enable lock endpoints
- Enable UI read-only fallbacks

---

## 8) Test Plan

## 8.1 Unit tests
- role matrix checks
- project visibility checks
- company isolation checks
- lock acquire/renew/release/expiry/conflict
- force unlock authorization
- audit creation for key actions

## 8.2 Integration tests
- API leakage tests (user A cannot read company B)
- bulk sync respects company scope
- quotes APIs scoped correctly
- supplier flows scoped correctly

## 8.3 UI acceptance
- viewer cannot mutate
- editor can edit allowed project only
- admin can assign/restrict visibility
- lock conflict message + read-only switch works

---

## 9) Rollout Plan

1. Deploy schema/backfill first
2. Deploy read-only guards + logging
3. Enable mutation guards
4. Deploy admin page
5. Deploy locking
6. Monitor logs/audit and refine

Rollback approach:
- keep migration checkpoints
- feature flags for enforcement toggles
- backup DB before hard-enforcement step

---

## 10) Future Backlog (explicitly postponed)

1. **SSO integration** (Google/Microsoft)
2. **Server-first data architecture** (reduce localStorage dependency)
3. **Database migration to Supabase**
4. Optional real-time locking via websocket channel

---

## 11) Implementation Slices (recommended PR order)

- PR1: models + migrations + backfill + helper auth/profile utilities
- PR2: company scoping for projects APIs + tests
- PR3: company scoping for quotes/supplier APIs + tests
- PR4: role & project visibility ACL + tests
- PR5: lock API + lock tests
- PR6: frontend lock/read-only UX
- PR7: in-app admin pages
- PR8: audit views + polish

---

## 12) Definition of Done

- No cross-company data exposure in any endpoint
- Role behavior works as defined
- Project visibility rules enforced backend-side
- Edit locking active with 3-min TTL, conflict notice, read-only fallback
- Admin/SuperAdmin UIs functional
- Audit trail queryable and complete for key mutations
- Existing live RFQ workflows remain stable

## 13) Completion Review (2026-02-15)

Status against DoD:
- ✅ Tenant isolation implemented in projects/quotes/buyer supplier APIs
- ✅ Role + ACL checks implemented and enforced
- ✅ Lock API + lock UI read-only fallback delivered
- ✅ Admin/SuperAdmin management endpoints + settings UI delivered
- ✅ Audit logs available + admin audit viewer + lock monitor delivered
- ✅ Regression tests pass after hardening migration

Optional follow-up (not blocking completion):
- UI polish for admin controls (bulk actions, clearer confirmation flows)
- Additional dedicated ACL/unit tests for admin edge paths
- Future roadmap items remain deferred (SSO, server-first sync, Supabase migration)
