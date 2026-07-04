"""GOLIVE — the operator-facing go-live rail (Phase 2 · the credential-gated half).

Cycle 48 built the SAFE half of Phase 2: ModelBrain is the default driver seam and
Morta-gated effects queue in a durable approval inbox. What remained was the part
that needs the OPERATOR — real credentials in the broker, a real egress grant for
the brain, an honest status surface. This module is that rail. It adds a surface,
never an authority path: every step below rides an EXISTING gate.

  • **Secret intake** (`intake_env`) — an EXPLICIT operator action (env → broker),
    never silent, never automatic beyond boot's announced pull. Convention:
    `DECIMA_SECRET_<NAME>` (plus `ANTHROPIC_API_KEY` mapped to name "anthropic").
    VERIFIED custody: `SecretsBroker.store` keeps the raw value ONLY in the
    broker's in-memory store (the HSM/enclave seam) — the Weft receives a
    `credential` REFERENCE cell (name + content digest, `disclosed: False`), so
    the CRED1 audit trail exists while the value never lands in any Cell, receipt,
    log line, or printed output. Intake is idempotent (an unchanged value re-lands
    nothing) and reports ONLY redacted status ("stored"/"unchanged").

  • **Egress grants** (`request_grant(host)`) — the operator flow to open the wire
    to ONE host. It forges a per-host egress capability (caveats: allowlist =
    exactly that host; the wire gate additionally enforces https-only) and a
    Morta-gated GRANT-ENACTOR capability whose one effect is to record the
    capability-scoped approval on that egress cap. The enactor is `requires_
    approval`, so it ENQUEUES a durable `inbox_item` — a human reviews with
    `inbox` and decides with `approve`/`deny`. NOTHING auto-approves: until the
    human approves, `wire._gate` refuses every connection (Morta gate) before any
    socket exists. Approving confers exactly {scheme https, that host} per the
    wire gate's rules — per-call, per-connection, provenance-recorded. A REVOKED
    grant is not silently resurrectable: re-requesting a host whose capability was
    retracted first RETRACTs the stale approval, so the human must decide again.

  • **Live-engine flip** (`activate_engine(k, name, host)`) — the missing half of
    the grant flow: after a human APPROVES an egress grant for an engine's host,
    this flips that ONE engine live. It runs the SAME approved-grant test
    `bind_brain` rides (`approved_egress_cap`), constructs the engine's wire-gated
    transport via `live_wire` (the ONLY live path — the gate re-runs the FULL rule
    of egress per call), and REGISTERS the engine in `k.live_engines` (the Lane B
    registry, a dict created on demand). The flip MINTS NOTHING — no capability,
    no approval; it only records which already-approved engine holds a gated
    transport. No approved grant → NO flip: the engine stays offline and any stale
    registration is dropped (fail closed). The flip lands an `engine_live` Cell on
    the Weft (name · host · capability id · shape — REDACTED, never a secret).
    A REVOKED grant un-lives the engine on the next doctor/flip: the registry is
    re-verified against the Weft, never trusted.

  • **Doctor** (`doctor`/`doctor_lines`) — honest, redacted state: wire armed?,
    granted/pending egress hosts, brain driver in effect + key PRESENCE (never the
    key), secrets held (names only), engines live vs test-mode. Engines report
    through `k.live_engines` — pruned against the Weft first (a revoked grant
    un-lives its engine), and absence is reported as absence, never guessed.

  • **Boot** (`boot`, called from run.py) — with NO key in the environment it does
    NOTHING and returns [] (identical behavior to before). With a key: announced
    intake, then `bind_brain` binds the ModelBrain to an APPROVED api.anthropic.com
    grant if (and only if) one exists on the Weft; otherwise the brain stays on the
    deterministic fallback and the boot lines say exactly what is missing.

Laws upheld: FAIL CLOSED (no grant → EgressDenied before any socket; unknown env →
nothing stored); Morta-gates outward effects (the grant enactment itself is a gated
effect a human approves); no ambient authority (holding a key buys no connection —
the wire demands the approved capability in the envelope, per call); untrusted-is-
data (nothing here is instruction_eligible); provenance on the Weft (credential
references, inbox items, approvals, wire decisions); secrets are APPLIED, never
disclosed (CRED1). Pure stdlib. Proof: heartbeat/checks/418_golive.py (the rail),
heartbeat/checks/460_liveflip.py (the live-engine flip).
"""
import os

