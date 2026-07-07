"""Tool-DISCOVERY + a deterministic semantic/vector index over capabilities.

Policy — PLUG-IN-OR-FORGE, in strict order:
  1. Find an EXISTING capability that fits the goal (rank the manifest registry by
     semantic similarity — the `manifest.py` catalog is the searchable surface).
  2. If nothing in the registry clears the bar, optionally consult a `research` seam
     (an injected callable that returns candidate tool descriptors from the web /
     external registries / an MCP index) — plug one in rather than reinvent it.
  3. Only if BOTH miss, FORGE a new capability — and the DEFAULT forge path is now the
     REAL pipeline (`forge.forge`: candidate born quarantined → reckoner evaluation →
     attested promotion, or refusal), so a production caller that passes no forge= seam
     still reaches real self-extension. Forging is the last resort, never the first
     move — this is what makes the built-in research/discovery function load-bearing.

The "vector embeddings" projection over capabilities (Method's data architecture) is
implemented here as a DETERMINISTIC LEXICAL EMBEDDING: tokenize → hash each token to a
dimension (BLAKE2b mod N) → an integer bag-of-words vector. It is NOT a real ML model —
it is a stdlib stand-in that occupies the exact seam where a real embedder wraps later
(swap `embed`/`similarity`, keep `search`/`discover`). Everything recorded is an INT:
no float ever enters a score. Same inputs → same vector → same ranking, forever.

Pure stdlib. Composes public `manifest`/`hashing`/`kernel` APIs only — no core edit.
"""
import hashlib
import math
import re

from decima import manifest as M
from decima.hashing import nfc

# Dimensionality of the hashed embedding space. A token maps to exactly one dimension;
# collisions are possible (as in any hashed feature space) but deterministic.
DIM = 1 << 20

# The similarity scale: cosine ∈ [0,1] is projected to an INT in [0, SCALE].
SCALE = 1000

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list:
    """Lowercase and split on any non-alphanumeric run — the deterministic tokenizer."""
    return _TOKEN.findall(nfc(str(text)).lower())


def _dim(token: str) -> int:
    """Map a token to a hashed dimension: BLAKE2b(token) mod DIM. Deterministic, stdlib."""
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % DIM


def embed(text) -> dict:
    """A deterministic lexical embedding: {dimension: count}, all INTS.

    Tokenize the text (lowercase, split on non-alphanumerics), hash each token to a
    dimension, and count occurrences. Pure and deterministic — the SAME text always
    yields the SAME vector. This is the seam where a real vector model wraps in later;
    for now it stands in with zero ML deps and no floats."""
    vec: dict = {}
    for tok in _tokens(text):
        d = _dim(tok)
        vec[d] = vec.get(d, 0) + 1
    return vec


def similarity(a: dict, b: dict) -> int:
    """An INT similarity score in [0, SCALE] — SCALE×cosine, computed with integer math.

    cosine = dot(a,b) / (‖a‖·‖b‖). To keep every value an int we compute
    SCALE·dot // isqrt(‖a‖²·‖b‖²) — no float ever appears. Disjoint or empty vectors
    score 0. Identical vectors score SCALE."""
    if not a or not b:
        return 0
    # Iterate the smaller vector's shared dimensions for the dot product.
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = 0
    for d, v in small.items():
        w = large.get(d)
        if w:
            dot += v * w
    if dot == 0:
        return 0
    norm_a_sq = sum(v * v for v in a.values())
    norm_b_sq = sum(v * v for v in b.values())
    denom = math.isqrt(norm_a_sq * norm_b_sq)
    if denom == 0:
        return 0
    return (SCALE * dot) // denom


def _manifest_text(m: dict) -> str:
    """The searchable text of a manifest: name + title + description + tags."""
    return " ".join([m["name"], m.get("title", ""), m.get("description", ""),
                     " ".join(m.get("tags", []))])


