# Part 12 - Compliance, Consent, And Access-Control Implementation Plan

> **Why this plan exists.** Scope §11 defines a compliance/security/access-control framework and
> §14 lists **"Compliance framework"** as a distinct deliverable, yet no plan owned it — consent
> was being modelled **independently and conflictingly** by both Part 4 (SMS) and Part 5 (email)
> (each proposed to create/extend the shared consent tables), and several **launch-blocking**
> controls from the Gap Analysis Part II had no home: content-class + PHI validation (Finding 2),
> the healthcare-exemption frequency cap (Finding 3), AI-voice consent/disclosure (Finding 1),
> the emergency compliance halt (Finding 9), and spend/blast-radius controls (Finding 10). This
> plan is the **single owner** of the shared compliance substrate so the channel plans depend on
> it instead of duplicating it.

## What Needs To Be Built

A shared, cross-channel compliance layer enforced by the engine **before every dispatch**:

1. **Multi-channel consent & suppression model** (voice/SMS/email), per-location-scoped, with
   optional institution/DSO-wide do-not-contact — the single schema Parts 3/4/5 consume.
2. **Content-class + PHI compliance validator** used at publish time and dispatch time.
3. **Frequency capping** (healthcare-exemption caps) as a v1 launch control.
4. **AI-voice consent, in-call identity disclosure, and opt-out** handling.
5. **Emergency compliance halt** for in-flight runs on a defective/unlawful workflow version.
6. **Spend / blast-radius controls** at publish and enrollment time.
7. **New RBAC permissions** for campaign authoring/management, tenant/location scoped.

## Existing System Context

The backend already has:

- `ConsentRecord`, `SmsSuppression`, `DoNotContact` — all with `CHECK (channel IN ('sms'))`
  (SMS-only by construction).
- `SmsComplianceService` and Twilio STOP/START/HELP inbound handling (carrier-enforced too).
- Retell post-call tagging pipeline (can emit a DNC-intent tag on voice calls).
- `Call.preferred_callback_datetime` and post-call classification.
- Existing RBAC roles: `SUPER_ADMIN`, `INSTITUTION_ADMIN`, `LOCATION_ADMIN`, `STAFF`, group oversight.
- Audit service and Postgres RLS tenant isolation.

Current gaps:

- Consent/suppression is SMS-only; email path performs **zero** consent/suppression/unsubscribe checks.
- No content-class or PHI-in-body validation.
- No cross-campaign frequency cap / cooldown.
- No in-call AI-voice identity disclosure or written-consent basis capture.
- No mechanism to stop in-flight runs on a specific workflow version.
- No publish-time blast-radius / enrollment-ceiling / spend cap.
- No campaign-specific RBAC permissions.

## Existing Components To Reuse

- `SmsComplianceService` and STOP/START keyword handling (keep; generalize scope).
- Retell post-call tag pipeline for voice DNC-intent.
- Audit service for every consent change, suppression, DNC, and config change.
- RLS conventions for all new tenant-scoped tables.
- Part 1 `WorkflowValidationService` (this plan supplies its compliance rule set).
- Part 11 `UsageReportingService` (source of current-period spend for caps).

## New Components Required

### Data Model (the single shared consent substrate — Parts 4 & 5 consume, do not redefine)

- **Migrate `ConsentChannel` + CHECK constraints** on `ConsentRecord` / `SmsSuppression` /
  `DoNotContact` to include `voice` and `email` (backward-compatible; existing `sms` rows preserved
  and interpreted as sms-scoped). This migration is owned **here**, not in Parts 4/5.
- `communication_consent_records` (or generalized `ConsentRecord`)
  - `institution_id`, `location_id`, `contact_id`
  - `channel` (sms/voice/email), consent basis (`express_written`, `express`, `implied`, `exempt_treatment`)
  - proof reference (intake form, call recording id, timestamp), captured_at, captured_by
- `communication_suppressions` (or generalized)
  - scope: **per-(location, channel)** default; unique index migrated from institution+channel+phone
  - reason: `stop_keyword`, `voice_tag_dnc`, `hard_bounce`, `complaint`, `staff_manual`, `reassigned_number`, `deceased`
