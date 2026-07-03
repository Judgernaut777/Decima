"""TERMINALS-AS-CITIZENS — admit a terminal / external tool as an ATTENUATED principal.

A terminal, a CLI agent, a mounted MCP server — anything that wants to PARTICIPATE in
the realm — is admitted as a first-class CITIZEN: a principal with its own key, spawned
holding NOTHING but a capability envelope attenuated DOWNHILL from authority an existing
realm principal already held. Admission is participation, never ambient access. The laws
this lane keeps, by SHAPE:

  - NO AMBIENT AUTHORITY (Law 2). A citizen's envelope is minted ONLY via
    `capability.attenuate` from an existing grant, and the derived grant is PROVEN
    downhill (`capability.attenuation_valid`) before anything lands on the Weft — an
    effect-allowlist (the sandbox caveat the executor enforces), a narrowed target
    scope, shrunk use/rate/budget bounds, and the effect class's Morta floor
    (`with_morta_floor`) merged in so it can never be proposed away. A widening
    proposal is REJECTED, never silently clamped. A citizen admitted with no grant can
    invoke nothing (default-deny).
  - THE ORDINARY GATE, NOT A SIDE DOOR. A citizen invoke routes through
    `kernel.invoke` → `capability.authorize`: possession proof, envelope, grantee,
    downhill delegation, every caveat (budget / lease / approval / sandbox). This
    module adds ONE gate of its own — the narrowed TARGET SCOPE, checked before the
    invoke is even attempted — and takes nothing away.
  - OUTPUT IS UNTRUSTED DATA, NEVER INSTRUCTION. Whatever a citizen's tool emits
    crosses the trust boundary through the disposition router (`k.ingest`,
    trusted=False): it is recorded `instruction_eligible=False`, an injection is kept
    as flagged DATA, and no citizen output can ever elevate itself to a task, an
    invoke, or a policy (the recall-vs-instruct law).
  - EVERYTHING ON THE WEFT (Laws 1/4). The admission and every citizen action —
    allowed or denied — is a Cell with edges, so `citizens(k)` (who is admitted,
    holding what) is a pure fold, and the audit reads like a story.

MCP bridge (thin, over decima/mcp + decima/mcp_server): `mount_citizen` imports an
external MCP server via `mcp.mount` (each tool a gated realm capability, foreign
default Morta-gated) and admits the server AS a citizen holding only ATTENUATED grants
of those tools. Decima's own exposed tools stay gated when a citizen calls them —
`mcp_server.handle` routes tools/call through `kernel.invoke`, so authorize + Morta run
exactly as for any native invoke.

Ints-not-floats: every recorded numeric (ticks, bounds) is an int; a float in a
narrowing or in citizen args is refused at the door. Pure composition over the public
capability / kernel / model / mcp APIs — no core edit.
"""
from decima import capability
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

CITIZEN_ADMISSION = "citizen_admission"
CITIZEN_ACTION = "citizen_action"


class CitizenError(Exception):
    """A refused admission / re-attenuation / malformed citizen request. Raised BEFORE
    anything is asserted — a refused narrowing writes nothing (fail closed)."""
    pass


def _no_floats(x, where: str) -> None:
    """Ints-not-floats at the door: recorded/signed content carries no float, ever."""
    if isinstance(x, float):
        raise CitizenError(f"{where}: floats are forbidden in recorded content "
                           f"(ints-not-floats), got {x!r}")
    if isinstance(x, dict):
        for kk, vv in x.items():
            _no_floats(vv, f"{where}.{kk}")
    elif isinstance(x, (list, tuple, set)):
        for vv in x:
            _no_floats(vv, where)


def _downhill_or_die(child: dict, parent: dict) -> None:
    """The admission law: a citizen grant is valid ONLY if it is a structural
    narrowing of its parent (permitted-invocation set ⊆ the parent's) AND the effect
    class's Morta floor survives. Refuses — asserting NOTHING — otherwise. Authority
    only ever flows downhill; there is no citizen path that widens."""
    ok, why = capability.attenuation_valid(child, parent)
    if not ok:
        raise CitizenError(f"refused: not a downhill attenuation — {why}")
    floor = capability.morta_floor(child.get("effect", ""))
    for fk, fv in floor.items():
        if fv and not child.get("caveats", {}).get(fk):
            raise CitizenError(f"refused: Morta floor caveat {fk!r} dropped "
                               f"(the floor is unstrippable)")