# ── Deterministic lexical RANKING (field-weighted, coverage-normalized, BM25-ish) ────
# The DEFAULT (no-embedder) ranking upgrades the plain bag-of-words cosine to a
# precision-oriented scorer that stays PURE INTEGER and deterministic:
#   - FIELD WEIGHTING — a hit in the NAME counts most, then tags/archetype/effect_class,
#     then the description (name > tags ≳ class > description);
#   - STEM-LITE — a tiny deterministic stemmer folds trivial morphology (documents→
#     document, coordinates→coordinate, shipped→ship) so obvious variants meet;
#   - a small SYNONYM/alias map expands an intent's words to catalog vocabulary
#     ("text someone"→sms/message, "wire funds"→transfer/payout, "hail a taxi"→ride);
#   - multi-term COVERAGE — the score is normalized by how much of the query's
#     discriminative mass was matched, so a partial hit scores proportionally less;
#   - integer BM25-ish IDF — a term rare across the live registry weighs more than a
#     common one, from a document-frequency count over the catalog.
# The result is an INT in [0, SCALE]: an irrelevant query normalizes to 0 (nothing
# clears a sane threshold), while an exact name match approaches SCALE. No float, ever.

# Field weights (name strongest, description weakest). Ints only.
_FIELD_WEIGHTS = {
    "name": 10, "title": 9, "tags": 8, "archetype": 3, "effect_class": 3,
    "description": 4,
}
_MAX_FIELD_WEIGHT = max(_FIELD_WEIGHTS.values())

# Query-side stopwords: filler that carries no capability signal, dropped BEFORE scoring
# so "charge a customer's card" ranks on charge/customer/card, not on "a".
_STOP = frozenset((
    "a an the this that these those to of for from with without and or but not no "
    "in on at by via as is are be am was were do does did done doing have has had "
    "my me i we you your our their his her its it he she they them us "
    "some someone something anyone anybody anything please kindly want wants wanted "
    "need needs needed would like can could should must may might will shall "
    "get got getting make made making let lets go going into onto out up off over "
    "about around new any all each every here there then than so just now").split())

# A small intent→vocabulary alias map. Keys are surface words a caller might use;
# values are the catalog words they should ALSO match. Deliberately conservative — an
# alias never turns a common word into a match-everything token. Applied on the query.
_SYNONYMS = {
    # communication
    "text": ("sms", "message"), "texting": ("sms", "message"),
    "sms": ("text", "message"), "ping": ("message", "notify", "alert"),
    "notify": ("message", "alert"), "dm": ("message",), "chat": ("message",),
    "contact": ("message", "email"), "message": ("sms", "email", "notify"),
    "email": ("message", "notify"),
    # payments / money
    "pay": ("payment", "charge"), "paid": ("payment", "charge"),
    "charge": ("payment", "card"), "billing": ("payment", "invoice"),
    "wire": ("transfer", "payout", "bank"), "remit": ("transfer", "payout"),
    "disburse": ("payout", "transfer"), "withdraw": ("payout", "transfer"),
    "money": ("payment", "transfer", "funds"),
    "funds": ("payout", "transfer", "money"), "refund": ("payment", "payout"),
    # trading
    "invest": ("brokerage", "stock", "trade"), "stocks": ("stock", "share"),
    "shares": ("share", "stock"), "crypto": ("cryptocurrency", "coin"),
    "bitcoin": ("cryptocurrency", "coin", "crypto"),
    # legal / docs
    "sign": ("esign", "signature", "esignature"),
    "signature": ("esign", "esignature"), "notarize": ("esign", "signature"),
    "esign": ("signature", "document"),
    # identity / auth
    "kyc": ("identity", "verify"), "authenticate": ("auth", "login", "oidc"),
    "login": ("auth", "signin", "oidc"), "signin": ("auth", "login", "oidc"),
    "verify": ("verification", "identity"),
    # transport / delivery
    "taxi": ("ride", "transport"), "cab": ("ride", "transport"),
    "uber": ("ride", "transport"), "lyft": ("ride", "transport"),
    "hail": ("ride", "transport"), "deliver": ("delivery", "dispatch"),
    "courier": ("delivery", "dispatch"),
    # tickets / support / paging
    "ticket": ("support", "helpdesk", "issue"), "helpdesk": ("support", "ticket"),
    "bug": ("issue", "ticket"), "incident": ("page", "alert", "oncall"),
    "page": ("pager", "oncall", "alert"), "escalate": ("page", "oncall"),
    # scheduling / calendar
    "meeting": ("calendar", "event", "appointment"),
    "appointment": ("calendar", "event", "booking"),
    "schedule": ("calendar", "event", "booking"),
    # weather / maps
    "forecast": ("weather",), "geocode": ("address", "coordinate"),
    "map": ("maps", "geocode"), "address": ("geocode", "coordinate"),
    # storage / files
    "upload": ("storage", "file", "blob"), "backup": ("storage", "file"),
    # translate / ocr
    "translate": ("translation", "language"),
    "scan": ("ocr", "extract", "document"), "ocr": ("extract", "text"),
    # crm / banking / finance
    "crm": ("contact", "sales", "deal"), "lead": ("contact", "crm", "sales"),
    "balance": ("bank", "account", "finance"),
    "transactions": ("bank", "finance"),
    # payroll / hiring / ads
    "salary": ("payroll", "wage"), "wages": ("payroll", "wage"),
    "hire": ("background", "screening"), "screening": ("background", "check"),
    "advertise": ("ads", "advertising", "campaign"),
    "campaign": ("ads", "advertising", "marketing"),
}


