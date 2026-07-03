# Findings: Outbound 05 — Outbound Email

## Resend HTTP pattern (email_notification_service.py:198-219)
```python
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
body = {"from": sender, "to": [recipient], "subject": subject, "text": text}
async with httpx.AsyncClient(timeout=15.0) as client:
    response = await client.post("https://api.resend.com/emails", headers=headers, json=body)
    if response.status_code >= 400:
        # raise
```
Plain text: omit "html" key, send "text" only.

## From-address format for Resend
Resend accepts "Name <email>" format for display name.
Build: f"{name} <{address}>" if name else address.

## Contact.email — encrypted property
`contact.email` decrypts from `email_encrypted`. May be None.

## Institution from-address (Plan 10)
`institution.email_from_address` (str | None), `institution.email_from_name` (str | None).
Fallback: `settings.resend_from_email` (platform-level).

## Template renderer (Plan 04)
`render_sms_body(template, contact, location, context)` handles `{{var}}` substitution.
Reuse for both subject_template and body_template.

## SendEmailNode fields
`subject_template`, `body_template`, `next_node_id`, `respect_quiet_hours`, `max_attempts`

## httpx already in project
Used by email_notification_service.py — no new dependency needed.
