# Clinic Administrator Guide

**For:** clinic administrators at Kadri Dental (Open Dental / NexHealth) and
Olive Tree Dental (Tracker / GoTracker). **Applies to:** the ScaleNexus dashboard.

## 1. Signing in

- Go to the dashboard URL your practice was given and sign in with your email.
- You will be asked for a **second factor** (authenticator app, passkey, or an
  emailed code). This is required for every account; it protects patient data.
- Your session lasts eight hours of activity. "Sign out" ends it immediately.

## 2. Your clinic is set up for you

Your practice management system (PMS) is already connected:
- **Kadri Dental** connects through NexHealth.
- **Olive Tree Dental** connects through the GoTracker connector on the clinic PC.

Syncing (patients, appointments) happens automatically. You do not need to
trigger it.

## 3. Campaigns

A campaign contacts patients automatically (voice, SMS or email) according to
rules you set. Pick a template, choose who it applies to, review, then publish.
Nothing is sent until you publish.

**The templates available:**

| Campaign | What it does |
|---|---|
| Pre-Appointment Confirmation | Calls/messages patients before a visit to confirm or cancel. |
| Appointment Reminder | A short SMS reminder before the appointment (runs separately from Confirmation). |
| Overdue Recall | Finds patients due or overdue for a visit and invites them to book. |
| Sales Qualification | Takes website/ad leads, qualifies them, and books them. |
| Post-Op Follow-Up | Follows up after a completed visit. |

**Publishing a campaign:**
1. **Campaigns → New campaign**, choose a template.
2. **Audience** — which clinic, and who qualifies (e.g. "appointments in the next
   48 hours"). Preview the list before you commit; the count must look right.
3. **Channels and timing** — voice / SMS / email, and when (within operating
   hours; quiet hours are respected automatically).
4. **Review the launch checklist** — every item must be green.
5. **Publish.** You can pause a campaign at any time; paused campaigns stop
   contacting patients immediately.

## 4. Compliance built in

You do not have to manage these by hand, but you should know they apply:

- **Consent** — patients without consent are skipped.
- **Quiet hours** — messages outside permitted hours are held until the window
  opens.
- **Do-not-contact** — anyone on the DNC list is never contacted; you can add a
  patient to it from their record.
- **Opt-out** — a patient replying STOP (SMS) or saying "stop" (voice) is
  suppressed across all channels automatically.

**You must still obtain and record consent** for your patients as required by
your provincial rules; the platform enforces the rules, it does not seek consent.

## 5. Results

Each campaign shows confirmed / cancelled / booked counts and outcomes per
patient. Use **Campaigns → [campaign] → Analytics** for the per-campaign view and
the dashboard tiles for the clinic summary. Every outbound contact is logged with
its outcome.

## 6. GoTracker sync health (Olive Tree Dental)

Bookings only appear once the connector on the clinic PC has synced. If the
connector is offline or the PC is asleep, changes will not flow. Contact support
if bookings stop appearing in the dashboard.

## 7. Managing people and access

- **Contacts / Patients** — one directory; search by name, phone or email.
- **Users** — administrators can invite staff and set their role. Roles decide who
  can see patient details and who can change settings.
- Two-factor authentication cannot be turned off for any account.

## 8. Getting help

Note the date/time, the patient's name (not the record), and what you expected to
happen, then contact your support channel. Do not email screenshots containing
patient details unless the channel is approved for PHI.