def _stem(tok: str) -> str:
    """A tiny deterministic stem-lite: fold trivial English morphology so obvious
    variants meet (documents→document, coordinates→coordinate, shipped→ship). It is
    intentionally crude — applied IDENTICALLY to query and manifest tokens, so all that
    matters is that variants collapse to the SAME root, not linguistic correctness. It
    leaves short tokens and -ss/-us/-is/-os/-as endings alone (so 'sms', 'ads', 'class'
    survive intact). Pure, deterministic, no float."""
    t = tok
    if len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is", "os", "as")):
        t = t[:-1]
    return t


def _field_index(m: dict) -> dict:
    """Per-field STEMMED token SETS for a manifest — the searchable surface split by
    field so a hit can be weighted by WHERE it landed. Deterministic."""
    def st(text):
        return {_stem(t) for t in _tokens(text)}
    tags = " ".join(m.get("tags", []) or [])
    return {
        "name": st(m.get("name", "")),
        "title": st(m.get("title", "")),
        "tags": st(tags),
        "archetype": st(m.get("archetype", "")),
        "effect_class": st(m.get("effect_class", "")),
        "description": st(m.get("description", "")),
    }


def _expand(tok: str) -> frozenset:
    """A query token's match set: itself (raw + stemmed) plus any stemmed synonyms. This
    is the alias layer that lets an intent's words meet catalog vocabulary."""
    base = _stem(tok)
    out = {tok, base}
    for syn in _SYNONYMS.get(tok, ()) + _SYNONYMS.get(base, ()):
        out.add(_stem(syn))
    return frozenset(out)


def _query_terms(goal) -> list:
    """The distinct, meaningful query terms as (label, expansion_set): stopwords and
    single-char tokens dropped, order-stable, de-duplicated by expansion."""
    terms = []
    seen = set()
    for tok in _tokens(goal):
        if len(tok) < 2 or tok in _STOP:
            continue
        exp = _expand(tok)
        key = tuple(sorted(exp))
        if key in seen:
            continue
        seen.add(key)
        terms.append((tok, exp))
    return terms


def _doc_freq(terms, allsets) -> dict:
    """Document frequency per query term over the corpus: how many manifests contain the
    term (or one of its synonyms/stems) in ANY field. The basis of the integer IDF."""
    df = {}
    for label, exp in terms:
        n = 0
        for allset in allsets:
            if exp & allset:
                n += 1
        df[label] = n
    return df


def _lexical_score(terms, fields, df, n_docs) -> int:
    """The field-weighted, coverage-normalized, IDF-scaled INT score in [0, SCALE].

    For each query term take the STRONGEST field it hits (name > tags/archetype/
    effect_class > description) and weight it by an integer IDF (rarer term → heavier).
    Normalize by the best achievable mass (every term hitting a NAME), so the score reads
    as 'how much of the query's discriminative mass this manifest covers'. A term that
    appears NOWHERE in the catalog (df == 0) is dropped from BOTH sides — an
    out-of-vocabulary filler word can neither be matched nor counted against a manifest —
    so an irrelevant query normalizes to 0 and a good match approaches SCALE."""
    num = 0
    denom = 0
    for label, exp in terms:
        d = df.get(label, 0)
        if d <= 0:
            continue                                    # out-of-vocabulary — ignore entirely
        idf = (1 + n_docs) // (1 + d)                   # integer IDF: rarer term → larger
        denom += _MAX_FIELD_WEIGHT * idf
        best = 0
        for field, weight in _FIELD_WEIGHTS.items():
            if weight > best and (exp & fields[field]):
                best = weight
        num += best * idf
    if denom == 0:
        return 0
    return (SCALE * num) // denom