from decima import egress, wire
from decima.hashing import blob_id, content_id, nfc
from decima.inbox import ApprovalInbox
from decima.model import assert_content, assert_edge
from decima.secrets import SecretsBroker
from decima.weft import RETRACT

SECRET_ENV_PREFIX = "DECIMA_SECRET_"          # DECIMA_SECRET_<NAME> → name (lowered)
ENV_ALIASES = {"ANTHROPIC_API_KEY": "anthropic"}
BRAIN_HOST = "api.anthropic.com"              # the model brain's one egress target
EGRESS_NAME_PREFIX = "egress.fetch:"          # per-host egress capability name
GRANT_EFFECT_PREFIX = "egress.grant:"         # per-host Morta-gated grant enactor
ENGINE_LIVE = "engine_live"                   # the Weft record of a flip (redacted)


# ── the broker: one per kernel, created on first intake ─────────────────────
def broker(k) -> SecretsBroker:
    """The kernel's go-live SecretsBroker (CRED1 custody: raw values live ONLY in
    its in-memory store; the Weft holds references). Created lazily, attached to
    the kernel instance — no core edit, one broker per realm."""
    b = getattr(k, "golive_secrets", None)
    if b is None:
        b = SecretsBroker(k)
        k.golive_secrets = b
    return b


# ── secret intake: explicit, idempotent, redacted ────────────────────────────
def _env_secrets(environ) -> dict:
    """The provider secrets present in `environ`, keyed by broker name. Values are
    handled locally and handed straight to the broker — never logged, never
    returned by any public surface."""
    found = {}
    for var, val in environ.items():
        if not val:
            continue
        if var in ENV_ALIASES:
            found[ENV_ALIASES[var]] = val
        elif var.startswith(SECRET_ENV_PREFIX):
            name = var[len(SECRET_ENV_PREFIX):].lower()
            if name:
                found[name] = val
    return found


def intake_env(k, environ=None) -> list:
    """EXPLICIT operator intake: pull `DECIMA_SECRET_<NAME>` (and ANTHROPIC_API_KEY
    → "anthropic") from the environment into the SecretsBroker. The raw value goes
    ONLY into the broker's in-memory store; the Weft gets the CRED1 reference cell
    (name + digest, disclosed=False). Idempotent — a value the broker already holds
    (same digest) re-lands nothing. Returns a REDACTED report: names + status
    ("stored" | "unchanged"), never a value."""
    env = os.environ if environ is None else environ
    b = broker(k)
    report = []
    found = _env_secrets(env)
    for name in sorted(found):
        val = found[name]
        held = b._store.get(nfc(name))
        if held is not None and held["digest"] == blob_id(val.encode("utf-8"),
                                                          kind="secret"):
            report.append({"name": name, "status": "unchanged", "value": "(redacted)"})
            continue
        b.store(name, val, service=name)
        report.append({"name": name, "status": "stored", "value": "(redacted)"})
    return report


# ── egress grants: the operator flow, riding the Morta/approval-inbox spine ──
def _egress_cap_name(host: str) -> str:
    return EGRESS_NAME_PREFIX + host


def _predicted_cap_id(name: str) -> str:
    """The deterministic id `kernel.integrate_tool` will mint for `name` — lets
    request_grant inspect prior state (a retracted grant) BEFORE re-installing."""
    return content_id({"cap": name, "effect": name, "impl": None})


