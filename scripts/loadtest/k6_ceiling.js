// Load-test: find the ceiling of the loadtest stack (NOT prod).
// v2: explicit per-request tags (so thresholds actually get samples and
// abortOnFail fires at the knee) + gentler read ramp to bracket the knee.
//   - reads:  GET /readyz   (DB + Redis ping)  -> raw throughput ceiling
//   - logins: POST /api/auth/login (Argon2id)  -> CPU/auth ceiling
//
//   k6 run -e BASE_URL=http://<alb-dns> \
//          -e LOGIN_EMAIL=staff@<slug>.dev -e LOGIN_PASSWORD='LocalDev123!' \
//          scripts/loadtest/k6_ceiling.js

import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL;
const LOGIN_EMAIL = __ENV.LOGIN_EMAIL;
const LOGIN_PASSWORD = __ENV.LOGIN_PASSWORD;

export const options = {
  discardResponseBodies: true,
  scenarios: {
    reads: {
      executor: 'ramping-arrival-rate',
      exec: 'readScenario',
      startRate: 50,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 2000,
      stages: [
        { target: 100, duration: '1m' },
        { target: 250, duration: '1m' },
        { target: 500, duration: '1m' },
        { target: 1000, duration: '2m' },
        { target: 2000, duration: '2m' },
      ],
    },
    logins: {
      executor: 'ramping-arrival-rate',
      exec: 'loginScenario',
      startRate: 5,
      timeUnit: '1s',
      preAllocatedVUs: 50,
      maxVUs: 600,
      startTime: '7m30s', // after the read ramp, so CPU isn't shared
      stages: [
        { target: 25, duration: '1m' },
        { target: 75, duration: '1m' },
        { target: 150, duration: '2m' },
      ],
    },
  },
  thresholds: {
    // Tagged per-request below, so these sub-metrics get real samples and abort
    // the moment the knee is crossed.
    'http_req_duration{ep:read}': [{ threshold: 'p(95)<800', abortOnFail: true, delayAbortEval: '15s' }],
    'http_req_failed{ep:read}': [{ threshold: 'rate<0.01', abortOnFail: true, delayAbortEval: '15s' }],
    'http_req_duration{ep:login}': [{ threshold: 'p(95)<2000', abortOnFail: true, delayAbortEval: '15s' }],
    'http_req_failed{ep:login}': [{ threshold: 'rate<0.02', abortOnFail: true, delayAbortEval: '15s' }],
  },
};

export function readScenario() {
  const res = http.get(`${BASE_URL}/readyz`, { tags: { ep: 'read' } });
  check(res, { 'readyz 200': (r) => r.status === 200 });
}

export function loginScenario() {
  const res = http.post(
    `${BASE_URL}/api/auth/login`,
    JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PASSWORD }),
    { headers: { 'Content-Type': 'application/json' }, tags: { ep: 'login' } },
  );
  check(res, { 'login not 5xx': (r) => r.status < 500 });
}