def _narrowed_grant(k, *, parent_id: str, grantee: str, granter: str,
                    narrow: dict, name: str) -> tuple[str, dict]:
    """Mint ONE citizen grant: attenuate downhill from `parent_id`, apply the
    narrowing (effect-allowlist → the sandbox caveat the executor enforces; target →
    a shrunk scope; the rest → stricter caveats via `capability.attenuate`), merge the
    Morta floor in, PROVE the result downhill, then assert it. Returns (cap_id, content).
    """
    w = k.weave()
    parent = w.get(parent_id)
    if parent is None or parent.type != "capability":
        raise CitizenError(f"admission refused: {parent_id!r} is not a capability")
    if parent.retracted:
        raise CitizenError("admission refused: the base capability is revoked "
                           "(nothing to attenuate from)")
    if not isinstance(narrow, dict) or not narrow:
        raise CitizenError(
            "admission refused: a citizen enters NARROWED — pass narrow= with an "
            "effect-allowlist / target scope / use bounds (never the parent's full envelope)")
    narrow = dict(narrow)
    _no_floats(narrow, "narrow")
    effects = narrow.pop("effects", None)
    target = narrow.pop("target", None)
    stricter = narrow                                   # remaining keys ride as caveats

    parent_cav = parent.content.get("caveats", {}) or {}
    if effects is not None:
        allow = sorted({nfc(str(e)) for e in effects})
        psb = parent_cav.get("sandbox") if isinstance(parent_cav.get("sandbox"), dict) else {}
        if psb.get("effects") is not None:              # an allowlist can only SHRINK
            allow = sorted(set(allow) & set(psb["effects"]))
        if parent.content["effect"] not in allow:
            raise CitizenError(
                "admission refused: the effect-allowlist must include the capability's "
                f"own effect {parent.content['effect']!r} (an empty envelope is spelled "
                "from_cap=None, not a self-excluding allowlist)")
        stricter["sandbox"] = {**psb, "effects": allow}

    # The effect class's permanent minimum caveats are merged IN (floor wins) so a
    # citizen grant for a gated effect is BORN with its floor intact.
    stricter = capability.with_morta_floor(parent.content["effect"], stricter)
    att = capability.attenuate(parent.content, stricter, parent_id,
                               grantee=grantee, granter=granter)
    if target is not None:
        att["target"] = nfc(str(target))                # the scope SHRINKS here
    _downhill_or_die(att, parent.content)               # proven ⊆ parent, or nothing lands
    att_id = content_id({"citizen_grant": nfc(name), "of": parent_id, "to": grantee,
                         "n": int(k.weft.lamport)})
    assert_content(k.weft, granter, att_id, "capability", att)
    return att_id, att


def admit_citizen(k, name: str, *, from_cap: str | None = None,
                  narrow: dict | None = None, author: str | None = None) -> dict:
    """Admit a terminal / external tool as a CITIZEN: a principal with its own key,
    holding ONLY a grant attenuated downhill from `from_cap` under `narrow`
    (effect-allowlist / target scope / use-rate-budget bounds; the Morta floor is
    preserved). `from_cap=None` admits a citizen with an EMPTY envelope — it may
    participate (be addressed, be audited) but can invoke NOTHING (default-deny).
    The admission is recorded on the Weft. Returns
    {citizen, grant, principal, admission}.
    """
    w = k.weave()
    granter_cell = w.get(author or k.decima_agent_id)
    if granter_cell is None or "principal" not in (granter_cell.content or {}):
        raise CitizenError("admission refused: no admitting agent (author) found")
    granter = granter_cell.content["principal"]
    name = nfc(name)
    principal = k.keyring.mint(name, "agent")           # its OWN key — it signs itself

    envelope, budget = [], 0
    if from_cap is not None:
        att_id, att = _narrowed_grant(k, parent_id=from_cap, grantee=principal.id,
                                      granter=granter, narrow=narrow or {}, name=name)
        envelope = [att_id]
        budget = int(att["caveats"].get("budget", 0) or 0)

    citizen_id = content_id({"citizen": name, "by": granter, "n": int(k.weft.lamport)})
    assert_content(k.weft, granter, citizen_id, "agent", {
        "principal": principal.id,
        "objective": f"citizen:{name} — participate within the narrowed envelope",
        "envelope": envelope, "budget": budget, "sandbox": False,
        "citizen": True, "citizen_name": name,
        "admitted_from": from_cap, "lineage": granter_cell.id,
    })
    adm_id = content_id({"admission": citizen_id, "n": int(k.weft.lamport)})
    assert_content(k.weft, granter, adm_id, CITIZEN_ADMISSION, {
        "citizen": citizen_id, "name": name, "principal": principal.id,
        "grants": list(envelope), "from_cap": from_cap, "by": granter,
        "at": int(k.weft.lamport),
    })
    assert_edge(k.weft, granter, citizen_id, "admitted_via", adm_id)
    return {"citizen": citizen_id, "grant": envelope[0] if envelope else None,
            "principal": principal.id, "admission": adm_id}