def request_grant(k, host: str) -> dict:
    """Request LIVE egress to `host`. Forges the per-host egress capability
    (allowlist = exactly that host; the wire additionally enforces https-only and
    Morta approval per call) and routes the APPROVAL through the existing inbox
    spine: a Morta-gated grant-enactor capability is enqueued as a durable
    `inbox_item`, and ONLY a human `approve` enacts it (recording the capability-
    scoped approval the wire gate demands). Nothing auto-approves; until then
    every connection attempt raises EgressDenied before any socket.

    Idempotent: an already-approved host reports "live"; an already-queued request
    returns the existing pending item. A host whose grant was REVOKED is not
    resurrected silently — the stale approval is RETRACTed so the wire stays
    closed until a human approves again.

    Returns {"status": "live" | "pending" | "refused", ...} (ids only, no values)."""
    raw = str(host).strip()
    h = egress._host_of(raw if "//" in raw else "//" + raw)
    if not h:
        return {"status": "refused", "reason": f"no host in {raw!r} — fail closed"}

    name = _egress_cap_name(h)
    # A retracted (Morta-revoked) grant must NOT come back pre-approved: retract
    # the stale capability-scoped approval BEFORE re-installing, so the re-forged
    # capability requires a fresh human decision at the inbox.
    prior = k.weave().get(_predicted_cap_id(name))
    if prior is not None and prior.retracted:
        from decima.capability import approval_id
        stale = k.weave().get(approval_id(_predicted_cap_id(name), None))
        if stale is not None and not stale.retracted:
            k.weft.append(k.human.id, RETRACT, {"cell": stale.id})

    ecap, hosts = egress.install(k, allowlist=[h], name=name)
    if ecap in k.approvals:
        return {"status": "live", "host": h, "capability": ecap}

    # The grant ENACTOR: a Morta-gated effect whose ONLY act is to record the
    # capability-scoped approval on THIS egress cap — and only after the human
    # approves the inbox item (requires_approval gates any direct invoke).
    def _enact(impl, args, _k=k, _host=h, _ecap=ecap):
        from decima import executor
        if args.get("egress_capability") != _ecap or args.get("host") != _host:
            raise executor.ExecError(
                "grant enactment refused: args do not name the requested grant "
                "(fail closed)")
        cap = _k.weave().get(_ecap)
        allow = (cap.content.get("caveats", {}).get("egress_allowlist")
                 if cap is not None else None)
        if cap is None or cap.retracted or allow != [_host]:
            raise executor.ExecError(
                "grant enactment refused: the egress capability drifted from the "
                "requested {https, %r} grant (fail closed)" % _host)
        aid = _k.approve(_ecap)
        return {"out": f"egress grant LIVE: {{scheme https, host {_host}}} — "
                       f"every call still passes the wire gate",
                "approval": aid, "egress_capability": _ecap, "host": _host}

    gcap = k.integrate_tool(GRANT_EFFECT_PREFIX + h, _enact,
                            caveats={"requires_approval": True,
                                     "effect_class": "COMMUNICATION"})
    ib = ApprovalInbox(k)
    for item in ib.pending():                     # already queued → same item
        if item.content.get("capability") == gcap:
            return {"status": "pending", "host": h, "capability": ecap,
                    "grant_capability": gcap, "item": item.id}
    agent = k.weave().get(k.decima_agent_id)
    item_id = ib.enqueue(
        agent, gcap, {"host": h, "egress_capability": ecap},
        description=f"grant LIVE egress to https://{h} — confers exactly "
                    f"{{scheme https, host {h}}} at the wire gate")
    return {"status": "pending", "host": h, "capability": ecap,
            "grant_capability": gcap, "item": item_id}


def approved_egress_cap(k, host: str):
    """The id of a LIVE (unretracted, human-approved) egress capability whose
    allowlist covers `host` and which Decima's envelope holds — or None. This is
    the read side of the grant flow: it confers nothing (the wire re-checks all of
    it per call)."""
    w = k.weave()
    agent = w.get(k.decima_agent_id)
    env = set(agent.content.get("envelope", [])) if agent is not None else set()
    for c in w.of_type("capability"):
        if c.retracted or c.id not in env:
            continue
        if host in c.content.get("caveats", {}).get("egress_allowlist", []) \
                and c.id in k.approvals:
            return c.id
    return None


