"""Tiny Twilio browser phone for local QA.

This is intentionally separate from the production FastAPI app. It lets a Twilio
phone number forward incoming calls to a browser using Twilio Voice JS SDK.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.rest import Client as TwilioRestClient
from twilio.twiml.voice_response import Client, Dial, VoiceResponse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="Twilio Browser Phone QA")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _env(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise HTTPException(status_code=500, detail=f"Missing env var: {name}")
    return value


def _client_identity() -> str:
    return _env("TWILIO_CLIENT_IDENTITY", default="hammad") or "hammad"


def _main_app_base_url() -> str:
    return (os.getenv("MAIN_APP_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _is_public_forwarded_request(request: Request) -> bool:
    forwarded_host = request.headers.get("x-forwarded-host") or ""
    return bool(forwarded_host and not forwarded_host.startswith(("localhost", "127.0.0.1")))


@app.get("/token")
async def token(request: Request) -> JSONResponse:
    """Return a Twilio Voice JS SDK access token for the browser."""
    if _is_public_forwarded_request(request) and os.getenv("ALLOW_PUBLIC_TOKEN") != "true":
        raise HTTPException(status_code=403, detail="Open the browser phone locally to mint tokens.")

    account_sid = _env("TWILIO_ACCOUNT_SID")
    api_key_sid = _env("TWILIO_API_KEY_SID")
    api_key_secret = _env("TWILIO_API_KEY_SECRET")
    identity = _client_identity()

    access_token = AccessToken(
        account_sid,
        api_key_sid,
        api_key_secret,
        identity=identity,
        ttl=int(os.getenv("TWILIO_ACCESS_TOKEN_TTL", "3600")),
    )
    grant = VoiceGrant(
        incoming_allow=True,
        outgoing_application_sid=os.getenv("TWILIO_TWIML_APP_SID") or None,
    )
    access_token.add_grant(grant)

    return JSONResponse({"identity": identity, "token": access_token.to_jwt()})


@app.get("/ice-servers")
async def ice_servers(request: Request) -> JSONResponse:
    """Return short-lived Twilio TURN/STUN servers for browser WebRTC media."""
    if _is_public_forwarded_request(request) and os.getenv("ALLOW_PUBLIC_TOKEN") != "true":
        raise HTTPException(status_code=403, detail="Open the browser phone locally for ICE servers.")

    account_sid = _env("TWILIO_ACCOUNT_SID")
    auth_token = _env("TWILIO_AUTH_TOKEN")
    client = TwilioRestClient(account_sid, auth_token)
    token = client.tokens.create(ttl=int(os.getenv("TWILIO_ICE_TOKEN_TTL", "3600")))
    return JSONResponse({"iceServers": token.ice_servers})


@app.api_route("/voice/incoming", methods=["GET", "POST"])
async def incoming_call(request: Request) -> Response:
    """TwiML endpoint for the Twilio number's incoming-call webhook.

    Configure the Twilio phone number:
      A call comes in -> Webhook -> https://YOUR-NGROK/voice/incoming
    """
    form = await request.form() if request.method == "POST" else {}
    caller = form.get("From") or request.query_params.get("From") or "unknown"
    called = form.get("To") or request.query_params.get("To") or "unknown"

    response = VoiceResponse()
    dial = Dial(
        answer_on_bridge=True,
        caller_id=called if str(called).startswith("+") else None,
        timeout=int(os.getenv("TWILIO_CLIENT_DIAL_TIMEOUT", "25")),
    )
    dial.append(Client(_client_identity()))
    response.append(dial)

    print(f"Routing incoming Twilio call from {caller} to browser client {_client_identity()}")
    return Response(content=str(response), media_type="application/xml")


@app.api_route("/voice/status", methods=["GET", "POST"])
async def voice_status(request: Request) -> dict[str, str | None]:
    """Optional Twilio status callback target for debugging."""
    data = dict(await request.form()) if request.method == "POST" else dict(request.query_params)
    print("Twilio status callback:", data)
    return {
        "call_sid": data.get("CallSid"),
        "call_status": data.get("CallStatus"),
        "direction": data.get("Direction"),
    }


@app.api_route("/voice/say", methods=["GET", "POST"])
async def say_test() -> Response:
    """TwiML used by /debug/call-browser-say to isolate Twilio browser audio."""
    response = VoiceResponse()
    response.say(
        "This is a Twilio browser audio test. If you can hear this, Twilio media is working.",
        voice="alice",
    )
    response.pause(length=1)
    response.say("Test complete.", voice="alice")
    return Response(content=str(response), media_type="application/xml")


@app.post("/debug/call-browser-say")
async def call_browser_say(request: Request) -> JSONResponse:
    """Place a Twilio-only call to the registered browser client.

    This bypasses Retell completely. If this call is also silent, the issue is
    Twilio Voice JS/WebRTC/network/device, not Retell or ScaleNexus workflow code.
    """
    if _is_public_forwarded_request(request) and os.getenv("ALLOW_PUBLIC_TOKEN") != "true":
        raise HTTPException(status_code=403, detail="Run this debug call locally.")

    public_base_url = os.getenv("PUBLIC_BASE_URL")
    if not public_base_url:
        forwarded_host = request.headers.get("x-forwarded-host")
        scheme = request.headers.get("x-forwarded-proto", "https")
        public_base_url = f"{scheme}://{forwarded_host}" if forwarded_host else None
    if not public_base_url:
        raise HTTPException(status_code=500, detail="Missing PUBLIC_BASE_URL")

    account_sid = _env("TWILIO_ACCOUNT_SID")
    auth_token = _env("TWILIO_AUTH_TOKEN")
    from_number = _env("TWILIO_TEST_FROM_NUMBER")
    identity = _client_identity()

    client = TwilioRestClient(account_sid, auth_token)
    call = client.calls.create(
        to=f"client:{identity}",
        from_=from_number,
        url=f"{public_base_url.rstrip('/')}/voice/say",
        method="POST",
    )
    return JSONResponse({"call_sid": call.sid, "to": f"client:{identity}"})


_PROXYABLE_WEBHOOK_PREFIXES = (
    "retell/",
    "nexhealth/webhooks/",
    "gotracker/webhooks/",
    "twilio/webhooks/",
)


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH"])
async def proxy_main_app_webhook(path: str, request: Request) -> Response:
    """Forward provider webhook traffic to the main local app.

    This lets one ngrok tunnel serve both:
      * Provider webhooks -> selected /api/v1/... webhook paths -> main app on port 8000
      * Twilio incoming voice -> /voice/incoming -> browser client
    """
    if not any(path.startswith(prefix) for prefix in _PROXYABLE_WEBHOOK_PREFIXES):
        raise HTTPException(status_code=404, detail="Not found")

    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        not in {
            "host",
            "content-length",
            "connection",
            "accept-encoding",
        }
    }
    target_url = f"{_main_app_base_url()}/api/v1/{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        upstream = await client.request(
            request.method,
            target_url,
            content=body,
            headers=headers,
            params=request.query_params,
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