def citizen_invoke(k, citizen_id: str, cap_id: str, args: dict | None = None,
                   nonce: str | None = None) -> dict:
    """One citizen action, through the ORDINARY gate. Checks the narrowed TARGET
    SCOPE (this module's one own gate — `authorize` does not compare args to the
    grant's target selector), then routes through `kernel.invoke`, so possession
    proof, envelope, grantee, downhill delegation, and every caveat (budget / lease /
    Morta approval / sandbox effect-allowlist) run exactly as for any native invoke.
    The action — allowed OR denied — is recorded as a `citizen_action` Cell, and a
    successful tool OUTPUT crosses the trust boundary through the disposition router
    as UNTRUSTED DATA (instruction_eligible=False, never obeyed)."""
    w = k.weave()
    cz = w.get(citizen_id)
    if cz is None or cz.type != "agent" or not cz.content.get("citizen"):
        raise CitizenError(f"{citizen_id!r} is not an admitted citizen")
    args = dict(args or {})
    _no_floats(args, "args")
    principal = cz.content["principal"]

    cap = w.get(cap_id)
    reason = None
    scope = "*"
    if cap is None or cap.type != "capability":
        reason = "no such capability"
    else:
        scope = cap.content.get("target", "*")
        req = nfc(str(args.get("target", scope)))
        if scope != "*" and req != scope:   # the envelope gate: outside the narrowed target scope ⇒ DENIED
            reason = (f"target {req!r} outside the citizen's narrowed scope {scope!r} "
                      "(authority does not follow curiosity)")

    if reason is not None:
        res = {"denied": reason}
    else:
        res = k.invoke(cz, cap_id, args, nonce=nonce)

    outcome = ("denied" if "denied" in res
               else "proposed" if "proposed" in res
               else res.get("status", "SUCCEEDED"))
    act_id = content_id({"citizen_action": citizen_id, "cap": cap_id,
                         "n": int(k.weft.lamport)})
    assert_content(k.weft, principal, act_id, CITIZEN_ACTION, {
        "citizen": citizen_id, "cap": cap_id,
        "target": nfc(str(args.get("target", scope))),
        "outcome": outcome, "reason": res.get("denied"),
        "invoke_event": res.get("invoke_event"),
        "at": int(k.weft.lamport),
    })
    assert_edge(k.weft, principal, act_id, "acted_as", citizen_id)

    # OUTPUT IS DATA: whatever the citizen's tool emitted is ingested UNTRUSTED —
    # the disposition router records it instruction_eligible=False; an injection is
    # kept as flagged DATA and can never elevate to a task / invoke / policy.
    if "ok" in res:
        out = res["ok"].get("out")
        if out:
            d = k.ingest(f"citizen:{cz.content.get('citizen_name', citizen_id)}",
                         str(out), trusted=False)
            assert_edge(k.weft, principal, d["intake"], "emitted_by", act_id)
            res["disposition"] = d
    res["action_cell"] = act_id
    return res