def search(k, goal, *, top_k: int = 5, archetype=None, effect_class=None,
           embedder=None) -> list:
    """Rank the manifest registry against `goal` by semantic similarity.

    Embeds the goal and every candidate manifest's name+title+description+tags, scores
    each, applies optional exact filters, and returns the top_k as
    [{name, score:int, manifest_cell_id}]. Ties break by name so results are stable
    across runs.

    Two ranking backends, same shape (int scores, deterministic ordering):
      - `embedder is None` (DEFAULT, unchanged): the built-in deterministic LEXICAL
        embedding — `embed`/`similarity`, hashed bag-of-words, pure stdlib, no float.
      - `embedder` provided: an OPTIONAL real vector model — a callable
        `embedder(text) -> list[float]` (e.g. `embed_engine.broker_embedder(...)`). The
        goal and each manifest's text are embedded into FLOAT vectors and ranked by
        `embed_engine.cosine_int`, which returns an INT score. The float vectors stay
        IN-MEMORY ONLY; only the INT score is ever surfaced/recorded — no float leaks."""
    cells = list(M.registry(k))
    scored = []
    if embedder is None:
        # DEFAULT: the field-weighted, coverage-normalized, IDF-scaled lexical scorer.
        # Corpus statistics (per-manifest field index + document frequency) are computed
        # once over the FULL registry so the IDF weighting is stable regardless of the
        # archetype/effect_class filter applied to the returned candidates.
        terms = _query_terms(goal)
        indices = [_field_index(c.content) for c in cells]
        allsets = [set().union(*idx.values()) for idx in indices]
        df = _doc_freq(terms, allsets)
        n_docs = len(cells)
        for c, fields in zip(cells, indices):
            m = c.content
            if archetype and m["archetype"] != archetype:
                continue
            if effect_class and m["effect_class"] != effect_class:
                continue
            score = _lexical_score(terms, fields, df, n_docs)
            scored.append({"name": m["name"], "score": int(score), "manifest_cell_id": c.id})
    else:
        from decima import embed_engine as _E
        q_vec = embedder(goal)                               # float vector — in-memory only
        for c in cells:
            m = c.content
            if archetype and m["archetype"] != archetype:
                continue
            if effect_class and m["effect_class"] != effect_class:
                continue
            score = _E.cosine_int(q_vec, embedder(_manifest_text(m)))
            scored.append({"name": m["name"], "score": int(score), "manifest_cell_id": c.id})
    # Highest score first; deterministic tiebreak on name.
    scored.sort(key=lambda r: (-r["score"], r["name"]))
    return scored[: int(top_k)]


# ── The PRODUCTION forge DEFAULT — the REAL pipeline, with NO caller wiring ──────────
# Cycle 60 (forge-real) made `forge.forge` route a discovery-triggered forge through the
# REAL candidate → reckoner → promotion pipeline — but the two PRODUCTION discover()
# call sites (kernel.say's live discovery hook and agent.suggest_capabilities) pass NO
# forge= seam, so production self-extension used to stop at a bare {"action": "forge"}
# signal (a toy: nothing authored, nothing evaluated, nothing promoted). The default
# below closes that gap AT THE SEAM, so BOTH production sites inherit the real pipeline
# without a core edit:
#
#   - a caller that passes NO forge= now reaches `forge.forge` — the forged capability
#     is authored BORN QUARANTINED, evaluated by the reckoner, and ATTESTED-PROMOTED
#     through the tiered trust gate, or REFUSED (PromotionBlocked, fail closed);
#   - a caller that passes an EXPLICIT forge= still overrides (the seam stays
#     injectable — tests inject deterministic pipelines);
#   - when NO codegen is reachable at all (the offline/oracle default —
#     `candidate.model_codegen` fails CLOSED with no key and no bound egress), the
#     default falls back to the legacy bare {"action": "forge"} signal, HONESTLY and
#     side-effect-free: nothing lands on the Weft, and the result is byte-identical
#     and deterministic across calls.
#
# `_DEFAULT_CODEGEN` is the injectable codegen binding for the DEFAULT path: production
# leaves it None (resolve to `candidate.model_codegen`, the live egress-gated model);
# an offline harness binds a deterministic codegen so the REAL default path runs.
_DEFAULT_CODEGEN = [None]


