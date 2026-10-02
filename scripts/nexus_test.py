#!/usr/bin/env python3
"""Nexus Test Suite CLI — call an agent function without Retell.

    export NEXUS_TEST_URL=https://staging.api.scalenexus.ai
    export NEXUS_TEST_KEY=...

    ./scripts/nexus_test.py health
    ./scripts/nexus_test.py targets
    ./scripts/nexus_test.py functions
    ./scripts/nexus_test.py call find_appointment_slots --location e2e \
        --arg provider_id=gt-3 --arg days=1

``--arg k=v`` is there so the common case needs no JSON quoting; ``--args '{...}'``
takes a full object when you need nesting. Values that parse as JSON are sent as
JSON (so ``days=1`` is a number, ``ids=[1,2]`` a list), otherwise as a string.

Standard library only — no install step, because a debugging tool you have to
set up is one you reach for less.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_ENV = "NEXUS_TEST_URL"
KEY_ENV = "NEXUS_TEST_KEY"

BOLD, DIM, RED, GREEN, YELLOW, RESET = (
    ("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m")
    if sys.stdout.isatty()
    else ("", "", "", "", "", "")
)


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    base = os.environ.get(BASE_ENV)
    key = os.environ.get(KEY_ENV)
    if not base or not key:
        sys.exit(f"{RED}Set {BASE_ENV} and {KEY_ENV} first.{RESET}")

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v1/test-suite{path}",
        data=data,
        method=method,
        headers={"X-Test-Suite-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null")
        except ValueError:
            return exc.code, raw.decode(errors="replace")
    except urllib.error.URLError as exc:
        sys.exit(f"{RED}Could not reach {base}: {exc.reason}{RESET}")


def _parse_arg(pair: str) -> tuple[str, object]:
    if "=" not in pair:
        sys.exit(f"{RED}--arg expects key=value, got {pair!r}{RESET}")
    key, _, raw = pair.partition("=")
    try:
        return key, json.loads(raw)
    except ValueError:
        return key, raw


def cmd_health(_args) -> int:
    status, body = _request("GET", "/health")
    print(json.dumps(body, indent=2))
    return 0 if status == 200 else 1


def cmd_functions(_args) -> int:
    status, body = _request("GET", "/functions")
    if status != 200:
        print(json.dumps(body, indent=2))
        return 1
    writes = body["writes_allowed"]
    print(
        f"{BOLD}{body['count']} functions{RESET}  "
        f"{DIM}env={body['environment']} writes={'on' if writes else 'off'}{RESET}\n"
    )
    for fn in body["functions"]:
        if fn["mutating"]:
            mark = f"{GREEN}WRITE{RESET}" if fn["callable_now"] else f"{YELLOW}WRITE{RESET}"
        else:
            mark = f"{DIM}read {RESET}"
        print(f"  {mark}  {BOLD}{fn['name']:<28}{RESET}{DIM}{fn['summary']}{RESET}")
    if not writes:
        print(f"\n{DIM}WRITE functions are refused: writes are off on this deployment.{RESET}")
    return 0


def cmd_targets(_args) -> int:
    status, body = _request("GET", "/targets")
    if status != 200:
        print(json.dumps(body, indent=2))
        return 1
    print(f"{BOLD}{body['count']} locations{RESET}\n")
    for t in body["targets"]:
        agent = "" if t["agent_bound"] else f"  {YELLOW}(no agent bound){RESET}"
        print(
            f"  {BOLD}{t['location']:<22}{RESET}{DIM}{t['pms'] or '-':<10}"
            f"{t['timezone'] or '-':<20}{t['name'] or ''}{RESET}{agent}"
        )
    return 0


def cmd_call(args) -> int:
    payload: dict = {"args": dict(args.args_json or {})}
    for pair in args.arg or []:
        key, value = _parse_arg(pair)
        payload["args"][key] = value
    if args.location:
        payload["location"] = args.location
    if args.institution:
        payload["institution"] = args.institution
    if args.agent_id:
        payload["agent_id"] = args.agent_id
    if args.allow_writes:
        payload["allow_writes"] = True

    status, body = _request("POST", f"/functions/{args.function}", payload)

    if status != 200 or not isinstance(body, dict):
        print(f"{RED}HTTP {status}{RESET}")
        print(json.dumps(body, indent=2))
        return 1

    target = body.get("target") or {}
    where = target.get("location_slug") or target.get("agent_id") or "?"
    head = f"{GREEN}ok{RESET}" if body.get("ok") else f"{RED}failed{RESET}"
    print(
        f"{BOLD}{body['function']}{RESET}  {head}  "
        f"{DIM}{where} · {target.get('pms') or '-'} · {body['duration_ms']}ms{RESET}\n"
    )
    if body.get("error"):
        print(f"{RED}{body['error']}{RESET}\n")
    print(json.dumps(body.get("result"), indent=2, default=str))
    return 0 if body.get("ok") else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="nexus_test.py",
        description="Call Nexus agent functions directly, without Retell.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="is the suite reachable, and what will it allow").set_defaults(fn=cmd_health)
    sub.add_parser("functions", help="list callable functions").set_defaults(fn=cmd_functions)
    sub.add_parser("targets", help="list clinics you can call against").set_defaults(fn=cmd_targets)

    call = sub.add_parser("call", help="run one function")
    call.add_argument("function")
    call.add_argument("--location", help="location slug (see `targets`)")
    call.add_argument("--institution", help="institution slug, when two clinics share a location slug")
    call.add_argument("--agent-id", dest="agent_id", help="Retell agent id, instead of --location")
    call.add_argument("--arg", action="append", metavar="K=V", help="repeatable")
    call.add_argument(
        "--args",
        dest="args_json",
        type=json.loads,
        metavar="JSON",
        help='full argument object, e.g. \'{"provider_id":"gt-3"}\'',
    )
    call.add_argument(
        "--allow-writes",
        action="store_true",
        help="required for functions that write into the practice's software",
    )
    call.set_defaults(fn=cmd_call)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
