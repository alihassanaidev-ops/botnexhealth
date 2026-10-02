# Test Suite

Call an agent function directly and see what it returns. No Retell call, no
signed payload, no ECS Exec, no CloudWatch.

```bash
export NEXUS_TEST_URL=https://staging.api.scalenexus.ai
export NEXUS_TEST_KEY=…            # ask an operator; staging only

./scripts/nexus_test.py targets                      # which clinics can I call?
./scripts/nexus_test.py functions                    # what can I call?
./scripts/nexus_test.py call find_appointment_slots \
    --location e2e --arg provider_id=gt-3 --arg days=1
```

```
find_appointment_slots  ok  e2e · gotracker · 184ms

{ "slots": [ { "start": "2026-09-03T09:00:00-04:00", … } ] }
```

## What it is

One implementation, two doors. Retell's endpoint and this one dispatch through
the same `_function_registry`, with the same call context, so a green result
here means the real path is green.

It is deliberately **not** a parallel "testable version" of the handlers. Two
copies would drift, the drift would be silent, and a testing tool that lies is
worse than no tool.

The only thing Retell contributes at call time is *which agent is speaking* —
held in a ContextVar. This route sets it from `--location` (a slug, resolved to
the clinic's bound agent) or `--agent-id`, and everything downstream behaves
exactly as it does on a live call.

## Where it runs

**Staging and local only.** Mounting requires two independent conditions:

| Condition | Setting |
|---|---|
| A key is configured | `TEST_SUITE_API_KEY` (or `TEST_SUITE_API_KEY_FILE`) |
| Not production | `APP_ENV` is not `production`/`prod` |

Both are required, so setting the key in production by accident still mounts
nothing — in production these paths return 404 because they do not exist, not
because a check rejected them.

## Reads and writes

Nine functions read. Six write into the practice's own software:

```
book_appointment · cancel_appointment · confirm_appointment
create_patient   · reschedule_appointment · reschedule_appointment_v2
```

A write needs **both** `TEST_SUITE_ALLOW_WRITES=true` on the deployment and
`--allow-writes` on the request. Neither alone is enough — a debugging tool
should not be one typo away from booking a real patient. Point writes at a test
tenant, never a live clinic.

That list is derived from the idempotency registry rather than written out
again, so a new writing function is refused here the moment it is guarded
there. A test asserts the two cannot drift.

## Endpoints

All under `/api/v1/test-suite`, all requiring `X-Test-Suite-Key`, all rate
limited to 60/minute.

| | |
|---|---|
| `GET /health` | reachable? which environment? are writes on? |
| `GET /functions` | every function, whether it writes, whether it is callable now |
| `GET /targets` | clinics available, their PMS and timezone, whether an agent is bound |
| `POST /functions/{name}` | run it |

`POST` body: `args`, plus `location` **or** `agent_id`, plus optional
`institution` and `allow_writes`.

Location slugs are unique *within* an institution, not globally — two practices
can each have a `main`. When a slug matches more than one, the call is refused
with the candidate institutions listed rather than quietly picking one, because
testing the wrong clinic and being told it worked is the worst outcome here.
Add `--institution` to disambiguate.

## Reading a result

```jsonc
{
  "function": "lookup_patient",
  "ok": false,
  "mutating": false,
  "duration_ms": 212,
  "target": { "location_slug": "e2e", "pms": "gotracker", … },
  "result": { "error": "Could not resolve institution + location" },
  "error": null
}
```

`ok` is false for **both** a raised exception (in `error`) and an in-band
`{"error": …}` result, because handlers signal failure both ways and you care
about the distinction less than about the fact.

A handler that raises returns **200 with `ok: false`**, not a 500 — the
exception type and message are the thing you came to see, and burying them in a
500 would send you back to CloudWatch.

## Privacy

Every call is audited with the function, tenant, duration and **argument names
only**. Argument values and results are never logged: `lookup_patient` takes a
name and date of birth and returns a patient. Responses go to the caller and
nowhere else.
