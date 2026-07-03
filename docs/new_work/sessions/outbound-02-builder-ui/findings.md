# Findings: Outbound 02 — Visual Workflow Builder UI

> Research record for the end-to-end Builder UI session. Compiled 2026-07-03 from the
> Scope (§9.1), Plan 02, the Phase-2 verification report, and first-hand codebase research
> (graphify-oriented + direct reads + 3 parallel research passes). Everything below is
> verified against the live codebase at HEAD `5563b57`.

---

## 1. What the Builder must be (from Scope §9.1 + Plan 02)

A GoHighLevel-style **no-code visual canvas** where a non-technical clinic admin authors,
configures, validates, publishes, pauses, duplicates, and versions workflows:

- Canvas of connected steps (pan/zoom, node selection, visual branches/waits).
- Side **palette** to add triggers/actions/waits/conditions from the §6 catalog.
- **Per-step configuration** via guided typed forms (never raw JSON).
- **Validation guardrails** that block invalid/non-compliant workflows and link errors to nodes.
- **Draft / Publish / Pause** with version history.
- **Template start** (the 4 launch campaigns) + **preview & test-run** before publish.

Launch posture: **template configuration + guided visual customization** for clinics;
free-form authoring is operator/roadmap but the architecture must not preclude it.

---

## 2. Existing frontend architecture (all paths under `nexus-dashboard-web/`)

### Stack & tooling
- Vite 7, React 19.2, TS 5.9, React Router 7.13, Tailwind 3.4, shadcn (copy-in) on Radix.
- Forms: **react-hook-form 7.71 + @hookform/resolvers 5.2 + zod 4.3** (canonical typed-form idiom).
- State/data: **raw `useEffect` + `useState`** — NO react-query/SWR anywhere.
- Toasts: `sonner` (`import { toast } from "sonner"`; `<Toaster/>` mounted in `AppLayout.tsx`).
- Icons: `lucide-react`. Styling helper: `cn()` in `src/lib/utils.ts` (`twMerge(clsx(...))`). Variants via `cva`.
- Path alias `@/` → `src/`.
- **React Flow / @xyflow/react is NOT installed** — must be added for the canvas.
- Testing: **Vitest 4.1 + @testing-library/react 16 + jest-dom + user-event + jsdom**; config in
  `vite.config.ts` (`environment: 'jsdom'`, `globals: true`, `setupFiles: ['./src/test/setup.ts']`, `css: false`);
  tests live in `src/test/`.

### Routing — `src/router.tsx`
- Lazy pages via `React.lazy` + local `S` Suspense helper: `<RoleGuard><S><Page/></S></RoleGuard>`.
- Root `AppLayout` (provider stack: Auth > Institution > Location > Notification + `<Outlet/>` + `<Toaster/>`)
  → `DashboardWrapper` (auth gate + `SidebarProvider` + `TopNav` + `AppSidebar` + `<Outlet/>`).
- **Existing Plan-08 campaign routes (to nest under):**
  ```tsx
  { path: "institution-admin/campaigns", element: <RoleGuard allowed={["INSTITUTION_ADMIN"]}><S><Campaigns/></S></RoleGuard> }
  { path: "institution-admin/campaigns/:id", element: <RoleGuard allowed={["INSTITUTION_ADMIN"]}><S><CampaignDetail/></S></RoleGuard> }
  ```
  Paths are relative (children of `/`). Static segments outrank params in RRv7 ranking (so `.../templates` beats `.../:id`).

### Sidebar — `src/components/app-sidebar.tsx`
- `NavItemDef = { title; url; icon: React.ElementType; exact? }`. Per-role arrays; `institutionAdminNav` holds:
  ```tsx
  { title: "Campaigns", url: "/institution-admin/campaigns", icon: Megaphone }
  ```
- Active check: `pathname === url || pathname.startsWith(url + "/")` — so the existing Campaigns entry
  already highlights for all nested builder routes. **No new nav entry strictly required**, but a
  "Templates" affordance improves discovery.

