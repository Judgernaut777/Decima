# WS3 — Live model-provider bounded qualification (evidence)

Lane branch: `qual/models`. Charter: `docs/DECIMA-0.3-RELEASE-QUALIFICATION.md` §WS3.

## Status

| Part | Status |
|---|---|
| Harness authored (`tests/live/`) | DONE |
| Non-live equivalent (deterministic + synthetic cloud stub) | **PASS** — `offline-qualification.json` |
| Redaction unit assertions (product redactor) | **PASS** |
| Normal CI still passes with no credential | **PASS** (307 baseline unchanged; live tests skip) |
| Live provider call | **BLOCKED-pending-operator-credential** — superseded, see below |

No live provider credential exists on this qualification host, so the live call itself
is operator-gated. Everything around it is authored and executed; the live suite skips
cleanly (collection needs no key).

> **Superseded (verified against the tree, 2026-07-27).** The live row is no longer BLOCKED
> and the `307 baseline` above is a lane-time figure (the in-process suite now collects
> **1269** tests). A **real local** model — llama.cpp Qwen3-30B-A3B on `127.0.0.1:8080`,
> OpenAI-compatible — is selected by the product's own routing and passes the app-path live
> suite; the evidence is `app-path-live-qualification.json`, `live-qualification.json` (which
> this file says is emitted only by a real live run — it exists) and
> `shell-driven-live-routing.md`, and the audit records it as finding **I-2 resolved**
> (`../audit/0.3-audit.md`, addendum 2026-07-12). No cloud credential is used; the
> deterministic offline provider remains the default when `DECIMA_LIVE_*` is unset. The lane
> table is left as written rather than edited to stay true — see the note in
> `scripts/check_release_metadata.py` on why release evidence must stop tracking main.

## Files

- `tests/live/harness.py` — provider-agnostic driver for all six checks + a real
  OpenAI-compatible transport and an env/secret-store broker.
- `tests/live/test_provider_qualification_offline.py` — the non-live suite (normal CI).
- `tests/live/test_provider_qualification_live.py` — the `live_provider`-marked suite.
- `docs/operations/model-configuration.md` — env-var names (no values) + reproduce.
- `offline-qualification.json` — machine-readable evidence emitted by the offline suite.
- `live-qualification.json` — emitted only when an operator runs the live suite.

## Reproduce

Non-live (this host):

```
PYTHONPATH="$TESTENV:$PWD" python3 -m pytest tests/live -q
```

Live (operator supplies values; names only shown here):

```
DECIMA_LIVE_PROVIDER=cloud DECIMA_LIVE_MODEL=<id> DECIMA_LIVE_BASE_URL=<url> \
DECIMA_LIVE_API_KEY=<secret> \
PYTHONPATH="$TESTENV:$PWD" python3 -m pytest -m live_provider tests/live -v
```

`TESTENV` is the test dependency path from the charter's environment section.
