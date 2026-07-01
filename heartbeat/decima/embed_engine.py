"""Real embedding engine — WRAP a hosted embeddings provider over stdlib urllib.

Decima's discovery lane (`discovery.py`) ranks capabilities by a DETERMINISTIC LEXICAL
embedding — a zero-dependency stand-in that occupies the exact seam where a real vector
model wraps later. This module is that real model: it asks a hosted embeddings provider
(an OpenAI / Voyage / Cohere-style HTTPS API) to turn text into FLOAT vectors, over
stdlib `urllib` with ZERO pip dependencies. Real engine, still pure-stdlib.

GUARDRAILS (mirroring the tax engine / Stripe rail / OIDC engine):
  - **HTTPS-only** — `embed_texts` refuses to send the API key to a non-`https://`
    endpoint before any request is made (never leak the key in cleartext).
  - **key via CRED1** — the provider API key lives in the secrets broker;
    `broker_embedder` returns a callable that goes through `broker.use_secret`, which
    applies the key INSIDE the broker (never returned, never logged, never on the Weft).
  - **fail closed** — a provider 4xx / declared error raises `EmbedEngineError`; an
    unreachable/timed-out endpoint raises `EmbedEngineError` ("unreachable").
  - **transport seam** — `embed_texts` takes a `transport(url, headers, body) -> (status,
    json)`; the default is a real `urllib` POST; tests inject a fake, so the offline
    oracle exercises the full contract with NO network.

CRITICAL — FLOATS: a real embedder returns FLOAT vectors. The Weft forbids floats in
signed content, so these vectors are used ONLY IN MEMORY for ranking — they are NEVER
written to a cell. The only value that lands on the Weft is an INT similarity SCORE,
computed by `cosine_int` (SCALE×cosine, integer-rounded). Float vectors in; int score
out; nothing float ever crosses onto the Log.

Composes public secrets / kernel APIs only. No core edit; does not touch discovery's
existing lexical path (it wires in additively as an optional embedder seam).
"""
import json
import math

# The similarity scale: cosine ∈ [-1,1] is projected to an INT score in [0, SCALE].
# Matches discovery.SCALE so scores are comparable across the lexical / real paths.
SCALE = 1000


class EmbedEngineError(Exception):
    """An embedding-engine failure — nothing may be recorded (fail closed). Covers a
    non-HTTPS endpoint, an unreachable/timed-out endpoint, and a provider 4xx/error."""


def _urllib_transport(url: str, headers: dict, body: str):
    """The real transport: a stdlib `urllib` POST (no pip dep). Returns
    (status_code, parsed_json). A 4xx/5xx surfaces as (code, error-json) rather than
    raising, so `embed_texts` decides success vs. definite error. A transport-level
    failure (DNS, timeout, TLS) raises — `embed_texts` maps that to `EmbedEngineError`
    (unreachable). Never used by the offline oracle (tests inject a fake transport)."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                       # 4xx/5xx carry a JSON body
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": f"http {e.code}"}


# The provider's default embeddings endpoint (OpenAI-style). Overridable per call.
DEFAULT_ENDPOINT = "https://api.openai.com/v1/embeddings"


def embed_texts(secret_key: str, texts, *, transport=None,
                model: str = "text-embedding-3-small",
                endpoint: str = DEFAULT_ENDPOINT) -> list:
    """Embed `texts` into FLOAT vectors by asking the REAL hosted provider.

    POSTs {"input": texts, "model": model} to the provider's HTTPS embeddings endpoint
    over stdlib `urllib` and returns the list of float vectors — one per input text, in
    input order. These vectors are IN-MEMORY ONLY (used for ranking); a real embedding is
    made of floats and MUST NEVER be written to a cell.

    HTTPS-only: a non-`https://` endpoint is refused BEFORE the key touches the wire.
    Raises `EmbedEngineError` on a non-HTTPS endpoint, an unreachable endpoint, or a
    definite provider error (4xx / error body) — the caller fails closed."""
    transport = transport or _urllib_transport

    if not str(endpoint).startswith("https://"):
        # Never put the API key on the wire in cleartext. Refuse before sending.
        raise EmbedEngineError("refusing to send the API key to a non-HTTPS embeddings endpoint")

    # Accept a single string or an iterable of strings; always send a list.
    if isinstance(texts, str):
        texts = [texts]
    inputs = [str(t) for t in texts]

    body = json.dumps({"input": inputs, "model": str(model)})
    headers = {
        "Authorization": f"Bearer {secret_key}",             # applied here, never returned
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        status, resp = transport(endpoint, headers, body)
    except Exception as e:                                    # network/timeout — unreachable
        raise EmbedEngineError(f"embeddings endpoint unreachable: {e}")

    if not isinstance(resp, dict):
        raise EmbedEngineError(f"unparseable embeddings response (status {status})")
    if status == 200 and isinstance(resp.get("data"), list):
        # Provider returns [{"index": i, "embedding": [...]}, ...]; order by index so the
        # returned vectors line up with the input order regardless of the wire ordering.
        rows = sorted(resp["data"], key=lambda d: d.get("index", 0))
        vectors = [[float(x) for x in row.get("embedding", [])] for row in rows]
        if len(vectors) != len(inputs):
            raise EmbedEngineError(
                f"provider returned {len(vectors)} vectors for {len(inputs)} inputs")
        return vectors
    err = resp.get("error_description") or resp.get("error") or f"http {status}"
    if isinstance(err, dict):
        err = err.get("message") or json.dumps(err)
    raise EmbedEngineError(f"provider rejected the embeddings request: {err}")


def broker_embedder(k, *, endpoint: str, credential_handle: str, broker, agent_cell,
                    transport=None, model: str = "text-embedding-3-small"):
    """Build an `embedder(text) -> list[float]` callable backed by CRED1.

    Returns a callable that resolves the provider API key via `broker.use_secret` (which
    applies the key INSIDE the broker and never discloses it) and calls `embed_texts` for
    the given text against the HTTPS `endpoint`. Accepts a single string (returns one
    float vector) or a list of strings (returns a list of float vectors) — batch is fine.

    The returned vectors are float and IN-MEMORY ONLY: `discovery.search` uses them for
    ranking via `cosine_int` (which emits an INT score); no float is ever recorded.
    A denied credential or an engine error raises (the caller fails closed)."""
    def embedder(text):
        batch = not isinstance(text, str)
        r = broker.use_secret(
            agent_cell, credential_handle,
            lambda key: embed_texts(key, text, transport=transport, model=model,
                                    endpoint=endpoint))
        if "denied" in r:
            raise EmbedEngineError(f"credential denied: {r['denied']}")
        vectors = r["ok"]                                    # list[list[float]]
        return vectors if batch else vectors[0]
    return embedder


def cosine_int(a, b) -> int:
    """An INT similarity score in [0, SCALE] between two FLOAT vectors, for ranking.

    Computes cosine = dot(a,b) / (‖a‖·‖b‖) in float (in-memory math over in-memory float
    vectors), clamps to [0, 1] (negative cosines floor at 0 for ranking), and returns
    round(SCALE × cosine) as an INT. Identical direction → SCALE; orthogonal/empty → 0.
    This is the ONLY value that may land on the Weft — it is always an int, never a
    float. The float vectors themselves never leave this function into a recorded cell."""
    if not a or not b or len(a) != len(b):
        return 0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom == 0.0:
        return 0
    cos = dot / denom
    if cos <= 0.0:
        return 0
    if cos >= 1.0:
        return SCALE
    return int(round(SCALE * cos))                            # int score — no float recorded
