"""Real embedding engine — hosted vectors over stdlib urllib, wired into discovery.

The discovery lane ranks capabilities by a DETERMINISTIC LEXICAL embedding — a
zero-dependency stand-in occupying the exact seam where a real vector model wraps later.
`embed_engine.py` is that real model: it asks a hosted embeddings provider (an OpenAI /
Voyage / Cohere-style HTTPS API) to turn text into FLOAT vectors, over stdlib `urllib`
(zero deps). This check drives it entirely OFFLINE via an injected fake transport that
returns canned vectors (the real `urllib` transport is never called), and proves:

  - `embed_texts` maps texts → float vectors via the injected transport (input order
    preserved; the request carries {input, model});
  - HTTPS-only: a non-`https://` endpoint is refused BEFORE any request (transport never
    called) — the API key never rides a cleartext wire;
  - `cosine_int` returns an INT in [0, SCALE] (identical → SCALE, orthogonal → 0);
  - `broker_embedder` resolves the key via CRED1 (`broker.use_secret`, dispense-don't-
    disclose) — the raw key never appears in any Weft event payload;
  - `discovery.search(k, goal, embedder=broker_embedder(...))` ranks manifests by the
    REAL vectors and returns INT scores (isinstance int);
  - default `discovery.search` (no embedder) still works unchanged (lexical);
  - CRITICAL: NO float is ever written to a cell — every recorded score is an int.

Contract: run(k, line). Fail loud. Owns a fresh, offline Kernel + SecretsBroker.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D
from decima import embed_engine as E
from decima import secrets

API_KEY = "sk_live_EMBED_PROVIDER_SUPER_SECRET_KEY"
ENDPOINT = "https://api.openai.com/v1/embeddings"

# Canned semantic vectors — a tiny 3-dim "concept space": [comms, geo, money]. Each text
# is placed near the concept it means, so REAL-vector ranking is clearly semantic (and
# distinct from a lexical bag-of-words). The provider "returns" these floats over the wire.
VECTORS = {
    # goal
    "send an email to a customer":        [0.98, 0.10, 0.15],
    # manifest texts (name + title + description + tags, joined by discovery._manifest_text)
    "send an email message":              [0.97, 0.05, 0.10],   # → close to the goal
    "geocode an address":                 [0.05, 0.99, 0.02],   # → far
    "charge a credit card":               [0.10, 0.03, 0.98],   # → far
    # cosine_int fixtures
    "orthogonal-a":                       [1.0, 0.0, 0.0],
    "orthogonal-b":                       [0.0, 1.0, 0.0],
}


def _vec_for(text: str):
    """The canned float vector for a text: exact match if known, else a fixed default
    (still a valid vector, so unknown manifest texts rank low against the goal)."""
    for key, vec in VECTORS.items():
        if key in text:
            return list(vec)
    return [0.01, 0.01, 0.01]


def _transport(calls):
    """A fake embeddings-provider transport: records each call and returns an OpenAI-style
    200 body ({"data": [{"index", "embedding"}, ...]}) built from the canned vectors.
    No network — the real urllib transport is never touched."""
    def t(url, headers, body):
        import json
        calls.append({"url": url, "headers": headers, "body": body})
        payload = json.loads(body)
        data = [{"index": i, "embedding": _vec_for(str(txt))}
                for i, txt in enumerate(payload["input"])]
        return 200, {"data": data, "model": payload["model"]}
    return t


def _decima(kk):
    return kk.weave().get(kk.decima_agent_id)


def _all_payloads(kk) -> str:
    return "".join(r[0] for r in kk.weft.db.execute("SELECT payload FROM events"))


def run(k, line):
    line("\n== REAL EMBEDDING ENGINE (hosted vectors over urllib) → discovery seam ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    broker = secrets.SecretsBroker(kk)
    broker.store("embed_provider", API_KEY, service="openai")
    handle = broker.issue("embed_provider", _decima(kk), "embed text for semantic search")

    # Register a small catalog to rank over. ─────────────────────────────────────────
    M.register(kk, M.capability_manifest(
        "send_email", title="send an email message",
        description="send an email message to a recipient", archetype="EFFECT",
        effect_class="COMMUNICATION", tags=["email", "message", "notify"]))
    M.register(kk, M.capability_manifest(
        "geocode", title="geocode an address",
        description="convert a street address to latitude and longitude coordinates",
        archetype="COMPUTE", effect_class="READ", tags=["address", "coordinates", "maps"]))
    M.register(kk, M.capability_manifest(
        "charge_card", title="charge a credit card",
        description="charge a customer credit card for a payment", archetype="EFFECT",
        effect_class="FINANCIAL", tags=["payment", "card", "billing"]))

    # 1. embed_texts maps texts → float vectors via the injected transport. ───────────
    calls = []
    vecs = E.embed_texts(API_KEY, ["send an email message", "geocode an address"],
                         transport=_transport(calls))
    assert len(vecs) == 2 and vecs[0] == [0.97, 0.05, 0.10] and vecs[1] == [0.05, 0.99, 0.02], vecs
    assert all(isinstance(x, float) for v in vecs for x in v), "real vectors are floats"
    assert len(calls) == 1 and calls[0]["url"] == ENDPOINT, calls
    import json as _json
    sent = _json.loads(calls[0]["body"])
    assert sent["input"] == ["send an email message", "geocode an address"], sent
    assert sent["model"] == "text-embedding-3-small", sent
    line("  embed_texts: texts → float vectors via injected transport "
         "(input order preserved; request carries {input, model}) ✓")

    # 2. HTTPS-only — a non-HTTPS endpoint is refused before any request. ─────────────
    http_calls = []
    try:
        E.embed_texts(API_KEY, ["x"], transport=_transport(http_calls),
                      endpoint="http://api.openai.com/v1/embeddings")
        assert False, "non-HTTPS endpoint must be refused"
    except E.EmbedEngineError as e:
        assert "HTTPS" in str(e), e
    assert http_calls == [], "a non-HTTPS endpoint must be refused before any request"
    line("  HTTPS-only: non-HTTPS endpoint refused before the key is sent "
         "(transport never called) ✓")

    # 3. cosine_int — INT in [0, SCALE]; identical → SCALE, orthogonal → 0. ───────────
    va = _vec_for("orthogonal-a")
    vb = _vec_for("orthogonal-b")
    same = E.cosine_int(va, va)
    orth = E.cosine_int(va, vb)
    assert isinstance(same, int) and same == E.SCALE, f"identical → SCALE, got {same}"
    assert isinstance(orth, int) and orth == 0, f"orthogonal → 0, got {orth}"
    assert E.cosine_int([], va) == 0, "empty vector → 0"
    line(f"  cosine_int: INT in [0,{E.SCALE}] (identical={same}, orthogonal={orth}) ✓")

    # 4. broker_embedder — key via CRED1; discovery.search ranks by REAL vectors. ─────
    bcalls = []
    embedder = E.broker_embedder(
        kk, endpoint=ENDPOINT, credential_handle=handle, broker=broker,
        agent_cell=_decima(kk), transport=_transport(bcalls))
    # The embedder resolves the key inside the broker and returns a float vector.
    gv = embedder("send an email to a customer")
    assert gv == [0.98, 0.10, 0.15] and all(isinstance(x, float) for x in gv), gv
    assert len(bcalls) >= 1, "broker_embedder must call the provider via the transport"

    ranked = D.search(kk, "send an email to a customer", top_k=3, embedder=embedder)
    assert ranked[0]["name"] == "send_email", f"real vectors must rank send_email first, got {ranked}"
    assert all(isinstance(r["score"], int) and not isinstance(r["score"], bool)
               for r in ranked), ("real-vector scores must be ints", ranked)
    assert ranked[0]["score"] > ranked[1]["score"], ("semantic separation", ranked)
    line(f"  broker_embedder + discovery.search(embedder=…) → "
         f"{[(r['name'], r['score']) for r in ranked]} (REAL vectors; send_email first; "
         f"scores int) ✓")

    # 5. discover() threads the embedder through and returns an INT score. ────────────
    used = D.discover(kk, "send an email to a customer", threshold=1, embedder=embedder)
    assert used["action"] == "use" and used["name"] == "send_email", used
    assert isinstance(used["score"], int) and not isinstance(used["score"], bool), used
    line(f"  discover(embedder=…, threshold=1) → action=use name={used['name']} "
         f"score={used['score']} (int; existing capability wins) ✓")

    # 6. default search (no embedder) still works UNCHANGED (lexical). ────────────────
    lex = D.search(kk, "email a customer", top_k=3)
    assert lex[0]["name"] == "send_email", f"lexical default must still rank send_email first, got {lex}"
    assert all(isinstance(r["score"], int) for r in lex), lex
    assert lex == D.search(kk, "email a customer", top_k=3), "lexical default must stay deterministic"
    line(f"  default search (no embedder) unchanged → {[(r['name'], r['score']) for r in lex]} "
         f"(lexical; deterministic; back-compat) ✓")

    # 7. dispense-don't-disclose — the raw API key never on the Weft. ─────────────────
    payloads = _all_payloads(kk)
    assert API_KEY not in payloads, "the raw embedding API key must never be written to the Weft"
    line("  no raw API key on the Weft — CRED1 applies it inside the broker ✓")

    # 8. CRITICAL — NO float anywhere in signed/recorded content. ─────────────────────
    floats_on_weft = 0
    for r in kk.weft.db.execute("SELECT payload FROM events"):
        try:
            obj = _json.loads(r[0])
        except (ValueError, TypeError):
            continue
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, float):
                floats_on_weft += 1
            elif isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    assert floats_on_weft == 0, f"NO float may land on the Weft, found {floats_on_weft}"
    # And every recorded discovery score is an int (belt and suspenders).
    for r in (*ranked, used, *lex):
        s = r["score"] if isinstance(r, dict) else r
        assert isinstance(s, int) and not isinstance(s, bool), ("score must be int", r)
    line("  NO float in any cell — float vectors stayed in-memory; only INT scores recorded ✓")

    line("  → real embeddings, still pure stdlib: a hosted provider (over urllib, zero "
         "deps) yields FLOAT vectors used only in memory for ranking; the key rides CRED1, "
         "cleartext endpoints are refused, and only INT scores ever touch the Weft.")