- `do_not_contact` — per-location default; privileged `institution`/`group` scope for "remove me everywhere".
- `contact_frequency_ledger`
  - `institution_id`, `location_id`, `contact_id`, provider/appointment context
  - rolling counters (per-day, per-week) of calls+texts per patient/provider for cap enforcement
- `workflow_content_class` (per published version, from Part 1 definition)
  - `transactional_care` (Confirmation/Reminder), `recall`, `sales_marketing`
  - drives which consent basis + validator rules apply

### Services

- `ComplianceGateService` — the single pre-dispatch gate every channel action calls
  - checks: consent basis for content class, suppression, DNC, quiet hours (delegates to Part 1
    `QuietHoursService`), and **frequency cap** — returns allow / hold-until / block(reason).
- `ConsentService` — capture/query consent across channels; intake express-consent capture.
- `SuppressionService` — apply STOP (SMS), voice DNC-tag, email hard-bounce/complaint, staff manual,
  reassigned-number/deceased; scope resolution (location vs institution vs group).
- `ContentComplianceValidator` — publish-time + dispatch-time rules:
  - block promotional language/offers in `transactional_care`/`recall` (exemption-voiding) unless
    written-consent basis exists;
  - **PHI-term detection** on SMS/email bodies (clinical detail must not go to insecure channels);
  - every send step must have a valid consent path for its content class (extends Part 1 validator).
- `FrequencyCapService` — enforce ≤1 message/day and ≤3 calls+texts/week per patient per provider
  (concise-message limits: ≤160-char SMS / ≤~1-min call for exempt campaigns), cross-campaign.
- `AiVoiceConsentService` — ensure written-consent basis for AI-voice marketing-class campaigns
  (Recall/Sales), inject in-call identity disclosure + opt-out into the outbound agent prompt,
  and treat AI voicemail as an artificial-voice message under the same rules.
- `EmergencyHaltService` — terminate **all in-flight runs** on a given workflow version (distinct
  from pause, which only halts new enrollments); cancels pending timers + queued channel attempts.
- `BlastRadiusService` — publish-time and enrollment-time checks: enrollment ceiling, projected
  spend vs `usage_budgets` (Part 11), step-up approval for large campaigns.

## End-To-End Implementation Approach

1. Migrate consent/suppression/DNC to multi-channel, location-scoped (backward compatible).
2. Build `ComplianceGateService` and route Parts 3/4/5 sends through it (defense-in-depth: the
   channel services keep their own low-level checks).
3. Add content class to workflow versions; implement `ContentComplianceValidator` and wire into
   Part 1 publish validation + dispatch-time recheck.
4. Add frequency ledger + `FrequencyCapService`; enforce in the enrollment gate (Part 1) and pre-dispatch.
5. Add AI-voice consent basis + in-call disclosure/opt-out (feeds Part 3 outbound prompt/config).
6. Add emergency halt; surface in operator tooling (Part 8 operations page).
7. Add blast-radius/spend caps at publish + enrollment; surface warnings in Part 2 builder + Part 8.
8. Add new RBAC permissions (create/edit/publish/pause workflows; configure campaigns; set DNC;
   run emergency halt; approve large campaigns), tenant/location scoped.
9. Audit every consent/suppression/DNC/config/halt action with actor attribution.

## Architecture Decisions

- **One consent substrate, owned here.** Parts 4/5 must not each migrate the consent tables;
  they call `ConsentService`/`ComplianceGateService`. Resolves the Part 4 ↔ Part 5 conflict.
- **Server-side gate is authoritative.** Builder validation (Part 2) improves UX; the engine's
  pre-dispatch gate is the enforcement point.
- **Content class is a first-class, validated property** — a clinic cannot silently turn an
  exempt care campaign into telemarketing by editing copy (Finding 2).
- **Frequency cap ships in v1** as a launch compliance control, not a deferred nicety — it is a
  condition of the TCPA healthcare exemption (Finding 3). Basic per-patient/per-provider cap only;
  richer preference-center deferred.
- **Pause ≠ halt.** Emergency halt stops in-flight runs on a version; pause only stops new
  enrollments (Finding 9).