def re_attenuate(k, citizen_id: str, cap_id: str, *, target: str | None = None,
                 caveats: dict | None = None, grantee: str | None = None,
                 name: str = "citizen-sub") -> str:
    """A citizen may pass its authority ON — but only DOWNHILL. The proposal is taken
    LITERALLY (target / caveat overrides applied verbatim) and then structurally
    PROVEN ⊆ the parent grant: a widening — a broader target, a loosened or dropped
    caveat, a stripped Morta floor — is REJECTED (`CitizenError`), never silently
    clamped-and-accepted. Returns the new (narrower) grant's cell id."""
    w = k.weave()
    cz = w.get(citizen_id)
    if cz is None or cz.type != "agent" or not cz.content.get("citizen"):
        raise CitizenError(f"{citizen_id!r} is not an admitted citizen")
    if cap_id not in set(cz.content.get("envelope", [])):
        raise CitizenError("refused: the citizen does not hold that grant "
                           "(nothing to attenuate)")
    parent = w.get(cap_id)
    if parent is None or parent.type != "capability" or parent.retracted:
        raise CitizenError("refused: the grant is missing or revoked")
    principal = cz.content["principal"]

    child = dict(parent.content)
    child["caveats"] = {**(parent.content.get("caveats", {}) or {}), **dict(caveats or {})}
    _no_floats(child["caveats"], "caveats")
    if target is not None:
        child["target"] = nfc(str(target))
    child["parent"] = cap_id
    child["grantee"] = grantee or principal
    child["granter"] = principal
    _downhill_or_die(child, parent.content)             # widening dies HERE, loudly
    att_id = content_id({"citizen_grant": nfc(name), "of": cap_id,
                         "to": child["grantee"], "n": int(k.weft.lamport)})
    assert_content(k.weft, principal, att_id, "capability", child)
    return att_id


def mount_citizen(k, server_name: str, transport, *, trusted: bool = False,
                  narrow: dict | None = None, author: str | None = None) -> dict:
    """The MCP bridge, citizen-shaped: mount an external MCP server (`mcp.mount` —
    each tool becomes a gated realm capability, foreign default Morta-gated) and admit
    the server AS a citizen whose envelope holds only ATTENUATED grants of those tool
    capabilities (per-tool effect-allowlist by default; pass `narrow` to shrink target
    / uses further). The tools' caveats — including `requires_approval` — persist
    downhill, so a Morta-gated tool stays Morta-gated for the citizen. Returns the
    admission dict plus {mounted: [realm cap ids], grants: [citizen grant ids]}."""
    from decima import mcp as _mcp
    cap_ids = _mcp.mount(k, server_name, transport, trusted=trusted, author=author)
    if not cap_ids:
        raise CitizenError(f"mount_citizen: MCP server {server_name!r} exposed no tools")
    w = k.weave()
    granter_cell = w.get(author or k.decima_agent_id)
    granter = granter_cell.content["principal"]

    def _narrow_for(cid):
        n = dict(narrow or {})
        n.setdefault("effects", [w.get(cid).content["effect"]])
        return n

    adm = admit_citizen(k, server_name, from_cap=cap_ids[0],
                        narrow=_narrow_for(cap_ids[0]), author=author)
    grants = [adm["grant"]]
    for extra in cap_ids[1:]:
        att_id, _att = _narrowed_grant(k, parent_id=extra, grantee=adm["principal"],
                                       granter=granter, narrow=_narrow_for(extra),
                                       name=nfc(server_name))
        k.grant(att_id, adm["citizen"])                 # into the citizen's envelope
        grants.append(att_id)
    return {**adm, "mounted": list(cap_ids), "grants": grants}


def citizens(k) -> list:
    """The realm's current citizens and their (narrowed) envelopes — a pure fold over
    the Weft. A projection confers NO authority: reading this list grants nothing."""
    w = k.weave()
    out = []
    for c in w.of_type("agent"):
        if c.retracted or not c.content.get("citizen"):
            continue
        env = []
        for gid in c.content.get("envelope", []):
            g = w.get(gid)
            if g is None or g.type != "capability" or g.retracted:
                continue
            env.append({"grant": gid, "effect": g.content.get("effect"),
                        "target": g.content.get("target", "*"),
                        "caveats": dict(g.content.get("caveats", {}) or {})})
        out.append({"citizen": c.id, "name": c.content.get("citizen_name"),
                    "principal": c.content.get("principal"),
                    "admitted_from": c.content.get("admitted_from"),
                    "envelope": env})
    return out