### Contexts
- `LocationContext`: `useLocationContext()` → `{ locations, selectedLocationId, selectedLocation, setSelectedLocationId, isLoading, canSwitch, refresh }`; convenience `useSelectedLocationId()` → `string | undefined`. `canSwitch` only for INSTITUTION_ADMIN; persisted at localStorage `nex.selectedLocationId`.
- `InstitutionContext`: `useInstitution()` → `{ profile, hasPms, pmsType, isLoading }`.

### API wrapper — `src/lib/api.ts`
- Shared axios default export, baseURL `VITE_API_URL || http://localhost:8000/api`, `withCredentials`, Bearer from `@/lib/token-manager`, 401-refresh interceptor.
- Feature API files (`src/lib/*-api.ts`, ~20 of them) are thin: `import api from "@/lib/api"`, `async fn` → `api.get/post<T>(url)` → return `data`, no try/catch (pages handle errors). **`workflow-api.ts` mirrors this exactly.**

### RoleGuard — `src/components/RoleGuard.tsx`
- Props `{ allowed: User["role"][]; children }`. Client-side redirect (UX guard; backend is authoritative — verified backend RBAC on all automation routes).
- Roles are a string-literal union on `User.role` (`src/types/index.ts`): `"SUPER_ADMIN" | "INSTITUTION_ADMIN" | "LOCATION_ADMIN" | "STAFF" | "GROUP_ADMIN"`.

### SSE — `src/hooks/useSSE.ts`
- `useSSE()` → `{ lastEvent, connectionState }`. Event union: `calls_updated | callbacks_updated | dashboard_updated | notification`. **No workflow/campaign event type exists.** (Builder doesn't need live SSE — authoring is local; runs are Plan 08's concern.)

### UI component inventory — `src/components/ui/`
Present: button, badge, card, dialog, **sheet** (Radix dialog, `side="right"` default, `sm:max-w-sm`), form (RHF wrappers: `Form/FormField/FormItem/FormLabel/FormControl/FormMessage`), input, textarea, select, tabs, table, switch, tooltip, label, dropdown-menu, popover, separator, scroll-area, skeleton, progress, alert (inline banner), sonner, calendar, chart, checkbox, masked-phi, notification-dialog, sidebar.
**Missing:** `alert-dialog`, `accordion` (both hand-rolled or absent).

### Key UI idioms to mirror
- **Page shell** (from `pages/Campaigns.tsx` / `CampaignDetail.tsx`): `<div className="relative flex-1 space-y-6 bg-background p-8 pt-6">` + fixed violet blur backdrop; icon header (`text-3xl font-bold tracking-tight`) + description + `Refresh` outline button; `Card`/`CardContent`; `Skeleton` loading; centered icon-in-circle empty state.
- **Status pills**: hand-rolled `STATUS_STYLES: Record<string,string>` + `cn()` span (NOT the `Badge` variant). Copy the map (`active`=emerald, `paused`=amber, `archived`=zinc, `draft`=blue).
- **Typed forms** (from `components/tenants/TenantForm.tsx`): `useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues })` + `<Form {...form}><form onSubmit={form.handleSubmit(onSubmit)}>` + `<FormField control render={({field}) => <FormItem><FormLabel/><FormControl><Input {...field}/></FormControl><FormMessage/></FormItem>} />`. For `Select`, wrap `SelectTrigger` in `<FormControl>` and bind `value={field.value} onValueChange={field.onChange}`.
- **Right panel** → `Sheet` (`open`/`onOpenChange`, `SheetContent className="sm:max-w-md"`). **Modals** → `Dialog`.
- **Destructive confirm** → `Dialog` + `<Button variant="destructive">` (mirror `pages/EmailTemplates.tsx` reset dialog). No `AlertDialog` primitive exists; avoid native `confirm()` for a polished builder.
- **Tabs/Table** idioms per `TenantDetail.tsx` / `Tenants.tsx`.
- **Toasts**: `toast.success/error`.

