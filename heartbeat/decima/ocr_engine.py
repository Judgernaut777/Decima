"""Real OCR / document-extraction engine — WRAP the provider, keep the bytes off the Weft.

Decima's policy: recreate the design in pure stdlib, but for high-liability / high-fidelity
externals WRAP THE REAL ENGINE rather than reimplement it. Turning pixels into text is such
an external — a production-grade OCR model (AWS Textract / Google Document AI-style) is a
real HTTPS API, so the real engine rides stdlib `urllib` with ZERO pip dependencies. OCR1
(`ocr.py`) stays the DETERMINISTIC STUB transcriber that keeps the oracle reproducible; this
module COMPLEMENTS it by asking a REAL OCR provider to extract text from an actual document
and recording only a content-addressed REFERENCE + the extracted text on the Weft.

The invariants that make this safe:
  - **document bytes never on the Weft** — the payload is uploaded to the provider; we
    compute a local content digest (`hashing.blob_id`) BEFORE the call and land only the
    digest + the extracted text (as DATA), never the raw document bytes.
  - **extracted text is UNTRUSTED DATA, never an instruction** — text the OCR engine returns
    came from OUTSIDE the trust boundary (an attacker can print "ignore your instructions"
    onto a scan). It is stored `instruction_eligible=False`: recallable/citable DATA, NEVER
    obeyed, never executed — the one law that makes an OCR worker safe (A1/B1).

GUARDRAILS (mirroring the tax engine / cloud-storage engine):
  - **HTTPS-only** — `extract` refuses to send the API key to a non-`https://` endpoint
    BEFORE the key touches the wire (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker; `read_document`
    calls `broker.use_secret`, which applies the key INSIDE the broker (never returned,
    never logged, never on the Weft). The raw key never appears in an `ocr_result` cell.
  - **fail closed** — a provider 4xx (unsupported format), an unreachable/timed-out
    endpoint, a non-HTTPS endpoint, or a denied credential records NO `ocr_result` cell and
    returns `{"denied": reason}`.
  - **ints only in signed content** — `page_count` / `char_count` / `confidence_pct` are
    ints (confidence is a whole percent 0-100, NOT a float); no float ever lands on the Weft.
  - **transport seam** — `extract` takes a `transport(url, headers, body) -> (status, json)`;
    the default is a real `urllib` POST; tests inject a fake, so the offline oracle exercises
    the full contract with NO network.

Composes public secrets / model / hashing / kernel APIs only. No core edit; does not touch
`ocr.py`.
"""
import json

from decima.model import assert_content
from decima.hashing import content_id, blob_id, nfc

OCR_RESULT = "ocr_result"    # the on-Weft record of a provider extraction (no key, no bytes)


class OcrEngineError(Exception):
    """An OCR-engine failure — no `ocr_result` may be recorded (fail closed). Covers a
    non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider 4xx/error."""


def _urllib_transport(url: str, headers: dict, body):
    """The real transport: a stdlib `urllib` POST (no pip dep). On success returns
    (status, parsed_json) with the provider's extraction. A 4xx/5xx carries an error body
    (returned, not raised), so `extract` decides success vs. definite error; a transport-level
    failure (DNS, timeout, TLS) raises — `extract` maps that to OcrEngineError (unreachable).
    Never used by the offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry an error body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


def _require_int(name: str, v):
    """Guard that a value the engine will sign onto the Weft is an int (never float/bool)."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise OcrEngineError(f"{name} must be an int, got {v!r}")
    return int(v)


def _confidence_pct(resp: dict) -> int:
    """Normalize the provider's confidence to a whole percent int in [0, 100] (NEVER a
    float on the Weft). Accepts `confidence_pct` (already a percent) or `confidence`
    (a 0.0-1.0 fraction, common for Textract/Document AI); a missing value → 0."""
    if "confidence_pct" in resp:
        pct = resp.get("confidence_pct")
    else:
        raw = resp.get("confidence", 0)
        # A 0.0-1.0 fraction is scaled to a percent; a 0-100 number is taken as-is.
        pct = raw * 100 if isinstance(raw, float) and 0.0 <= raw <= 1.0 else raw
    try:
        pct = int(round(float(pct)))
    except (TypeError, ValueError):
        pct = 0
    return max(0, min(100, pct))


def _document_bytes(document: dict) -> bytes:
    """The document payload as bytes — a `bytes`/`bytearray` passes through, a `str` is
    UTF-8 encoded; a bare `document_ref` string (a pointer, no inline bytes) is content-
    addressed by the reference itself. The raw bytes are hashed locally and shipped to the
    provider — they never land on the Weft."""
    payload = document.get("bytes", document.get("payload"))
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    ref = document.get("document_ref")
    if isinstance(ref, str) and ref:
        return ref.encode("utf-8")
    raise OcrEngineError("document needs raw bytes (`bytes`/`payload`) or a `document_ref`")