- **Opt-out is not consent.** For AI-voice marketing-class campaigns (Recall/Sales), capture
  express (written where required) consent at intake; keyword/tag handles withdrawal (Finding 1).

## Technical Considerations

- Migration must preserve existing SMS `ConsentRecord`/`SmsSuppression`/`DoNotContact` rows and
  interpret pre-migration scope correctly (verify current institution- vs location-scope).
- Quiet hours: clinic-level TZ for v1 (Part 1). **US multi-timezone caveat (Finding 12):** a
  US clinic contacting out-of-region patients at clinic-9am can violate the called party's quiet
  hours — flag as a US-market guardrail (widen window or require patient-TZ capture) before
  enabling US cross-zone campaigns.
- Cross-channel fallback (voicemail → SMS, Part 3/Finding 14) must re-check the target channel's
  own consent + line-type through the gate, not inherit voice consent.
- PHI-term detection is heuristic; treat as a warning+block for high-risk terms, tunable, audited.
- Region rules: US A2P 10DLC vs Canada toll-free + CASL (bilingual EN/FR STOP) — consent basis and
  disclosure text differ by region; keep region-aware.

## Dependencies

- Part 1 (validator hook, enrollment gate, QuietHoursService, run cancellation for halt).
- Parts 3/4/5 (route sends through the gate; supply channel-specific suppression signals).
- Part 11 (current-period spend for blast-radius/budget caps).
- Part 2 / Part 8 (surface validation blockers, blast-radius warnings, emergency halt control).
- Legal classification of each campaign (exempt-care vs marketing) — product/legal decision.

## Edge Cases

- Contact opts out on one channel mid-run with future steps on other channels.
- Written consent absent for a marketing-class campaign at publish → block publish.
- Promotional phrase added to a Recall template → validator blocks or forces written-consent gating.
- Patient matches Confirmation + Reminder + Recall in one week → frequency cap holds/drops later touches.
- Emergency halt fired while runs are mid-dispatch → cancel timers, no double-contact.
- Blast-radius ceiling exceeded on a CSV enrollment → require step-up approval.
- Reassigned/deceased number suppression added after enrollment.
- Legacy sms-only rows read after the multi-channel migration.

## Risks

- Migrating live consent tables can create subtle compliance regressions (mitigate: backward-compatible, tested).
- Over-aggressive PHI/promotional detection blocks legitimate messages (mitigate: tunable, warn+audit).
- Frequency cap misconfigured could suppress legitimate care reminders (mitigate: per-provider defaults, observability).
- If the gate is bypassed by any channel path, a non-compliant send escapes (mitigate: single gate + defense-in-depth).

## Validation Strategy

- Unit tests: consent-basis-by-content-class matrix; suppression precedence; location vs institution scope.
- Unit tests: frequency cap counting across campaigns, day/week windows, per-provider.
- Unit tests: content validator (promotional in exempt class; PHI-term detection; missing consent path).
- Unit tests: emergency halt cancels timers + in-flight attempts; distinct from pause.
- Unit tests: blast-radius ceiling + spend cap using Part 11 usage.
- Integration: STOP on one channel suppresses future steps across channels for that location.
- Integration: publish blocked when consent path / content class invalid.
- RLS + regression tests guarding cross-tenant leakage of consent/suppression.
- Migration test: pre-existing sms rows still enforce correctly post-migration.

## Deployment Considerations

- Ship the multi-channel consent migration first, backward compatible, before channel plans switch to the gate.
- Feature-flag content validator strictness and frequency cap thresholds for tuning in staging.
- Emergency-halt control restricted to super-admin/operator initially.
- Blast-radius/spend caps default conservative; require explicit raise with approval.
- Runbooks: emergency halt procedure, consent-migration rollback, DNC scope escalation, region A2P/CASL.
- Alarms: gate block-rate anomalies, frequency-cap hit rate, halt invocations, PHI-detection hits.

## Future Extensibility

- Full patient preference center / channel opt-down (Gap 16).
- Formal consent-management workflows beyond opt-out + intake capture.
- Per-patient timezone as launch-blocking for US multi-zone clinics (Finding 12).
- Bilingual (EN/FR) consent + disclosure content (Gap 15).
- Content-governance approval workflow for scripts/templates (Gap 25).