### Test idioms — `src/test/`
- Pure logic: `import { describe, it, expect } from "vitest"` over `@/lib/...` functions.
- Component: RTL `render(<MemoryRouter><AuthProvider><LocationProvider>…</LocationProvider></AuthProvider></MemoryRouter>)` with `vi.mock("@/lib/api")`, `vi.mock("@/lib/token-manager")`, `vi.mock("sonner")`.

---

## 3. Backend contract (authoritative — the UI must match)

**URL bases** (routers mounted at `/api`, NOT `/api/v1`; frontend `api` baseURL already ends in `/api`):
- `/automation/workflows`, `/automation/templates` (relative to the axios baseURL).

### Workflow lifecycle — `src/app/api/routes/automation_workflows.py`
| Method + path | Purpose | Notes |
|---|---|---|
| `POST /automation/workflows` | Create workflow | body `{name, definition}`; **create-and-publish → status `active`** (no draft-with-definition path) |
| `GET /automation/workflows` | List | InstitutionAdmin |
| `GET /automation/workflows/{id}` | Get one | 404 if missing |
| `PATCH /automation/workflows/{id}` | Update name and/or **publish new definition version** | body `{name?, definition?}` |
| `POST .../{id}/publish` | Publish/activate (reuses current def if none) | |
| `POST .../{id}/pause` · `/resume` · `/archive` | Lifecycle | |
| `POST .../{id}/enroll` | Enroll one contact (real run) | 409 if not `active` / no version |
| `GET .../{id}/runs` · `/runs/{run_id}` · `POST /runs/{run_id}/cancel` | Runs | coarse status only |
| `POST .../{id}/bulk-enroll` | Up to 500 async | |
| `GET/POST/DELETE .../outbound-halt` | Emergency halt status/activate/release | **queryable pre-send by UI** |

`WorkflowResponse`: `{id, name, status, trigger_type|null, definition|null, current_version_id|null, created_at, updated_at}`.
`status` ∈ `draft|active|paused|archived` (**no `published`** — publish sets `active`).

### Templates — `src/app/api/routes/automation_templates.py`
- `GET /automation/templates` → `[{id, name, description, trigger_type, definition, tags}]`
- `GET /automation/templates/{id}` → same
- `POST /automation/templates/{id}/instantiate` → **BROKEN (TPL-01/02)**: passes `trigger_type=`/`definition=` to `create_draft` which accepts neither; also `create_draft` never persists a version. Raises `TypeError`.
- 4 templates: `appointment-reminder-24h`, `appointment-confirmation-48h`, `recall-sms-6month`, `reactivation-sms-18month`. `send_voice` intentionally omitted (needs clinic Retell agent id).

### Definition schema — `src/app/services/automation/definition_schema.py` (the JSON the builder authors)
- **All models `extra="forbid"`** → **CANNOT persist visual x/y coordinates** in the definition (backend 422s on unknown keys). Layout must be computed client-side.
- Top-level `WorkflowDefinition`: `{ schema_version: "1.0", trigger, entry_node_id, nodes[≥1] }`.
- **Triggers** (discriminated on `type`): `appointment_offset {offset_hours:int, appointment_type_ids?:str[]}`, `recall_scan {recall_interval_months:int≥1}`, `manual {}`, `bulk_import {}`.
- **Nodes** (discriminated on `type`, all have `id`):
  - `wait { delay: WaitDelay, next_node_id, respect_quiet_hours=true }`
  - `send_sms { body_template(≥1), next_node_id, respect_quiet_hours=true, max_attempts=1 (1..3) }`
  - `send_voice { retell_agent_id(≥1), next_node_id, respect_quiet_hours=true, max_attempts=1 (1..3) }` (no body)
  - `send_email { subject_template(≥1), body_template(≥1), next_node_id, respect_quiet_hours=true, max_attempts=1 (1..3) }`
  - `condition { logic: "AND"|"OR"=AND, rules:[≥1], true_next_node_id, false_next_node_id }`
  - `exit { outcome?: str|null }`
