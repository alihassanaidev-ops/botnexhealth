# Task Plan: Outbound 06 - Campaign Templates

## Goal
Provide reusable campaign templates that instantiate valid workflow definitions on top of the engine.

## Current Status
Mostly complete for Dev A's definition/template layer. Not product-live until Dev B compliance and channels exist.

## Completed
- [x] Appointment reminder template
- [x] Appointment confirmation template
- [x] Recall SMS 6-month template
- [x] Reactivation SMS/email 18-month template
- [x] Template list/get API
- [x] Template instantiate API
- [x] Schema validity tests

## Remaining
- [ ] Product/legal review of campaign classification
- [ ] Final copy approval for patient-facing message templates
- [ ] Wire real send handlers once Dev B channels are available
- [ ] Add channel-specific configuration when provisioning exists

## Dependencies
- Plan 12 compliance/consent.
- Plan 04 SMS and Plan 05 email before templates can truly send.
- Legal/product classification for recall/reactivation.