def bind_default_codegen(codegen):
    """Bind the codegen the DEFAULT (no forge= passed) last-resort path authors source
    through, returning the PREVIOUS binding so the caller can restore it. `None`
    restores the production default — `candidate.model_codegen`, the live egress-gated
    model seam, which FAILS CLOSED offline (honest bare-signal fallback). A test binds
    a deterministic codegen here to drive the real pipeline offline, then restores."""
    prev = _DEFAULT_CODEGEN[0]
    _DEFAULT_CODEGEN[0] = codegen
    return prev


def default_forge(k, goal) -> dict:
    """The PRODUCTION last-resort forge: route `goal` through the REAL forge pipeline
    (`forge.forge`: candidate authored BORN QUARANTINED → reckoner evaluation →
    ATTESTED, tiered promotion). A candidate that fails evaluation is REFUSED —
    `PromotionBlocked` propagates (fail closed, nothing registered, NO stub). Forging
    grants nothing extra: the promoted organ rides the same ocap spine (authorize gates
    every INVOKE; Morta can revoke it) as any integrated tool.

    Only when NO codegen is reachable at all (`candidate.CodegenUnavailable` — offline,
    no key, no bound egress) does this fall back to the bare, honest
    {"action": "forge"} signal — probed BEFORE the pipeline is entered, so the
    unavailable path writes NOTHING to the Weft and stays byte-identical across calls
    (deterministic). Imports lazily so discovery adds no forge/candidate dependency at
    module load (no import cycle)."""
    from decima import candidate as _C             # lazy: no import cycle at module load
    from decima import forge as _F
    goal = nfc(str(goal))
    codegen = _DEFAULT_CODEGEN[0]
    if codegen is None:
        # The production seam: the live egress-gated model authors the source. Probe it
        # FIRST so an offline/keyless environment falls back WITHOUT touching the Weft
        # (no trust anchors, no candidate cells) — the legacy honest signal.
        try:
            source = _C.model_codegen(goal)
        except _C.CodegenUnavailable:
            return {"action": "forge", "goal": goal,
                    "reason": "no existing capability matches"}

        def codegen(_intent, _src=source):
            return _src                            # reuse the already-authored source
    # The REAL pipeline, strictly: with a codegen in hand an evaluation failure raises
    # PromotionBlocked (fail closed) — never a decorative stub, never a fake success.
    return _F.forge(k, goal, codegen=codegen)


# ── CATALOG ACTIVATION — a "use" suggestion becomes an approvable INSTALL ───────────
# Before this seam the discovery "use" path was DECORATIVE: `discover()` surfaced a
# use-suggestion and `kernel.say` told the human "approve to activate it" — but no
# ApprovalInbox item was ever submitted and no installer mapped the found manifest to a
# real handler, so approval had NOTHING to fire. Closed here, at the discovery/inbox
# seam (no core edit): every "use" suggestion `discover()` returns for a NOT-YET-
# INSTALLED capability now SUBMITS a durable activation — a Morta-gated activation
# ENACTOR capability (`catalog.activate:<name>`, requires_approval) enqueued as an
# `inbox_item`. A human `approve` enacts it through the SAME approve_invocation/
# authorize/Morta spine as any gated effect, and the enactor INSTALLS the manifest as a
# real gated capability via the PUBLIC `kernel.integrate_tool` — after which invoking
# it routes the ordinary authorize + Morta path (the manifest's own caveats, e.g.
# `requires_approval` on a FINANCIAL rail, still gate every invoke). Because the
# submission rides INSIDE `discover()`, both production call sites — kernel.say's live
# discovery hook and agent.suggest_capabilities — inherit it with no kernel edit.
#
# Laws: NOTHING auto-activates (the enactor is requires_approval — a direct invoke is
# denied at the gate; only an explicit human approve() installs); a DENIED activation
# installs nothing and leaves the denial Cell the inbox records; with NO handler bound
# for the capability the enactor REFUSES (fail closed — approval installs nothing
# rather than wiring a fake handler); manifest/suggestion content stays DATA
# (instruction_eligible=False on the queued item — it can describe, never instruct).

ACTIVATE_PREFIX = "catalog.activate:"          # the per-capability activation enactor

# The injectable handler registry: capability name → effect handler `(impl, args) ->
# dict`. Activation INSTALLS a capability, so it must map the manifest to a REAL
# handler; the operator (or a check, hermetically) binds one here — an engine's live
# handler once its credentials exist. UNBOUND names fail CLOSED at approve-time:
# approval installs nothing (never a stub passed off as the engine).
_ACTIVATION_HANDLERS: dict = {}