# ── the brain: bind an approved grant (never mint one) ──────────────────────
def bind_brain(k) -> str:
    """If the kernel's brain is a ModelBrain and an APPROVED api.anthropic.com
    egress grant exists on the Weft, bind it (`bind_egress`) so live calls pass
    the wire gate per call. Idempotent; binds only what a human already approved —
    it grants nothing, approves nothing. Returns a one-line redacted status."""
    from decima.agent import ModelBrain
    b = k.brain
    if not isinstance(b, ModelBrain):
        return ("brain: rule (deterministic offline default) — export "
                "ANTHROPIC_API_KEY and restart to configure the model driver")
    if b.transport is not None or b.egress is not None:
        return "brain: model — egress-bound; every live call passes the wire gate"
    cap = approved_egress_cap(k, BRAIN_HOST)
    if cap is None:
        return (f"brain: model configured but NOT live — no approved egress grant "
                f"for {BRAIN_HOST}; every live call fails closed (EgressDenied → "
                f"deterministic rule fallback). run `grant {BRAIN_HOST}`, then "
                f"`inbox` / `approve <id>`, then `live`.")
    agent = k.weave().get(k.decima_agent_id)
    b.bind_egress(k, agent, cap)
    return (f"brain: model — bound to approved egress grant {cap[:8]} "
            f"({BRAIN_HOST}, https only, wire-gated per call)")


# ── live engines: flip behind an approved grant (never mint one) ─────────────
# The live_wire adapter for each engine seam shape (see live_wire's survey):
#   post     transport(url, headers, body) — the canonical JSON seam (~27 engines)
#   get      same seam, GET verb (maps_engine, esign.fetch_status; body=None)
#   get2     transport(url, headers) — weather_engine's 2-arg GET seam
#   method   transport(url, headers, body, method=) — sms.py's per-call verb
#   put      S3-shaped PUT, success meta from headers (storage / cloud_storage)
#   get_raw  raw object bytes, never json-parsed (storage.get_object)
ENGINE_SHAPES = ("post", "get", "get2", "method", "put", "get_raw")


def _build_transport(k, agent, cap, shape, timeout, _open):
    """Construct the wire-gated transport for `shape` via live_wire — the ONLY
    live path (the gate re-runs the FULL rule of egress on every call; `_open`
    replaces the SOCKET, never the gate — the offline test seam)."""
    from decima import live_wire
    if shape == "post":
        return live_wire.gated_transport(k, agent, cap, timeout=timeout, _open=_open)
    if shape == "get":
        return live_wire.gated_transport(k, agent, cap, method="GET",
                                         timeout=timeout, _open=_open)
    if shape == "get2":
        return live_wire.gated_get_transport(k, agent, cap, timeout=timeout, _open=_open)
    if shape == "method":
        return live_wire.gated_method_transport(k, agent, cap, timeout=timeout, _open=_open)
    if shape == "put":
        return live_wire.gated_put_transport(k, agent, cap, timeout=timeout, _open=_open)
    return live_wire.gated_get_raw_transport(k, agent, cap, timeout=timeout, _open=_open)


def live_registry(k) -> dict:
    """The kernel's live-engine registry — `k.live_engines`, the Lane B seam the
    doctor reports. A dict `name → {engine, host, capability, shape, cell,
    transport}` created lazily on the kernel instance (no core edit, one registry
    per realm). An entry CONFERS NOTHING: the wire re-runs the full rule of
    egress on every call; the registry only RECORDS which human-approved engine
    currently holds a gated transport."""
    reg = getattr(k, "live_engines", None)
    if reg is None:
        reg = {}
        k.live_engines = reg
    return reg


def _entry_live(k, entry) -> bool:
    """Is a registry entry STILL backed by its human-approved grant? Re-verified
    against the Weft — retracted (Morta-revoked) or approval-less capabilities
    un-live their engine; the registry is never trusted over the log."""
    cap_id = entry.get("capability") if isinstance(entry, dict) else None
    if not cap_id:
        return False
    c = k.weave().get(cap_id)
    if c is None or getattr(c, "type", None) != "capability" or c.retracted:
        return False
    if entry.get("host") not in c.content.get("caveats", {}).get(
            "egress_allowlist", []):
        return False
    return cap_id in k.approvals


def _prune_dead_engines(k) -> dict:
    """Drop every registry entry whose grant no longer stands (revoked grant →
    the engine un-lives on the next doctor/flip). Returns the registry."""
    reg = live_registry(k)
    for name in [n for n, e in list(reg.items()) if not _entry_live(k, e)]:
        reg.pop(name, None)
    return reg