def extract(secret_key: str, document: dict, *, transport=None) -> dict:
    """Extract text from one document with the REAL OCR provider over stdlib `urllib`.

    `document` describes the input — `endpoint` (the provider's HTTPS OCR URL), the raw
    document as `bytes`/`payload` (bytes or str) or a `document_ref` pointer, and an optional
    `mime`/`format`. The local content digest is computed with `hashing.blob_id` BEFORE the
    call; the bytes are then POSTed to `endpoint`. Returns
    {text, provider_ref (job id), page_count:int, char_count:int, digest, confidence_pct:int}
    — `char_count` is the length of the extracted text, `confidence_pct` is a whole percent
    (0-100, never a float). The document BYTES are never returned or recorded — only the
    digest + the extracted text (which is UNTRUSTED DATA).

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `OcrEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or a definite
    provider error (4xx / error body) — the caller (`read_document`) fails closed."""
    transport = transport or _urllib_transport

    endpoint = str(document.get("endpoint", ""))
    if not endpoint.startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise OcrEngineError("refusing to send the API key to a non-HTTPS OCR endpoint")

    body = _document_bytes(document)
    digest = blob_id(body, kind="blob")                      # content-address BEFORE the call
    mime = str(document.get("mime", document.get("format", "application/octet-stream")))

    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": mime,
        "Accept": "application/json",
        "Content-Length": str(len(body)),
    }
    try:
        status, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise OcrEngineError(f"OCR endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise OcrEngineError(f"unparseable OCR response (status {status})")
    if status == 200 and "text" in resp:
        text = resp.get("text")
        text = text if isinstance(text, str) else str(text)
        return {
            "text": text,                                    # UNTRUSTED DATA, never obeyed
            "provider_ref": resp.get("provider_ref") or resp.get("job_id") or resp.get("id"),
            "page_count": _require_int("page_count", resp.get("page_count", 0)),
            "char_count": len(text),                         # local, authoritative int
            "digest": digest,
            "confidence_pct": _confidence_pct(resp),
        }
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    raise OcrEngineError(f"provider rejected the document: {err}")   # definite error


def read_document(k, *, endpoint: str, document: dict, credential_handle: str, broker,
                  agent_cell, transport=None) -> dict:
    """Extract a document with the REAL OCR provider and record the result on the Weft (fail closed).

    Resolves the provider API key via CRED1 (`broker.use_secret`, which applies the key
    INSIDE the broker and never discloses it), runs `extract` against the HTTPS `endpoint`,
    and on success asserts an `ocr_result` cell carrying provider_ref / page_count (int) /
    char_count (int) / confidence_pct (int) / digest AND the extracted `text` flagged
    `instruction_eligible=False` (UNTRUSTED DATA) — NEVER the API key, NEVER the raw document
    bytes. Returns {ocr_result: <cell id>, provider_ref, char_count, digest}.

    On a denied credential (revoked/unauthorized/over-budget) or any engine error (non-HTTPS,
    unreachable, provider 4xx) it records NO cell and returns {"denied": reason}."""
    doc = {**document, "endpoint": endpoint}
    try:
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: extract(key, doc, transport=transport))
    except OcrEngineError as e:
        return {"denied": f"ocr_engine: {e}"}                # fail closed — no cell
    if "denied" in r:
        return {"denied": r["denied"]}                       # credential handle denied
    result = r["ok"]

    content = {
        "text": nfc(result["text"]),                         # the extracted transcript, as DATA
        "provider_ref": result.get("provider_ref"),
        "page_count": _require_int("page_count", result["page_count"]),
        "char_count": _require_int("char_count", result["char_count"]),
        "confidence_pct": _require_int("confidence_pct", result["confidence_pct"]),
        "digest": result["digest"],
        "engine": "wrapped-provider",
        "trusted": False,                                    # a scanned doc is untrusted, always
        "instruction_eligible": False,                       # OCR1 law: transcript is DATA, never obeyed
        "recallable": True, "citable": True,
        "disclosed": False,                                  # neither the key nor the raw bytes
    }
    # Content-addressed by digest + provider_ref: re-extracting identical bytes is idempotent
    # and one document keeps one identity on the Log.
    cid = content_id({"ocr_result": {"digest": content["digest"],
                                     "provider_ref": content["provider_ref"]}})
    assert_content(k.weft, k.decima_agent_id, cid, OCR_RESULT, content)
    return {
        "ocr_result": cid,
        "provider_ref": content["provider_ref"],
        "char_count": content["char_count"],
        "digest": content["digest"],
    }


def results(k) -> list:
    """All folded `ocr_result` cells on the Weft."""
    return list(k.weave().of_type(OCR_RESULT))