def bind_activation_handler(name, handler):
    """Bind the effect handler that activating catalog capability `name` installs,
    returning the PREVIOUS binding (None if there was none) so a caller can restore
    it. `handler=None` unbinds — approval then fails CLOSED for that name (nothing
    installs until a real handler is bound). Binding CONFERS NOTHING: the handler only
    ever becomes invokable through an explicit human-approved activation, and
    `capability.authorize` + Morta still gate every subsequent invoke."""
    key = nfc(str(name))
    prev = _ACTIVATION_HANDLERS.get(key)
    if handler is None:
        _ACTIVATION_HANDLERS.pop(key, None)
    else:
        _ACTIVATION_HANDLERS[key] = handler
    return prev


def installed_capability(k, name):
    """The id of the LIVE (non-retracted) capability cell named `name`, or None. The
    predicate that keeps activation honest: an already-installed capability is never
    re-queued for approval (the suggestion reports status 'installed' instead)."""
    name = nfc(str(name))
    for c in k.weave().of_type("capability"):
        if not c.retracted and c.content.get("name") == name:
            return c.id
    return None


def submit_activation(k, suggestion) -> dict:
    """Turn a `discover()` "use" suggestion into a durable, Morta-gated ACTIVATION:
    an ApprovalInbox item whose explicit human `approve()` INSTALLS the found manifest
    as a real gated capability (`kernel.integrate_tool` — the public installer), after
    which invoking it routes the ordinary authorize + Morta path. `deny()` installs
    nothing and records the denial Cell (the inbox spine). Fails LOUD on anything that
    is not a resolvable use-suggestion (fail closed: no item for a phantom manifest).

    Idempotent both ways: an already-INSTALLED capability returns
    {"status": "installed", ...} and queues nothing; an already-PENDING activation
    returns the SAME item ({"status": "pending", "item": <existing>}), so repeated
    discovery of the same goal never floods the inbox and `discover()` stays
    deterministic across calls. Grants nothing by itself: the queued item is DATA
    (instruction_eligible=False), the enactor is requires_approval (a direct,
    approval-less invoke is denied at the gate), and the installed capability keeps
    the MANIFEST's own caveats — a Morta-gated rail stays Morta-gated after install."""
    if not isinstance(suggestion, dict) or suggestion.get("action") != "use":
        raise ValueError("submit_activation requires a discover() 'use' suggestion")
    name = nfc(str(suggestion.get("name") or ""))
    mid = suggestion.get("manifest")
    if not name or not mid:
        raise ValueError("a use-suggestion must carry a capability name and its "
                         "manifest cell id (fail closed)")
    mcell = k.weave().get(mid)
    if mcell is None or mcell.type != M.MANIFEST or mcell.content.get("name") != name:
        raise ValueError(f"no manifest cell {mid!r} for {name!r} — nothing to "
                         f"activate (fail closed)")
    held = installed_capability(k, name)
    if held is not None:                     # already live — nothing to approve
        return {"status": "installed", "name": name, "capability": held,
                "manifest": mid}

    def _install(impl, args, _k=k, _name=name, _mid=mid):
        """The activation ENACTOR: runs ONLY through an approved, nonce-pinned invoke
        (the inbox carried a human decision to the gate). Maps the manifest to its
        bound handler and installs it via the public `kernel.integrate_tool` with the
        MANIFEST's caveats — the single source of truth for how it is gated."""
        from decima import executor as _X
        if args.get("name") != _name or args.get("manifest") != _mid:
            raise _X.ExecError("activation refused: args do not name the suggested "
                               "manifest (fail closed)")
        mc = _k.weave().get(_mid)
        if mc is None or mc.retracted or mc.type != M.MANIFEST \
                or mc.content.get("name") != _name:
            raise _X.ExecError("activation refused: the manifest drifted or was "
                               "retracted since the suggestion (fail closed)")
        already = installed_capability(_k, _name)
        if already is not None:              # a second approved item — idempotent
            return {"out": f"capability {_name!r} already active",
                    "capability": already, "manifest": _mid, "installed": False,
                    "instruction_eligible": False}
        handler = _ACTIVATION_HANDLERS.get(_name)
        if handler is None:
            raise _X.ExecError(
                f"activation refused: no handler bound for {_name!r} "
                f"(bind_activation_handler) — fail closed, nothing installed")
        cap_id = _k.integrate_tool(_name, handler, caveats=dict(mc.content.get("caveats") or {}))
        return {"out": f"activated {_name!r} from its catalog manifest — authorize "
                       f"+ Morta still gate every invoke",
                "capability": cap_id, "manifest": _mid, "installed": True,
                "instruction_eligible": False}

    # The enactor is itself a Morta-gated capability: a direct (approval-less) invoke
    # is DENIED at the ocap gate; only the inbox's approve — approve_invocation with
    # the item's pinned nonce — can enact it. Content-addressed, so re-submission
    # resolves to the SAME enactor id (deterministic across calls).
    gcap = k.integrate_tool(ACTIVATE_PREFIX + name, _install,
                            caveats={"requires_approval": True,
                                     "effect_class": "INSTALL"})
    from decima.inbox import ApprovalInbox   # lazy: no import cycle at module load
    ib = ApprovalInbox(k)
    for item in ib.pending():                # already queued → the SAME pending item
        if item.content.get("capability") == gcap:
            return {"status": "pending", "name": name, "item": item.id,
                    "activation": gcap, "manifest": mid}
    agent = k.weave().get(k.decima_agent_id)
    item_id = ib.enqueue(
        agent, gcap, {"name": name, "manifest": mid},
        description=f"activate catalog capability “{name}” — install its bundled "
                    f"manifest as a gated capability (authorize + Morta still gate "
                    f"every invoke)",
        provenance=mid)
    return {"status": "pending", "name": name, "item": item_id,
            "activation": gcap, "manifest": mid}