def activate_engine(k, name, host, *, shape: str = "post", timeout: int = 20,
                    _open=None) -> dict:
    """Flip the named engine LIVE — if and ONLY if a human already approved an
    egress grant covering `host`. Runs the SAME approved-grant test `bind_brain`
    rides (`approved_egress_cap`: unretracted · human-approved · held in Decima's
    envelope); with no approved grant the engine CANNOT flip — it stays offline,
    any stale registration is dropped, and the return names exactly what is
    missing (fail closed). With one, the engine's wire-gated transport is
    constructed via `live_wire` (`shape` picks the engine's seam; the gate
    re-runs the FULL rule of egress — allowlist · Morta · revocation ·
    `wire_decision` provenance — on EVERY call) and the engine is registered in
    `k.live_engines`, so the doctor reports it truthfully.

    MINTS NOTHING: no capability, no approval, no lease — a flip only RECORDS
    that an already-approved engine holds a gated transport. The flip lands an
    `engine_live` Cell on the Weft (name · host · capability id · shape —
    redacted, no secret, `instruction_eligible: False`) with a `flipped_via`
    edge to the approving grant. Idempotent: re-flipping an already-live engine
    on the same grant re-lands nothing.

    Returns {"status": "live", ..., "transport": <gated>, "cell": <id>} or
    {"status": "offline", "reason": ...} — never a partial registration."""
    eng = nfc(str(name).strip())
    raw = str(host).strip()
    h = egress._host_of(raw if "//" in raw else "//" + raw)
    reg = _prune_dead_engines(k)
    if not eng or not h:
        return {"status": "offline", "engine": eng or None,
                "reason": "an engine name and a host are required — fail closed"}
    if shape not in ENGINE_SHAPES:
        return {"status": "offline", "engine": eng, "host": h,
                "reason": f"unknown transport shape {shape!r} (one of "
                          f"{', '.join(ENGINE_SHAPES)}) — fail closed"}

    # THE approved-grant test (the same one bind_brain binds through): a live,
    # human-approved egress capability for this host, held in Decima's envelope.
    cap = approved_egress_cap(k, h)
    if cap is None:                       # no approved grant → NO flip, no entry
        reg.pop(eng, None)                # a failed flip never leaves a stale "live"
        return {"status": "offline", "engine": eng, "host": h,
                "reason": f"no approved egress grant for {h} — the engine stays "
                          f"offline (fail closed). run `grant {h}`, then `inbox` "
                          f"/ `approve <id>`, then flip again."}

    held = reg.get(eng)
    if held is not None and held.get("host") == h \
            and held.get("capability") == cap and held.get("shape") == shape:
        return {"status": "live", "engine": eng, "host": h, "capability": cap,
                "transport": held["transport"], "cell": held["cell"]}

    agent = k.weave().get(k.decima_agent_id)
    transport = _build_transport(k, agent, cap, shape, timeout, _open)

    # Record the flip on the Weft — redacted provenance, never a secret: the
    # engine name, its host, the APPROVING capability's id, the seam shape.
    content = {"engine": eng, "host": h, "capability": cap, "shape": shape,
               "instruction_eligible": False}
    cid = content_id({ENGINE_LIVE: content})
    if k.weave().get(cid) is None:        # same flip → one identity, one record
        assert_content(k.weft, k.decima_agent_id, cid, ENGINE_LIVE, content)
        assert_edge(k.weft, k.decima_agent_id, cid, "flipped_via", cap)
    reg[eng] = {"engine": eng, "host": h, "capability": cap, "shape": shape,
                "cell": cid, "transport": transport}
    return {"status": "live", "engine": eng, "host": h, "capability": cap,
            "transport": transport, "cell": cid}


