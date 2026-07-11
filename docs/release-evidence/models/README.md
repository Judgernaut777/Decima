# WS3 — Live model-provider bounded qualification (evidence)

Lane branch: `qual/models`. Charter: `docs/DECIMA-0.3-RELEASE-QUALIFICATION.md` §WS3.

## Status

| Part | Status |
|---|---|
| Harness authored (`tests/live/`) | DONE |
| Non-live equivalent (deterministic + synthetic cloud stub) | **PASS** — `offline-qualification.json` |
| Redaction unit assertions (product redactor) | **PASS** |
| Normal CI still passes with no credential | **PASS** (307 baseline unchanged; live tests skip) |
| Live provider call | **BLOCKED-pending-operator-credential** |

No live provider credential exists on this qualification host, so the live call itself
is operator-gated. Everything around it is authored and executed; the live suite skips
cleanly (collection needs no key).

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
