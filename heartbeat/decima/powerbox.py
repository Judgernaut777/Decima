"""The Powerbox — a trusted capability broker (MORTA_CAPABILITIES §7).

An agent does not mint its own authority and does not receive broad grants "just
in case". It *asks* the powerbox for the narrowest capability that serves a
stated purpose, and the broker — under realm policy — issues a scoped, attenuated
grant bound to that agent's principal. Least authority becomes the default path,
not a discipline someone has to remember.

The flow (MORTA §7), each step on the Log so it is auditable and time-travelable:

  1. the agent asserts a request: capability name, purpose, and minimum scope;
  2. the broker searches its brokerable sources for a compatible grant;
  3. it proposes the NARROWEST attenuation that covers the request;
  4. policy auto-approves low-risk requests or routes a human approval (Morta);
  5. the grant event binds holder + caveats (and carries the Morta floor);
  6. use is audited; the request cell records the decision.

The broker holds *source* grants (root-issued, grantee = the broker) and only
ever issues things ⊆ those — `capability.attenuation_valid` proves the narrowing
structurally before issuing, so the broker can never widen authority. It is NOT
the kernel: it has no INVOKE authority and the ocap check (`authorize`) still
gates every use of what it hands out. The broker never sees secrets (it would
issue broker handles; not modelled in the prototype).
"""
from decima.weft import ASSERT
from decima.hashing import content_id
from decima import capability
from decima.capability import (capability_content, attenuate, attenuation_valid,
                               with_morta_floor)

# Risk policy (a thin stand-in for a realm policy Cell). Low-risk effects are
# auto-approved; Morta-floored effects always route a human approval; anything
# unclassified fails safe to a human, never to auto-grant.
LOW_RISK_EFFECTS = {"echo", "transform"}

# What this broker is allowed to hand out, and the source scope it brokers from.
# (name, effect, source caveats) — the source is the widest grant the broker holds.
DEFAULT_BROKERABLE = (
    ("echo",  "echo",      {}),
    ("shout", "transform", {}),
    ("shell", "shell",     {"budget": 100}),
)


def policy_decision(effect: str, purpose: str) -> str:
    """Return 'auto' (issue now), 'approval' (issue, but gate on a human), or
    'deny'. Morta-floored effects are never auto-approved (MORTA §4)."""
    if capability.morta_floor(effect):
        return "approval"
    if effect in LOW_RISK_EFFECTS:
        return "auto"
    return "approval"            # default-to-human for the unclassified (fail safe)


class Powerbox:
    def __init__(self, kernel, brokerable=DEFAULT_BROKERABLE):
        self.k = kernel
        self.principal = kernel.keyring.mint("powerbox", "broker")
        self.sources: dict[str, str] = {}     # cap name -> source cap id (broker-held)
        for name, effect, caveats in brokerable:
            self.sources[name] = self._install_source(name, effect, caveats)

    # -- broker source authority (root installs it; grantee = the broker) ------
    def _install_source(self, name, effect, caveats) -> str:
        cid = content_id({"broker_source": name, "effect": effect})
        content = capability_content(name=name, effect=effect, caveats=caveats,
                                     grantee=self.principal.id, granter=self.k.root.id)
        self.k.weft.append(self.k.root.id, ASSERT,
                           {"cell": cid, "type": "capability", "content": content})
        return cid

    # -- the request → propose → gate → issue path -----------------------------
    def request(self, requester_cell, name, purpose, scope=None, duration=None) -> dict:
        """An agent asks for the narrowest grant of `name` serving `purpose`.
        Returns {granted, needs_approval} | {denied} and records the decision."""
        scope = scope or {}
        req_id = content_id({"cap_request": name, "by": requester_cell.id,
                             "purpose": purpose, "n": self.k.weft.lamport})
        self._record(req_id, {"requester": requester_cell.id, "name": name,
                              "purpose": purpose, "scope": scope, "duration": duration,
                              "status": "received"})

        # 2. search compatible source grants the broker holds.
        source_id = self.sources.get(name)
        if source_id is None:
            return self._close(req_id, {"denied": f"no brokerable source for {name!r}"})
        base = self.k.weave().get(source_id)
        if base is None or base.retracted:
            return self._close(req_id, {"denied": f"source for {name!r} not live"})

        # 3. propose the narrowest attenuation, then 4. apply the Morta floor +
        #    policy. The floor is merged FIRST so a 'deny'/'approval' decision can
        #    never be talked out of by the requested scope.
        stricter = self._narrowest(scope, duration)
        stricter = with_morta_floor(base.content["effect"], stricter)
        decision = policy_decision(base.content["effect"], purpose)
        if decision == "deny":
            return self._close(req_id, {"denied": f"policy forbids {name!r} for this purpose"})
        if decision == "approval":
            stricter["requires_approval"] = True

        # 5. issue: prove the narrowing, then bind holder + caveats.
        att = attenuate(base.content, stricter, base.id,
                        grantee=requester_cell.content["principal"],
                        granter=self.principal.id)
        valid, why = attenuation_valid(att, base.content)
        if not valid:                                  # belt and braces — must hold
            return self._close(req_id, {"denied": f"attenuation invalid: {why}"})
        grant_id = self._issue(requester_cell, att, base.content["name"], req_id)
        return self._close(req_id, {"granted": grant_id,
                                    "needs_approval": decision == "approval",
                                    "caveats": att["caveats"]})

    def _narrowest(self, scope, duration) -> dict:
        """The TIGHTEST scope that still covers the request: only what was asked
        for, nothing wider. Unmentioned dimensions inherit the source's (and the
        floor's) constraints unchanged."""
        stricter = {}
        if "budget" in scope:
            stricter["budget"] = int(scope["budget"])
        if duration is not None:
            stricter["expires"] = int(duration)        # thin: a relative-TTL marker
        for k in ("requires_approval", "sandbox_only", "read_only", "reversible_only"):
            if scope.get(k):
                stricter[k] = scope[k]
        return stricter

    def _issue(self, requester_cell, att, name, req_id) -> str:
        """Assert the attenuated grant (granter = broker) and add it to the
        requester's envelope. The broker authors both, so the grant's provenance
        names the broker — the audit trail of who handed out what."""
        grant_id = content_id({"broker_grant": name,
                               "to": requester_cell.content["principal"], "req": req_id})
        self.k.weft.append(self.principal.id, ASSERT,
                           {"cell": grant_id, "type": "capability", "content": att})
        agent = self.k.weave().get(requester_cell.id)
        env = list(agent.content.get("envelope", []))
        if grant_id not in env:
            env.append(grant_id)
        self.k.weft.append(self.principal.id, ASSERT,
                           {"cell": requester_cell.id, "type": "agent",
                            "content": {**agent.content, "envelope": env}})
        return grant_id

    # -- audit: the request is a Cell; its lifecycle is folded from the Weave ---
    def _record(self, req_id, content):
        self.k.weft.append(self.principal.id, ASSERT,
                           {"cell": req_id, "type": "cap_request", "content": content})

    def _close(self, req_id, result) -> dict:
        req = self.k.weave().get(req_id)
        status = ("denied" if "denied" in result
                  else "needs_approval" if result.get("needs_approval")
                  else "granted")
        self._record(req_id, {**req.content, "status": status,
                              "decision": {k: v for k, v in result.items()}})
        return {"request": req_id, **result}

    def requests(self) -> list:
        """All brokered requests, folded from the Weave — the broker's audit log."""
        return self.k.weave().of_type("cap_request")
