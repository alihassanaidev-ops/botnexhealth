# Twilio Browser Phone QA

Tiny local helper for receiving Retell test calls in a browser.

Flow with a single ngrok tunnel:

```text
Retell calls Twilio US/Canada number
Twilio incoming-call webhook hits this Python app
Python returns TwiML <Dial><Client>hammad</Client></Dial>
Browser tab rings through Twilio Voice JS SDK
Retell post-call webhook also hits this Python app
Python forwards Retell webhook to the main local app on port 8000
```

## Required Twilio Setup

You need:

- Twilio Account SID
- Twilio API Key SID
- Twilio API Key Secret
- A Twilio phone number that can receive calls from Retell

The API key is not the Auth Token. Create it in Twilio Console under API keys.

## Run Locally

From repo root:

```bash
cp twilio_browser_phone/.env.example twilio_browser_phone/.env
```

Fill in `twilio_browser_phone/.env`, then export it:

```bash
set -a
source twilio_browser_phone/.env
set +a
uv run uvicorn twilio_browser_phone.app:app --host 0.0.0.0 --port 8010
```

In another terminal:

```bash
ngrok http 8010
```

Open the local browser phone, not the ngrok URL:

```text
http://localhost:8010/
```

Click **Register browser phone**. The `/token` endpoint is intentionally
blocked for public ngrok requests so live Twilio browser tokens are not minted
through the public tunnel.

Use the same ngrok base URL for the Retell test agent webhook:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.dev/api/v1/retell/webhook
```

## Configure Twilio Number

In Twilio Console, open the US/Canada phone number.

Set incoming voice webhook:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.dev/voice/incoming
```

Optional status callback:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.dev/voice/status
```

Method can be `POST`.

## Test

Use the Twilio number as the Retell workflow `to_number`.

Expected:

```text
Retell creates outbound call
Twilio receives the call
Browser tab rings
You answer in browser
Retell agent talks to you
Retell webhook returns to the main app
Workflow resumes
```

Keep this one ngrok tunnel open during testing. The helper proxies Retell
webhooks to the main local app through `MAIN_APP_BASE_URL`.