def discover(k, goal, *, threshold: int, research=None, embedder=None, forge=None) -> dict:
    """The PLUG-IN-OR-FORGE dispatcher. Deterministic given the same inputs.

    - Search the registry. If the best score >= `threshold` (an INT), USE it:
      {"action":"use", "name":..., "score":int, "manifest": cell_id, "activation":...}.
      A use-suggestion for a NOT-YET-INSTALLED capability also SUBMITS an activation
      (`submit_activation`): a Morta-gated ApprovalInbox item whose human approve()
      installs the manifest via `kernel.integrate_tool` — nothing auto-activates, and
      repeated calls reuse the same pending item (idempotent, so the returned dict is
      stable across calls given unchanged state).
    - Else, if a `research(goal) -> list` seam is injected and yields a candidate tool
      descriptor, PLUG IT IN: {"action":"plug_in", "candidate": <descriptor>}.
    - Else FORGE as a last resort. An injected `forge(k, goal) -> dict` seam overrides
      (tests inject deterministic pipelines); with NO forge= passed — the PRODUCTION
      shape — the DEFAULT is `default_forge`, the REAL `forge.forge` pipeline: the
      forged capability is born quarantined, evaluated, and attested-promoted
      ({"action":"forged", "stub": False, "promoted": True, ...}), or refused
      (`PromotionBlocked`, fail closed). Only when no codegen is reachable at all
      (offline — `candidate.model_codegen` fails closed) does the default fall back to
      the bare, honest signal {"action":"forge", "goal":..., "reason":...}, writing
      nothing.

    Find an existing tool first (registry → research seam); forge only when nothing
    matches. `threshold` is an int; scores are ints; no float is ever recorded."""
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise ValueError("threshold must be an int (no floats in a recorded score)")
    ranked = search(k, goal, top_k=1, embedder=embedder)
    best = ranked[0] if ranked else None
    if best is not None and best["score"] >= threshold:
        sug = {"action": "use", "name": best["name"], "score": best["score"],
               "manifest": best["manifest_cell_id"]}
        # ACTIVATION (the running path): a use-suggestion is no longer decorative —
        # submit it at THIS seam so both production callers (kernel.say's discovery
        # hook and agent.suggest_capabilities) inherit an approvable, installable
        # activation with no core edit: "approve to activate it" now has an inbox
        # item for the human decision to land on, and approve() actually installs.
        # Inert-on-failure like the say hook itself: a submission failure never
        # costs the caller the suggestion (the advice survives; activation=None).
        try:
            sug["activation"] = submit_activation(k, sug)
        except Exception:  # noqa: BLE001 — the suggestion must survive a submit failure
            sug["activation"] = None
        return sug
    if research is not None:
        candidates = research(goal) or []
        if candidates:
            return {"action": "plug_in", "candidate": candidates[0]}
    if forge is None:
        forge = default_forge                      # ← the PRODUCTION DEFAULT: the REAL pipeline
    return forge(k, nfc(str(goal)))                # Nona grows the organ (last resort)
