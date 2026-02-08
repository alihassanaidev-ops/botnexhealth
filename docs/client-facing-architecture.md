# Nexus Dental AI — Platform Architecture Overview

## For: Client Review

---

## What We're Building

A single AI-powered platform that works with **any dental practice management system (PMS)**. Whether your practice uses Dentrix, Eaglesoft, Open Dental, or any other system — our voice agent and dashboard work the same way.

---

## How It Works (Simple Version)

```
Your Practice
     |
     v
Your PMS (Dentrix, Eaglesoft, Open Dental, etc.)
     |
     v
Our Platform (one system, works with all PMSes)
     |
     ├── AI Voice Agent (answers calls, books appointments)
     ├── Dashboard (manage everything in one place)
     └── Universal API (connects everything together)
```

**You don't need to know or care which connector we use behind the scenes** (NexHealth, Sikka, or direct integration). You just use our platform.

---

## What This Means For You

### One Platform, Any PMS

- If you have **one practice** with Dentrix — it works.
- If you have **multiple locations** with different PMSes — they all work through the same dashboard.
- If you **switch PMSes** in the future — we reconnect you without rebuilding anything.

### Simple Setup Process

When you onboard, here's what happens:

1. **We connect to your PMS** — Our team handles the technical connection. You provide basic credentials (like you would for any integration).

2. **You configure your preferences** — Through a simple setup wizard:
   - Which appointment types do you offer? (Cleaning, New Patient Exam, etc.)
   - What are your providers' schedules? (Dr. Smith: Mon-Fri, 9am-5pm)
   - Which rooms/chairs are available?

3. **You're live** — The voice agent starts taking calls and booking appointments using your real availability.

> **Note:** Some PMSes require a few extra configuration steps (like setting appointment durations or linking room assignments). Our setup wizard guides you through exactly what's needed — nothing more, nothing less.

### The AI Voice Agent

Once set up, the voice agent can:

- Look up existing patients by name, phone, or date of birth
- Check real-time availability across your providers
- Book, reschedule, or cancel appointments
- Handle new patient intake
- All in natural conversation — patients talk to it like they'd talk to your front desk

The voice agent talks to our universal system, not directly to your PMS. This means:
- It works the same regardless of which PMS you use
- Updates to the agent benefit all practices immediately
- No PMS-specific voice scripts needed

---

## Multi-Location / Multi-Practice Support

Each practice location gets its own tenant account with:

- Its own PMS connection (can be different PMSes per location)
- Its own credentials (encrypted, isolated)
- Its own voice agent configuration
- Its own provider schedules and appointment types

But you manage them all from **one dashboard**.

| Location | PMS | Status |
|----------|-----|--------|
| Main Office | Dentrix (via NexHealth) | Connected |
| Downtown Branch | Eaglesoft (via Sikka) | Connected |
| New Location | Open Dental (direct) | Ready to connect |

---

## Security & Compliance

- **HIPAA Compliant** — All patient data encrypted in transit and at rest
- **Per-Practice Isolation** — One practice's data never touches another's
- **Encrypted Credentials** — All PMS API keys stored with AES-256-GCM encryption
- **Audit Trail** — Every action logged (who did what, when)
- **Auto-Logout** — Dashboard sessions expire after 15 minutes of inactivity
- **No Browser Storage** — Authentication tokens stored in memory only (not saved to disk)

---

## Future-Proof

Our adapter architecture means:

- **Adding a new PMS** = writing one connector, not rebuilding the system
- **PMS-specific features** are handled transparently — you don't see the complexity
- **Your voice agent, dashboard, and workflows stay the same** even as we add more PMS support
- **Sikka alone gives us access to 20+ PMSes** through a single integration
- **NexHealth covers another 20+ PMSes** through their platform

Between these two connectors, we already cover the vast majority of dental PMSes in the market. And adding direct integrations for specific PMSes is straightforward with our architecture.

---

## Summary

| Question | Answer |
|----------|--------|
| Can we support multiple PMSes? | Yes — already supporting NexHealth and Sikka ecosystems |
| Is the system modular? | Yes — each PMS has its own adapter, everything else is universal |
| Will adding new PMSes require a rebuild? | No — one new adapter file per PMS |
| Will our practice need to change anything if you add features? | No — your setup stays the same |
| Can we have different PMSes at different locations? | Yes — each location is independent |
| Is it HIPAA compliant? | Yes — encryption, audit logs, auto-logout, isolated tenants |
| Will the voice agent work the same across PMSes? | Yes — it talks to our universal API, not the PMS directly |

---

*Nexus Dental AI — One platform for every practice.*
