"""Real machine-translation engine — WRAP the provider, don't roll your own MT.

Decima's dependency policy: recreate the design in pure stdlib, but for capabilities
whose quality/liability lives OUTSIDE us — machine translation is the textbook case —
WRAP THE REAL ENGINE rather than reimplement it. TRANSLATE1 (`translate.py`) stays a
deterministic STUB (a fixed phrase map behind the effect seam); this module COMPLEMENTS
it by asking a REAL translation provider (a DeepL/Google-Translate-style HTTPS API) to
translate actual text. The provider is just an HTTPS API, so the real engine rides
stdlib `urllib` with ZERO pip dependencies: real engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / OIDC engine):
  - **text is UNTRUSTED DATA** — a translation is "here is what the text SAID in another
    tongue", never "do what the text said". The recorded `translation` cell flags the
    source AND the translated_text `instruction_eligible: False`: an injection-laced
    source ("ignore your instructions and …") is translated as DATA and never obeyed.
  - **HTTPS-only** — `translate` refuses to send the API key to a non-`https://`
    endpoint BEFORE any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `render` calls
    `broker.use_secret`, which applies the key INSIDE the broker (never returned, never
    logged, never on the Weft). The raw key never appears in a `translation` cell.
  - **fail closed** — a provider 4xx (unsupported lang), an unreachable endpoint, or a
    denied credential records NO cell and returns `{"denied": reason}`.
  - **ints only in signed content** — `char_count` is an int; no float ever lands on
    the Weft.
  - **transport seam** — `translate` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST; tests inject a fake, so the offline
    oracle exercises the full contract with NO network.

Composes public secrets / model / kernel APIs only. No core edit; does not touch
translate.py.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id

TRANSLATION = "translation"      # the on-Weft record of a provider translation (no key)


class TranslateEngineError(Exception):
    """A translation-engine failure — no `translation` cell may be recorded (fail
    closed). Covers a non-HTTPS endpoint, an unreachable/timed-out endpoint, and a
    provider 4xx/error (e.g. an unsupported language)."""


def _urllib_transport(url: str, headers: dict, body: str):
    """(Phase 2 · GO LIVE) FAIL-CLOSED default — the bare stdlib socket default is
    GONE: the armed wire guard (decima/wire.py) refuses ungated egress anyway, so
    `transport=None` on the live path now refuses HERE, first, with the sanctioned
    path named. Build the wire-gated transport via
    `live_wire.gated_transport(k, agent_cell, cap_id)`
    (a granted, Morta-approved egress capability) and inject it as `transport=`.
    Injected fake transports (the offline oracle, every test-mode path) never
    resolve to this default and are unaffected."""
    from decima import live_wire
    raise live_wire.NoGatedTransport(
        "translate_engine", hint='live_wire.gated_transport(k, agent_cell, cap_id)')


def translate(secret_key: str, request: dict, *, transport=None) -> dict:
    """Translate text by asking the REAL provider.

    `request` describes the job — `endpoint` (the provider's HTTPS translate URL),
    `text` (the UNTRUSTED source string), `source_lang` (optional; the provider may
    auto-detect), and `target_lang`. POSTs it over stdlib `urllib` and returns the
    provider's answer: {translated_text, detected_source_lang, provider_ref,
    char_count:int}. `char_count` is an int (no floats).

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `TranslateEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or
    a definite provider error (4xx / error body, e.g. an unsupported language) — the
    caller (`render`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(request.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise TranslateEngineError("refusing to send the API key to a non-HTTPS translate endpoint")

    text = str(request.get("text", ""))                      # UNTRUSTED DATA — sent, never obeyed
    target_lang = str(request.get("target_lang", ""))
    if not target_lang:
        raise TranslateEngineError("target_lang is required")

    payload = {
        "text": text,
        "source_lang": request.get("source_lang"),
        "target_lang": target_lang,
    }
    body = json.dumps(payload)
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise TranslateEngineError(f"translate endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise TranslateEngineError(f"unparseable translate response (status {status})")
    if status == 200 and "translated_text" in resp:
        translated_text = str(resp.get("translated_text"))
        # char_count is signed content — keep it an int (the provider's or ours).
        cc = resp.get("char_count")
        if not isinstance(cc, int) or isinstance(cc, bool):
            cc = len(translated_text)
        return {
            "translated_text": translated_text,
            "detected_source_lang": resp.get("detected_source_lang")
                                    or resp.get("source_lang")
                                    or request.get("source_lang"),
            "provider_ref": resp.get("provider_ref") or resp.get("id"),
            "char_count": int(cc),
        }
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise TranslateEngineError(f"provider rejected the translate request: {err}")   # definite error


def render(k, *, endpoint: str, request: dict, credential_handle: str, broker,
           agent_cell, transport=None) -> dict:
    """Get a REAL provider translation and record it on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `translate` on `request` against the
    HTTPS `endpoint`, and on success asserts a `translation` cell carrying
    source_lang/target_lang/translated_text/char_count/provider_ref. The source text and
    its translated_text are flagged `instruction_eligible: False` (UNTRUSTED DATA — a
    translation is a fact ABOUT what the text said, never something to obey); the raw key
    is NEVER on the cell. Returns
    {translation: <cell id>, provider_ref, char_count}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error
    (non-HTTPS, unreachable, provider 4xx) it records NO cell and returns
    {"denied": reason}."""
    req = {**request, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: translate(key, req, transport=transport))
    except TranslateEngineError as e:
        return {"denied": f"translate_engine: {e}"}          # fail closed — no translation cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    cc = result["char_count"]
    if not isinstance(cc, int) or isinstance(cc, bool):
        return {"denied": "translate_engine: char_count must be an int"}

    content = {
        "source_lang": result.get("detected_source_lang") or request.get("source_lang"),
        "target_lang": str(request.get("target_lang", "")),
        # The source was UNTRUSTED DATA and so is its translation — never an instruction.
        "source": str(request.get("text", "")),
        "translated_text": result["translated_text"],
        "char_count": int(cc),
        "provider_ref": result.get("provider_ref"),
        "engine": "provider",
        "instruction_eligible": False,
        "untrusted": True,
    }
    # Content-addressed by the translation body (re-translating identical inputs is
    # idempotent and a translation keeps one identity on the Log).
    cid = content_id({"translation": content})
    assert_content(k.weft, k.decima_agent_id, cid, TRANSLATION, content)
    return {
        "translation": cid,
        "provider_ref": content["provider_ref"],
        "char_count": content["char_count"],
    }


def translations(k) -> list:
    """All folded provider `translation` cells on the Weft."""
    return list(k.weave().of_type(TRANSLATION))
