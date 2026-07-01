"""Real weather engine — WRAP the provider, offline contract (data policy).

Policy: recreate the design in pure stdlib, but ask a REAL provider for EXTERNAL FACTS
rather than fabricate them. WEATHER1 (`weather.py`) stays a capture-and-stub lane;
`weather_engine.py` asks a REAL OpenWeather / Tomorrow.io-style HTTPS provider for the
actual current reading, over stdlib `urllib` (zero deps). This check drives it entirely
OFFLINE via an injected fake transport (the real `urllib` transport is never called), so
the oracle stays deterministic and network-free while proving the full contract:

  - success: an injected 200 reading with a FLOAT temperature → a `weather_reading` cell
    whose temp_dc / humidity_pct / wind_kph / precip_pct are INTS (not float, not bool)
    with the correct conversion (tenths of °C, whole percent, whole km/h); provider_ref
    present;
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (the fake
    transport is never called) — the API key never rides a cleartext wire;
  - fail closed: a provider 4xx / error → {"denied": ...} and NO `weather_reading` cell;
  - dispense-don't-disclose: the raw API key never appears in any event payload on the
    Weft (CRED1 applies it inside the broker), and NO float appears in the recorded cell.

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import weather_engine, secrets

API_KEY = "owm_live_OPENWEATHER_SUPER_SECRET_KEY"
ENDPOINT = "https://api.openweathermap.org/data/2.5/weather"


def _transport(calls, response):
    """A fake weather-provider transport: records each call and returns `response` (a
    (status, json) tuple) or invokes it if callable (to raise). No network."""
    def t(url, headers):
        calls.append({"url": url, "headers": headers})
        return response() if callable(response) else response
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def run(k, line):
    line("\n== REAL WEATHER ENGINE (wrapped provider, offline contract) — data policy ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("openweather", API_KEY, service="openweather")
    handle = broker.issue("openweather", _decima(kk), "read current weather")

    # An UNTRUSTED location by coordinates (micro-degrees ints — never a float on the Weft).
    location = {"lat_udeg": 51_507_400, "lng_udeg": -127_800}   # London-ish

    # 1. SUCCESS — provider returns FLOAT temp; we cross into ints and record them. ──────
    calls = []
    ok_resp = (200, {
        "main": {"temp": 21.5, "humidity": 63},        # 21.5°C float → temp_dc 215
        "wind": {"speed": 3.6},                          # 3.6 m/s → 12.96 → 13 km/h
        "weather": [{"main": "Clouds", "description": "broken clouds"}],
        "pop": 0.2,                                      # 20% precip
        "dt": 1_690_000_000,
        "id": 2643743, "name": "London",
    })
    res = weather_engine.reading(kk, endpoint=ENDPOINT, location=location,
                                 credential_handle=handle, broker=broker,
                                 agent_cell=_decima(kk), transport=_transport(calls, ok_resp))
    assert "weather_reading" in res, res
    assert res["temp_dc"] == 215 and res["condition"] == "Clouds", res
    assert res["provider_ref"] == 2643743, res
    assert len(calls) == 1 and calls[0]["url"].startswith(ENDPOINT), calls
    cell = kk.weave().get(res["weather_reading"]).content
    assert cell["temp_dc"] == 215 and cell["humidity_pct"] == 63, cell
    assert cell["wind_kph"] == 13 and cell["precip_pct"] == 20, cell
    assert cell["condition"] == "Clouds" and cell["provider_ref"] == 2643743, cell
    for fld in ("temp_dc", "humidity_pct", "wind_kph", "precip_pct", "observed_at"):
        v = cell[fld]                                    # ints only in signed content
        assert isinstance(v, int) and not isinstance(v, bool) and not isinstance(v, float), (fld, v)
    line("  success: injected 200 (float temp) → weather_reading cell; temp_dc / "
         "humidity_pct / wind_kph / precip_pct are ints (215 / 63 / 13 / 20); "
         "provider_ref present ✓")

    # 2. HTTPS-only — a non-HTTPS endpoint is refused before any request. ───────────────
    http_calls = []
    bad = weather_engine.reading(kk, endpoint="http://api.openweathermap.org/data/2.5/weather",
                                 location=location, credential_handle=handle, broker=broker,
                                 agent_cell=_decima(kk), transport=_transport(http_calls, ok_resp))
    assert "denied" in bad and "HTTPS" in bad["denied"], bad
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: a non-HTTPS endpoint is refused before the key is sent "
         "(transport never called) ✓")

    # 3. FAIL CLOSED — a provider 4xx / error → denied, NO weather_reading recorded. ────
    before = len(weather_engine.readings(kk))
    err_calls = []
    declined = weather_engine.reading(kk, endpoint=ENDPOINT, location=location,
                                      credential_handle=handle, broker=broker,
                                      agent_cell=_decima(kk),
                                      transport=_transport(err_calls, (401, {"message": "Invalid API key"})))
    assert "denied" in declined and "weather_engine" in declined["denied"], declined
    assert len(err_calls) == 1, "the request was made, but the 4xx must fail closed"
    assert len(weather_engine.readings(kk)) == before, "no weather_reading cell on a provider error"
    line("  fail closed: provider 4xx → {denied} and NO weather_reading cell recorded ✓")

    # 4. DISPENSE-DON'T-DISCLOSE — no raw key on the Weft, no float in the cell. ────────
    payloads = "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))
    assert API_KEY not in payloads, "the raw weather API key must never be written to the Weft"
    for key, val in cell.items():
        assert not isinstance(val, float), (key, val, "no float may land in a signed cell")
    line("  no raw API key on the Weft (CRED1 applies it inside the broker); "
         "no float in the recorded cell ✓")

    line("  → weather is wrapped, not fabricated: a real provider (over stdlib urllib, "
         "zero deps) reports the reading; Decima crosses floats into ints (tenths / "
         "percent / kph) on the Weft, holds the key in CRED1, refuses cleartext, and "
         "fails closed.")
