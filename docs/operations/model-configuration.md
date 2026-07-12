# Model provider configuration (operator guide)

_Owner: WS3 (live model-provider qualification). This document names the environment
variables and secret-store references the live qualification reads. **It contains no
secret values, and none may ever be committed.**_

Decima's default, fully-tested model path is the **deterministic provider** — offline,
reproducible, no credential. Every automated test (the gate plus the non-live
qualification in `tests/live/test_provider_qualification_offline.py`) runs against it
and needs no key. A live provider is optional and operator-configured.

## Recommended local model

The default **recommended LOCAL model** is **`Qwen3.6-35B-A3B`**. This is forward
guidance for standing up an on-host OpenAI-compatible endpoint — it is a
recommendation, **not** a claim that this exact model was live-qualified on any
specific host. Routing is **model-agnostic**: the id it selects always comes from
`DECIMA_LIVE_MODEL` (or the registry), never from the recommendation. The
recommendation lives in exactly one place in code —
`decima.services.api.models_setup.RECOMMENDED_LOCAL_MODEL` — and config, diagnostics
(`decima.services.diagnostics.service.model_surface`), and the optional bench script
(`scripts/bench_local_provider.py`) all read it from there, so the literal is never
scattered. To move the recommendation, change that one constant.

You may run a **different** id than the recommendation (the endpoint decides what it
serves); set `DECIMA_LIVE_MODEL` to whatever the endpoint actually names. To measure
a configured local endpoint before relying on it:

```
DECIMA_LIVE_PROVIDER=local DECIMA_LIVE_MODEL=<served-id> \
DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080 \
python3 scripts/bench_local_provider.py
```

The bench probe skips cleanly (exit 0, nothing measured) when no endpoint is set.

## Capability-aware routing

Routing can select by **capability**, not name. Each catalogue entry carries bounded
integer capability scores (`reasoning_strength`, `coding`, `planning`,
`structured_reliability`, each `0..5`) plus `latency_class` and `cost_class` enum
tags. A `TaskSpec` may declare `required_capabilities` (a hard filter — an entry
scoring below the level is ineligible) and `preferred_capabilities` (a soft ranking
bias). With neither set, routing is **byte-identical** to capability-unaware routing.

These tags steer **selection only** and confer **no authority**: a model that
over-claims a capability can be *proposed* more often, but is never *permitted* more —
every effect still flows through the kernel's authorization + approval + receipt
chain, and a **sensitive/private task still routes local-only** regardless of what
capabilities any external model claims.

## Invariants a provider must not violate

- A model **proposes**; it never authorizes. A `ModelResponse` is inert DATA with no
  `execute`/`invoke`/`authorize` method and no capability, grant, principal, or key.
  Turning a proposal into an effect always goes through the kernel's authorization +
  approval + receipt chain, which `decima/models` does not import (invariant 4).
- A **sensitive/private task never leaves the box**: routing filters to local models
  before ranking, so it can never select a cloud provider (`sensitive_local_only`).
- **Secrets are applied by a broker at call time**, never stored on the provider
  object, embedded in code, placed in a prompt/context, or written to a log. The live
  adapters (`LocalProvider`, `CloudProvider`) make **no** network call by themselves
  and **fail closed** (`LiveTransportRequired`) when no transport seam is configured.

## Environment variables (names only)

The live qualification harness (`tests/live/harness.py`) reads exactly these. Set them
in the operator's shell or CI secret store — never in the repo.

| Variable | Meaning | Required |
|---|---|---|
| `DECIMA_LIVE_PROVIDER` | provider kind: `cloud` or `local` | yes, to run live |
| `DECIMA_LIVE_MODEL` | model id as the endpoint names it | yes, to run live |
| `DECIMA_LIVE_BASE_URL` | OpenAI-compatible base URL (e.g. `http://127.0.0.1:8080`) | yes, to run live |
| `DECIMA_LIVE_API_KEY` | **the secret value** the broker applies as `Authorization: Bearer …` | cloud only |
| `DECIMA_LIVE_TIMEOUT_S` | per-call timeout in seconds (int) | no; default `30` |

`DECIMA_LIVE_API_KEY` is the one variable that holds a secret. It is read **only** by
the `EnvSecretBroker` at call time, applied inside the broker, and never returned,
stored on an attribute, or logged. For a `local` provider it is not used at all (an
on-host endpoint needs no credential and nothing leaves the box).

### Secret-store reference (production)

In production the credential should come from the OS secret store rather than a raw
environment variable. The broker seam (`broker.use_secret(name, fn)`) is the single
integration point: back it with the platform keystore and pass the store's reference
**name** where the harness reads `DECIMA_LIVE_API_KEY`. The value never transits the
repo, a fixture, a log line, the browser, or a model context.

## Running the live qualification

Normal CI never runs it (the `live_provider` marker is skipped and collection needs no
key). To run it against one already-supported provider, supply your own values:

```
# a hosted OpenAI-compatible endpoint (cloud)
DECIMA_LIVE_PROVIDER=cloud \
DECIMA_LIVE_MODEL=<model-id> \
DECIMA_LIVE_BASE_URL=<https://endpoint> \
DECIMA_LIVE_API_KEY=<the-secret-value> \
PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider tests/live -v

# a purely local on-host endpoint (no credential leaves the box)
DECIMA_LIVE_PROVIDER=local \
DECIMA_LIVE_MODEL=<model-id> \
DECIMA_LIVE_BASE_URL=http://127.0.0.1:8080 \
PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider tests/live -v
```

`TESTENV` is the test dependency path from the release charter.

## What the live qualification asserts

The live suite drives the **same** harness the offline suite proves, against a real
transport:

1. **connectivity / routing** — the configured model is diagnostically available; a
   task routes to it; the decision records provider, model, reason codes, estimated
   cost, and task-sensitivity class; the answer returns through `ModelResponse`.
2. **structured proposal** — a bounded structured plan is validated against a schema;
   a malformed proposal is rejected / bounded-corrected and never auto-invoked.
3. **budget enforcement** — a deliberately small budget admits one call then blocks;
   the budget state (`spent`/`remaining`/`exhausted`) is inspectable.
4. **privacy** — a local-only task never selects the cloud provider and no request
   reaches it; a synthetic cloud-eligible task transmits only synthetic content.
5. **failure / fallback** — invalid credential, timeout, rate limit, unavailable
   model, and malformed response are surfaced, fall back bounded (no retry storm), do
   not widen authority, and never leak the secret; every attempt is recorded.
6. **secret handling** — the credential comes from the broker only; a redaction
   assertion over captured logs (using the shipping product redactor,
   `decima.services.diagnostics.service._redact_line`) confirms it never survives.

## Current status

- **Offline / non-live qualification: PASS** on this host (no credential, no network) —
  evidence in `docs/release-evidence/models/offline-qualification.json`.
- **Live call: BLOCKED-pending-operator-credential** — no live provider credential
  exists on the qualification host. Run the command above with a real credential to
  produce `docs/release-evidence/models/live-qualification.json`.