# ── doctor: honest, redacted state ───────────────────────────────────────────
def doctor(k) -> dict:
    """The go-live state, honestly and REDACTED: wire armed?, egress grants
    (granted/pending/retracted), brain driver in effect + key PRESENCE (never a
    value), secrets held (names only), engines live vs test-mode (via the
    `k.live_engines` registry `activate_engine` populates — PRUNED against the
    Weft first, so a revoked grant un-lives its engine here; an empty list means
    nothing is live). No secret value, ever, in any field."""
    from decima.agent import ModelBrain
    w = k.weave()
    agent = w.get(k.decima_agent_id)
    env_ids = set(agent.content.get("envelope", [])) if agent is not None else set()

    grants = []
    for c in w.of_type("capability"):
        hosts = c.content.get("caveats", {}).get("egress_allowlist")
        if not hosts:
            continue
        grants.append({"capability": c.id, "hosts": sorted(hosts),
                       "approved": (c.id in k.approvals) and not c.retracted,
                       "retracted": bool(c.retracted),
                       "held": c.id in env_ids})
    pending = [{"item": c.id, "description": c.content.get("description")}
               for c in ApprovalInbox(k).pending()
               if str(c.content.get("capability_name", ""))
               .startswith(GRANT_EFFECT_PREFIX)]

    b = getattr(k, "golive_secrets", None)
    held_names = set(b._store) if b is not None else set()
    ref_names = {c.content.get("name") for c in w.of_type("credential")
                 if not c.retracted}
    secrets = [{"name": n,
                "status": ("held (in-broker, applied never disclosed)"
                           if n in held_names
                           else "reference-only (broker restarted — re-run "
                                "`secrets intake`)")}
               for n in sorted((held_names | ref_names) - {None})]

    brain = k.brain
    is_model = isinstance(brain, ModelBrain)
    bound = is_model and (brain.transport is not None or brain.egress is not None)
    key_present = bool(getattr(brain, "api_key", None)) \
        or ("anthropic" in held_names) or bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {
        "wire_armed": wire.armed(),
        "brain": {"driver": "model" if is_model else "rule",
                  "model": getattr(brain, "model", None),
                  "key": "present" if key_present else "absent",
                  "egress_bound": bound,
                  "effective": ("model (live, wire-gated)" if bound
                                else "rule (deterministic offline)")},
        "egress": grants,
        "pending_grants": pending,
        "secrets": secrets,
        "engines": {"live": sorted(_prune_dead_engines(k)),
                    "note": "engines run test-mode (injected transports) unless "
                            "flipped live behind a human-approved egress grant "
                            "(activate_engine → k.live_engines; re-verified "
                            "against the Weft, wire-gated per call) — an "
                            "empty list means NOTHING is live"},
    }


def doctor_lines(k) -> list:
    """`doctor` rendered as shell lines (redacted; no value ever printed)."""
    d = doctor(k)
    br = d["brain"]
    lines = [
        "wire guard: %s — ungated urlopen raises EgressDenied before any socket"
        % ("ARMED" if d["wire_armed"] else "NOT ARMED (!)"),
        "brain: driver=%s model=%s key=%s → in effect: %s"
        % (br["driver"], br["model"] or "—", br["key"], br["effective"]),
    ]
    if d["egress"]:
        lines.append("egress grants:")
        for g in d["egress"]:
            state = ("LIVE (approved)" if g["approved"]
                     else "revoked" if g["retracted"] else "awaiting approval")
            lines.append("  %s  https://{%s}  %s"
                         % (g["capability"][:8], ", ".join(g["hosts"]), state))
    else:
        lines.append("egress grants: (none — nothing may leave the box; "
                      "`grant <host>` to request one)")
    for p in d["pending_grants"]:
        lines.append("  pending #%s  %s" % (p["item"][:8], p["description"]))
    if d["secrets"]:
        lines.append("secrets (names only — values never leave the broker):")
        for s in d["secrets"]:
            lines.append("  %s: %s" % (s["name"], s["status"]))
    else:
        lines.append("secrets: (none held — export DECIMA_SECRET_<NAME> and run "
                     "`secrets intake`)")
    eng = d["engines"]
    lines.append("engines live: %s — %s"
                 % (", ".join(eng["live"]) or "(none)", eng["note"]))
    return lines


# ── boot wiring (run.py) ─────────────────────────────────────────────────────
def boot(k, environ=None) -> list:
    """Boot-time go-live wiring. NO provider secret in the environment → returns
    [] and touches NOTHING (behavior identical to before this module existed).
    Otherwise: announced (never silent) intake into the broker, then bind the
    model brain to an already-APPROVED api.anthropic.com grant if one exists.
    Grants nothing, approves nothing; all output is redacted."""
    env = os.environ if environ is None else environ
    if not _env_secrets(env):
        return []
    lines = ["[go-live] secret %r: %s (value held by the broker — never on the "
             "Weft, never shown)" % (r["name"], r["status"])
             for r in intake_env(k, environ=env)]
    lines.append("[go-live] " + bind_brain(k))
    return lines