- `WaitDelay` (discriminated on `delay_type`): `duration {duration_seconds:int≥0}`, `calendar {offset_days:int, time_of_day "HH:MM"}`.
- `ConditionRule`: `{ field(≥1), op: eq|neq|in|not_in|is_null|is_not_null, value?: bool|int|str|str[]|null }` (list values must be all strings).
- `validate_graph_structure` (post-validator) enforces: entry_node_id exists; wait/send `next_node_id` refs exist; condition true/false refs exist; **≥1 exit node**. Raises a single `ValueError` string (→ 422). Does NOT check unreachable nodes, cycles, or duplicate ids.
- Observed merge fields in templates: `{{patient_first_name}}`, `{{clinic_name}}`.

### Missing backend endpoints the Plan-02 UI "wants" (→ client-side or documented dependency)
- No pre-publish **validate** endpoint returning node-linked errors → build **client-side validation** mirroring `validate_graph_structure` + richer checks; backend 422 remains the authoritative fallback on publish.
- No **merge-field catalog** endpoint → ship a client-side catalog constant.
- No **channel-readiness** endpoint (Plan 10 added `institutions.twilio_account_sid_encrypted`/`email_from_address` but no readiness route) → surface **emergency-halt** state (exists) + client-side per-node completeness (e.g. voice needs `retell_agent_id`); document full readiness as a Plan 10 dependency.
- No **preview/test-run/dry-run** endpoint → build **client-side** message preview + graph simulation (no dispatch).
- No **version-list** endpoint (only `current_version_id`) → versions page shows the **current published snapshot** read-only; document full history as a backend dependency.

---

## 4. Constraints & decisions derived from the above
1. **No backend changes** — stay in the isolated frontend lane (the reason Plan 02 was chosen). All "missing endpoints" are implemented client-side or documented as dependencies of other plans (01/06/10). See §2 of `task_plan.md`.
2. **Clone template via the working create endpoint**, not the broken instantiate: `getTemplate(id)` → `createWorkflow({name, definition})`. Documented; switchable to `instantiate` once TPL-01/02 is fixed.
3. **Layout is derived, never persisted** — deterministic layered layout from `entry_node_id` (BFS depth = column, siblings stacked). Keeps definitions schema-valid.
4. **No true draft-with-definition** exists server-side (ENG-11/12, TPL-02). The builder models the *editing buffer* as a client-side draft (in-memory + localStorage autosave); "Publish" = `POST` (new) or `PATCH` (existing) with an explicit confirmation. Draft-first server lifecycle is a pending product decision — documented.
5. **Nest under `/institution-admin/campaigns/*`** (not a new `/campaigns` root) → natural extension, reuses the existing nav highlight, avoids the Plan-02↔Plan-08 route collision flagged in the verification report.
6. RBAC `allowed={["INSTITUTION_ADMIN"]}` — matches backend create/publish RBAC and the existing campaigns pages.

## 5. Open limitations to record (implement around, document clearly)
- Full version history, backend validation payloads, merge-field catalog, channel-readiness, and non-destructive test-run are **backend follow-ups** owned by Plans 01/06/10. The UI degrades gracefully and is wired to switch to them when they land.
- `send_voice` templates absent upstream; the builder still supports authoring a voice node (with `retell_agent_id`).

> **Update 2026-07-03 (session `outbound-03-builder-backend-followups`):** the
> dependency-free subset has since been implemented on the backend —
> **version-list** (`GET /automation/workflows/{id}/versions`), a **node-linked
> validate endpoint** (`POST /automation/workflows/validate`), and the **broken
> `instantiate`** fix. Still deferred (concrete blockers): merge-field catalog &
> server test-run (no send-handler renderer yet, Plans 03/04/05), channel readiness
> (voice needs Retell provisioning), draft-with-definition (product decision +
> migration), and optimistic-lock. See that session's `findings.md §3`.
