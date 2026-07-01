"""The kernel — wires the primitives into a living system and boots Decima.

Boot:
  - mint root (authority source), executor, reckoner (Nona), human, and the
    Decima orchestrator's own principal
  - ASSERT bootstrap capabilities (echo, shell, forge) GRANTED to Decima
  - ASSERT the Decima orchestrator agent, bound to its principal, holding them
Every agent — Decima and every subagent — has its OWN key and signs its own
INVOKEs. Authority is a signed grant to a principal, never id-possession. The
first capability Decima ships with is the capability to author capabilities
(`forge`) — the bootstrap, Nona's first beat.
"""
import os
import time

from decima.crypto import Keyring
from decima.weft import Weft, ASSERT, RETRACT, INVOKE, ATTEST
from decima.weave import Weave
from decima.capability import (capability_content, authorize, attenuate,
                               build_proof, verify_proof)
from decima.hashing import content_id, nfc
from decima.agent import make_brain, Action, _find_named
from decima import executor, memory


class Kernel:
    MAX_DELEGATION_DEPTH = 2   # Decima(0) → worker(1) → sub-worker(2); 2 cannot delegate
    ORG_POLICY_DENIAL_LIMIT = 2   # refuse a HELD cap after this many denied delegations w/ 0 completions

    def __init__(self, db_path: str, fresh: bool = False):
        seed_path = db_path + ".keys"
        if fresh:
            for p in (db_path, seed_path):
                if os.path.exists(p):
                    os.remove(p)

        seed = bytes.fromhex(open(seed_path).read().strip()) if os.path.exists(seed_path) else None
        self.keyring = Keyring(seed)
        if seed is None:
            with open(seed_path, "w") as f:
                f.write(self.keyring.master.hex())

        self.weft = Weft(db_path, self.keyring)
        self.brain = make_brain()
        self.spent: dict[str, float] = {}     # in-memory budget ledger (seam)
        self.approvals: set[str] = set()       # capabilities approved this session

        self.root = self.keyring.mint("root", "root")
        self.executor = self.keyring.mint("executor", "executor")
        self.reckoner = self.keyring.mint("nona", "reckoner")
        self.human = self.keyring.mint("you", "human")
        self.decima = self.keyring.mint("decima", "agent")

        if self.weft.count() == 0:
            self._boot()
        self.decima_agent_id = self._find_decima()

    # -- boot --------------------------------------------------------------
    def _boot(self):
        echo = self._assert_cap("echo", "echo")
        shell = self._assert_cap("shell", "shell", caveats={"budget": 100})
        forge = self._assert_cap("forge", "forge")
        # Browser capabilities are SPLIT (specs/BROWSER_WORKER.md): observe is
        # read-only and auto-allowed; publish is an outward effect, Morta-gated.
        observe = self._assert_cap("browser.observe", "browser", impl={"op": "observe"})
        publish = self._assert_cap("browser.publish", "browser",
                                   caveats={"requires_approval": True}, impl={"op": "publish"})
        agent_id = content_id({"agent": "decima-orchestrator"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": agent_id, "type": "agent",
            "content": {
                "principal": self.decima.id,
                "objective": "serve the user; allot capability to the work",
                "brain": "rules-stub",
                "envelope": [echo, shell, forge, observe, publish],
                "budget": 100,
                "sandbox": False,
            },
        })

    def _assert_cap(self, name, effect, caveats=None, impl=None) -> str:
        cap_id = content_id({"cap": name, "effect": effect, "impl": impl})
        content = capability_content(name=name, effect=effect, caveats=caveats or {},
                                     impl=impl, grantee=self.decima.id, granter=self.root.id)
        self.weft.append(self.root.id, ASSERT,
                         {"cell": cap_id, "type": "capability", "content": content})
        return cap_id

    def _find_decima(self) -> str:
        for c in self.weave().of_type("agent"):
            if c.content.get("brain"):
                return c.id
        raise RuntimeError("no orchestrator agent found")

    # -- projections -------------------------------------------------------
    def weave(self, upto_seq=None) -> Weave:
        return Weave.fold(self.weft, upto_seq)

    def principal_for(self, agent_cell) -> str:
        # Each agent signs with its OWN bound principal — possession of its key.
        return agent_cell.content["principal"]

    def lease_uses(self, weave, cap_id) -> int:
        """Deterministic count of prior INVOKEs this capability has authorized — the
        spend side of a single-use / max_uses lease. Folded from the Weave (the
        INVOKE events that named `cap_id`), so it is a pure function of the Log: it
        re-derives identically on every fold and time-travels like all state. A lease
        is exhausted once this reaches `max_uses` (LEASE caveat). Only INVOKEs that
        were authorized through THIS exact grant id count toward its budget of uses."""
        return sum(1 for inv in weave.invocations if inv.cap == cap_id)

    # -- the core action path: authorize -> INVOKE -> execute -> ASSERT ----
    def invoke(self, agent_cell, cap_id, args) -> dict:
        w = self.weave()
        holder = self.principal_for(agent_cell)
        spent = self.spent.get(agent_cell.id, 0.0)
        # LEASE inputs (LEASE1): "now" is the LOGICAL frontier time (lamport), never
        # wall-clock — determinism. `prior_uses` is the count of INVOKEs this cap has
        # already authorized, folded deterministically from the Weave; together they
        # gate the time-locked (`expires_at`) + single-use (`max_uses`) lease caveats.
        now = self.weft.lamport
        prior_uses = self.lease_uses(w, cap_id)
        # Bind the proof to THIS exact request: verb + body + nonce + frontier.
        nonce = os.urandom(16).hex()
        parents = [self.weft.head] if self.weft.head else []
        body = {"cap": cap_id, "args": args}
        proof = build_proof(w, self.keyring, holder, cap_id, INVOKE, body, nonce, parents)
        ok, reason = verify_proof(w, self.keyring, agent_cell, proof, INVOKE, body,
                                  nonce, parents, spent, self.approvals,
                                  now=now, prior_uses=prior_uses)
        if not ok:
            return {"denied": reason}

        cap = w.get(cap_id)
        # LIVE1 — autonomy gate at the invoke boundary. ocap has said this principal MAY
        # invoke; the ladder now says HOW autonomously it may act on THIS effect_class.
        # This generalizes the delegate-time governance gate (LOOP1) to invoke-time:
        # consult the per-(agent, capability) rung and refuse / propose / require approval
        # BEFORE the effect runs. INERT by default — if no rung is set for (agent, cap),
        # behaves exactly as before. The gate decision is recorded on the Weft.
        gate = self._autonomy_gate(agent_cell, cap, args)
        if gate is not None:
            return gate

        # RECEIPT-HARDENING: validate the receipt `cost` BEFORE any effect runs, so a
        # malformed cost fails loud and writes NOTHING (no INVOKE, no effect, no
        # receipt). Signed receipt content is integer money/units — never a float,
        # never a bool-as-int. (Spend accounting below still float()s for its ledger
        # arithmetic; only the receipt's recorded `cost` is the validated int.)
        cost = args.get("cost", 0)
        if not (isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0):
            raise ValueError(f"receipt cost must be a non-negative int (not bool), got {cost!r}")

        # The INVOKE carries its AuthorizationProof and is signed by the holder's key.
        inv = self.weft.append(holder, INVOKE,
                               {**body, "nonce": nonce, "proof": proof}, authorized=cap_id)
        # SB1: enforce the capability's sandbox profile at the executor boundary.
        # ocap already said this principal MAY invoke; the sandbox bounds what the
        # handler MAY TOUCH (network/fs/effect-allowlist). A violation — or a definite
        # exec error — is refused and recorded as a FAILED receipt, never a crash.
        sandbox = cap.content.get("caveats", {}).get("sandbox")
        try:
            result = executor.execute(cap.content["effect"], cap.content.get("impl"),
                                      args, sandbox=sandbox)
        except (executor.SandboxViolation, executor.ExecError) as e:
            code = "sandbox" if isinstance(e, executor.SandboxViolation) else "exec"
            result = {"status": executor.FAILED, "out": None,
                      "error": {"code": code, "retryable": False, "message": str(e)}}
        if result.get("status") != executor.FAILED:
            self.spent[agent_cell.id] = spent + float(args.get("cost", 0))
        # The completion is a separate ASSERT (WEFT §6): the `result` cell is an
        # EffectReceipt (WEFT §8) causally descending from the INVOKE. It carries
        # `status` — SUCCEEDED / FAILED / UNKNOWN — so an ambiguous effect is
        # recorded as UNKNOWN, never a fabricated outcome (FOLD §11 #8). The
        # idempotency key is the invocation nonce (one logical op = one INVOKE);
        # effect_class travels on the capability's caveats (defaults to READ).
        status = result.get("status", executor.SUCCEEDED)
        rid = content_id({"result_of": inv.id})
        # The idempotency key is the invocation nonce by default (one logical op =
        # one INVOKE). A caller re-attempting the SAME logical op may pass an explicit
        # `idempotency` in args to reuse a key across attempts — the seam that lets a
        # later definite receipt reconcile an earlier UNKNOWN (below). Existing callers
        # pass no such key, so they keep the unique-nonce behavior unchanged.
        idem = args.get("idempotency", nonce)
        receipt = {"of": inv.id, "cap": cap.content["name"], **result,
                   "status": status, "executor": self.executor.id, "attempt": 0,
                   "idempotency": idem, "cost": cost,
                   "effect_class": cap.content.get("caveats", {}).get("effect_class", "READ")}
        # Multi-attempt reconciliation (WEFT §8): if this receipt is DEFINITE
        # (SUCCEEDED/FAILED) and a PRIOR receipt for the same idempotency key is still
        # UNKNOWN, mark that THIS receipt reconciles it — additively, via `supersedes`.
        # The prior UNKNOWN is never deleted or retracted; it stays in history and the
        # canonical_for_idempotency projection now folds to this definite one.
        if status in (executor.SUCCEEDED, executor.FAILED):
            prior_unknown = [c for c in w.receipts_for_idempotency(idem)
                             if c.content.get("status") == executor.UNKNOWN]
            if prior_unknown:
                receipt["supersedes"] = prior_unknown[-1].id
        self.weft.append(self.executor.id, ASSERT, {
            "cell": rid, "type": "result", "content": receipt,
        })
        # A sandbox/exec refusal surfaces as a denial (the effect did not happen),
        # while the FAILED receipt above keeps the blocked attempt auditable.
        if status == executor.FAILED and result.get("error", {}).get("code") in ("sandbox", "exec"):
            return {"denied": result["error"]["message"], "status": status,
                    "result_cell": rid, "invoke_event": inv.id, "signer": holder}
        return {"ok": result, "status": status, "result_cell": rid,
                "invoke_event": inv.id, "signer": holder}

    def _autonomy_gate(self, agent_cell, cap, args):
        """LIVE1 — the autonomy ladder, enforced at the invoke boundary.

        Keyed on (acting principal, capability name); the effect_class travels on the
        capability's caveats (defaults to READ), exactly as the EffectReceipt records it.

        Returns None to PROCEED (the common, inert path), or a denial/proposal dict to
        STOP the invoke before any effect runs:

          • no rung set for (agent, cap) → None (INERT — behaves exactly as before);
          • READ/PURE effect_class → None at every rung (observing is always allowed);
          • rung 1 (read-only) → REFUSE a write/effect (fail closed, recorded);
          • rung 2 (draft)     → do NOT execute; record a PROPOSAL instead;
          • rung 3 (supervised)→ REVERSIBLE proceeds; IRREVERSIBLE/FINANCIAL require a
                                  Morta approval (deny until cap is approved);
          • rung 4/5           → proceed (the verdict is still recorded — audited).

        The decision (and its reason) is recorded on the Weft by autonomy.decide(), so an
        autonomy verdict is auditable, never an ambient toggle. A demotion takes effect on
        the very next invoke because the rung is re-read from the Weave each call. Lazy
        import keeps the kernel free of an import cycle (as _governance_verdict does)."""
        from decima import autonomy as au
        principal = self.principal_for(agent_cell)
        capability = cap.content["name"]
        # INERT unless a rung is EXPLICITLY set for this (agent, capability). We must NOT
        # use level_of() here — its safe-floor default (read-only) would gate every cap
        # that was never enrolled, breaking back-compat. Absence of a rung = unrestricted.
        if au.get_level(self, principal, capability) is None:
            return None
        effect_class = cap.content.get("caveats", {}).get("effect_class", au.READ)
        d = au.decide(self, principal, capability, effect_class=effect_class)
        verdict = d["verdict"]

        if verdict == au.EXECUTE:
            return None                                  # rung permits this effect — proceed (audited)

        if verdict == au.REFUSE:                         # rung 1: read-only refuses a write/effect
            return {"denied": f"autonomy gate (rung {d['level']}): {d['reason']}",
                    "autonomy": d, "decision": d.get("decision")}

        if verdict == au.PROPOSE:                        # rung 2: draft a proposal, execute nothing
            pid = content_id({"autonomy_proposal": principal, "capability": capability,
                              "effect_class": d["effect_class"], "at": self.weft.head})
            self.weft.append(principal, ASSERT, {
                "cell": pid, "type": "proposal",
                "content": {"agent": principal, "capability": capability,
                            "effect_class": d["effect_class"], "args": args,
                            "reason": d["reason"], "decision": d.get("decision")},
            })
            return {"proposed": pid, "autonomy": d, "decision": d.get("decision")}

        # require_approval — rung 3 on an irreversible/financial effect. Honor the SAME
        # Morta approval seam the ocap caveat uses (approve(cap_id)). Deny until approved;
        # once approved, fall through (None) so the effect runs.
        if cap.id in self.approvals:
            return None
        return {"denied": f"autonomy gate (rung {d['level']}): {d['reason']} "
                          "— awaiting Morta approval",
                "requires_approval": cap.id, "autonomy": d, "decision": d.get("decision")}

    # -- granting = asserting a signed edge to a named grantee -------------
    def grant(self, cap_id, agent_id):
        w = self.weave()
        agent = w.get(agent_id)
        principal = agent.content["principal"]
        env = list(agent.content.get("envelope", []))
        if cap_id not in env:
            env.append(cap_id)
        self.weft.append(self.root.id, ASSERT,
                         {"cell": agent_id, "type": "agent",
                          "content": {**agent.content, "envelope": env}})
        cap = w.get(cap_id)
        if cap.content.get("grantee") != principal:
            self.weft.append(self.root.id, ASSERT,
                             {"cell": cap_id, "type": "capability",
                              "content": {**cap.content, "grantee": principal,
                                          "granter": self.root.id}})

    def spawn(self, parent_agent_cell, name, base_cap_id, stricter, objective,
              sandbox=False):
        """Decima allots: mint a subagent with its OWN key and a downhill, signed,
        attenuated grant. The GRANTER (parent) signs the grant event it issues."""
        w = self.weave()
        sub = self.keyring.mint(name, "agent")
        granter = parent_agent_cell.content["principal"]
        base = w.get(base_cap_id)
        att = attenuate(base.content, stricter, base_cap_id,
                        grantee=sub.id, granter=granter)
        att_id = content_id({"grant": name, "of": base_cap_id, "to": sub.id})
        self.weft.append(granter, ASSERT,
                         {"cell": att_id, "type": "capability", "content": att})
        sub_id = content_id({"agent": name, "by": granter})
        self.weft.append(granter, ASSERT, {
            "cell": sub_id, "type": "agent",
            "content": {
                "principal": sub.id, "objective": objective,
                "envelope": [att_id], "budget": att["caveats"].get("budget", 0),
                "sandbox": sandbox, "lineage": parent_agent_cell.id,
            },
        })
        return sub_id, att_id, sub

    def approve(self, cap_id):
        """A human (or a Morta policy) approves a requires_approval capability."""
        self.approvals.add(cap_id)

    # -- EffectReceipt lifecycle: compensate / cancel (WEFT §8) ------------
    @staticmethod
    def _validate_cost(cost):
        """Signed receipt content is an integer count of money/units — never a float,
        never a bool-as-int. Fail loud on anything else (writes no receipt)."""
        if not (isinstance(cost, int) and not isinstance(cost, bool) and cost >= 0):
            raise ValueError(f"receipt cost must be a non-negative int (not bool), got {cost!r}")
        return cost

    def compensate(self, receipt_id, reason="", cost=0):
        """Saga-style compensation: record that a compensating action UNDID a prior
        SUCCEEDED effect. Appends a NEW `result` receipt with status COMPENSATED that
        names the original via `compensates` (and a provenance EDGE to it). Additive —
        the original receipt is left untouched and still folds in of_type('result');
        the pair (original SUCCEEDED, its COMPENSATED) is the auditable undo. Returns
        the new receipt cell id."""
        from decima import model
        self._validate_cost(cost)
        orig = self.weave().get(receipt_id)
        if orig is None or orig.type != "result":
            raise ValueError(f"compensate: {receipt_id!r} does not name a result receipt")
        rid = content_id({"compensates": receipt_id, "at": self.weft.head})
        receipt = {"of": orig.content.get("of"), "cap": orig.content.get("cap"),
                   "status": executor.COMPENSATED, "executor": self.executor.id,
                   "attempt": 0, "idempotency": orig.content.get("idempotency"),
                   "effect_class": orig.content.get("effect_class", "READ"),
                   "compensates": receipt_id, "reason": reason, "cost": cost,
                   "out": None}
        self.weft.append(self.executor.id, ASSERT,
                         {"cell": rid, "type": "result", "content": receipt})
        # Provenance: link the compensation to the effect it undid.
        model.assert_edge(self.weft, self.executor.id, rid, "compensates", receipt_id)
        return rid

    def cancel(self, cap_id, reason="", cost=0):
        """Record an effect CANCELLED before submission — a definite never-sent
        outcome. Appends a `result` receipt with status CANCELLED naming the
        capability and the reason, WITHOUT invoking the effect (nothing reaches the
        world). This is an EXPLICIT record only — it is deliberately NOT wired into
        invoke()'s denial path (a gate denial stays a denial, not a receipt). Returns
        the receipt cell id."""
        self._validate_cost(cost)
        rid = content_id({"cancelled": cap_id, "reason": reason, "at": self.weft.head})
        receipt = {"cap": cap_id, "status": executor.CANCELLED,
                   "executor": self.executor.id, "attempt": 0,
                   "reason": reason, "cost": cost, "out": None,
                   "effect_class": "READ"}
        self.weft.append(self.executor.id, ASSERT,
                         {"cell": rid, "type": "result", "content": receipt})
        return rid

    def revoke(self, cap_id):
        """Morta: revocation = RETRACT (WITHDRAW) of the capability cell."""
        self.weft.append(self.root.id, RETRACT, {"cell": cap_id})

    def redact(self, cell_id):
        """Morta: REDACT — withdraw AND erase the payload (WEFT §5 / FOLD §10). The
        cell's content leaves every projection (a content-free tombstone remains);
        the event skeleton stays on the Log. Right-to-be-forgotten at the fold."""
        self.weft.append(self.root.id, RETRACT, {"cell": cell_id, "mode": "REDACT"})

    # -- Phase 2: registry consumers (ingestion + tool integration) --------
    def ingest_observation(self, agent_cell, url) -> dict:
        """Observe a URL (untrusted) and ingest it across the trust boundary by routing
        it through the LIVE disposition router (INTAKE1): the page text becomes an intake
        that Decima auto-disposes — a fact is remembered as DATA, noise is archived, an
        injection is kept as flagged DATA — never an instruction. Trust follows the source
        (False for the web), and an untrusted intake can never elevate to an action. The
        observation receipt still grounds the provenance (`observed_via`).
        (Was a direct `memory.remember`; the disposition router now decides.)"""
        from decima import disposition, model
        obs = _find_named(self.weave(), agent_cell, "browser.observe")
        if obs is None:
            return {"denied": "no browser.observe capability"}
        res = self.invoke(agent_cell, obs.id, {"url": url})
        if "denied" in res:
            return res
        out, receipt = res["ok"], res["result_cell"]
        elig = bool(out.get("instruction_eligible", False))   # trust flows from the source
        d = self.ingest(f"web:{url}", out["out"], trusted=elig)
        # Preserve the observation-receipt provenance: ground the intake in the receipt.
        model.assert_edge(self.weft, self.principal_for(agent_cell),
                          d["intake"], "observed_via", receipt)
        return {"observed": out["out"], "receipt": receipt,
                "disposition": d["disposition"], "action": d["action"],
                "claim": d["produced"] if d["action"] == disposition.REMEMBER else None,
                "instruction_eligible": elig}

    def ingest(self, source, text, *, trusted=False, kind=None) -> dict:
        """The LIVE inbound entry (INTAKE1): any inbound datum — a message, tool output,
        an observed page — is captured and auto-routed through the disposition router
        (DISP1). Untrusted inbound (the default) can only ever be remembered as DATA or
        archived; it can never elevate to a task/invoke/policy. Returns the disposition."""
        from decima import disposition
        return disposition.dispose(self, source, text, trusted=trusted, kind=kind)

    def integrate_tool(self, name, handler, caveats=None) -> str:
        """Integrate a CLI tool / external agent as a capability: register its
        effect handler in the executor registry and grant Decima a capability to
        run it. A new tool is ONE call — no kernel edit. `authorize` still gates
        who may invoke it; it runs as the invoking agent's principal (sandbox seam)."""
        executor.register(name, handler)
        cap_id = self._assert_cap(name, name, caveats=caveats)
        self.grant(cap_id, self.decima_agent_id)
        return cap_id

    # -- high level: a spoken/typed turn -----------------------------------
    def say(self, text: str) -> list[str]:
        text = nfc(text)                       # normalize human text at the boundary (§1)
        transcript = []
        uid = content_id({"utterance": text, "lamport": self.weft.lamport})
        self.weft.append(self.human.id, ASSERT,
                         {"cell": uid, "type": "utterance", "content": {"text": text}})
        transcript.append(f"you ▸ {text}")

        agent = self.weave().get(self.decima_agent_id)
        action = self.brain.decide(text, self.weave(), agent)
        if action.reasoning:                       # the model brain's stated why
            transcript.append(f"decima ⟂ {action.reasoning}")
        if action.kind == "delegate":
            lines, _ = self._delegate(agent, action, depth=1, label="decima", parent_task=None)
            transcript.extend(lines)
            return transcript
        if action.kind == "respond":
            rid = content_id({"reply": action.text, "to": uid})
            self.weft.append(self.decima.id, ASSERT,
                             {"cell": rid, "type": "speech", "content": {"text": action.text}})
            transcript.append(f"decima ▸ {action.text}")
            return transcript

        res = self.invoke(agent, action.cap, action.args)
        if "denied" in res:
            transcript.append(f"decima ▸ ✋ denied: {res['denied']}")
            return transcript
        out = res["ok"].get("out", res["ok"])
        cap = self.weave().get(action.cap)
        transcript.append(f"decima ▸ [{cap.content['name']}] {out}")
        return transcript

    # -- delegation: reason → spawn → brief (recorded as a task) → run ------
    def _assert_task(self, author_principal, task_id, content):
        """A delegation is graph state: a typed `task` cell linking delegator →
        worker → grant → result, so the org tree is a fold over the Weave."""
        self.weft.append(author_principal, ASSERT,
                         {"cell": task_id, "type": "task", "content": content})

    def _delegate(self, delegator_cell, action, depth, label, parent_task):
        """Fan out: one worker per brief. Each gets its own key + a downhill grant,
        the briefing is recorded as a task cell, then the worker runs (and may
        itself delegate, up to MAX_DELEGATION_DEPTH)."""
        lines, outcomes = [], []
        principal = self.principal_for(delegator_cell)
        for spec in (action.tasks or []):
            name, objective, budget = spec["subagent"], spec["objective"], (spec["budget"] or 10)
            cap = _find_named(self.weave(), delegator_cell, spec["capability"])
            if cap is None:
                # Record the gap as a task — a measurable signal for the score and
                # for learned policy ("we lacked X"). This is what the forge → use
                # loop closes: forge X, then re-delegate succeeds.
                gap_id = content_id({"gap": spec["capability"], "for": name,
                                    "by": principal, "n": self.weft.lamport})
                self._assert_task(principal, gap_id, {
                    "objective": objective, "delegator": delegator_cell.id,
                    "delegator_name": label, "worker": None, "worker_name": name,
                    "grant": None, "capability": spec["capability"], "parent": parent_task,
                    "depth": depth, "status": "ungranted", "steps": 0, "denials": 1,
                    "latency_ms": 0, "result": "delegator does not hold this capability",
                })
                lines.append(f"{label} ▸ ✋ can't delegate “{spec['capability']}” — not held (gap recorded)")
                outcomes.append({"status": "ungranted", "steps": 0, "denials": 1})
                continue
            # Live governance gate (LOOP1): before spending a worker, Decima consults its
            # OWN recorded rules (B4 `memory.governance_check`) — "what's banned" — and
            # refuses a delegation a `banned_action` covers, citing the rule + the prior
            # evidence that earned it. governance_check exists; this is the deferred kernel
            # wiring that makes it LIVE: the "memory prevents repeated bad actions" promise,
            # enforced at delegate-time rather than merely queryable. Inert if no governance.
            gov = self._governance_verdict(objective, spec["capability"])
            if not gov["allow"]:
                ev = (gov.get("evidence") or [{}])[0]
                blocked_id = content_id({"gov_refused": spec["capability"], "for": name,
                                         "by": principal, "n": self.weft.lamport})
                self._assert_task(principal, blocked_id, {
                    "objective": objective, "delegator": delegator_cell.id,
                    "delegator_name": label, "worker": None, "worker_name": name,
                    "grant": None, "capability": spec["capability"], "parent": parent_task,
                    "depth": depth, "status": "governance_denied", "steps": 0, "denials": 0,
                    "latency_ms": 0, "result": gov["reason"],
                    "governance": ev.get("governance"), "evidence": gov.get("evidence"),
                })
                lines.append(f"{label} ▸ ⛔ {gov['reason']}")
                outcomes.append({"status": "governance_denied", "steps": 0, "denials": 0})
                continue
            # Learned org policy DRIVES the choice (D3): the cap is held, but if its
            # recorded track record is bad (repeated denials, zero completions), do
            # not spend another worker on a delegation doomed to be denied — refuse
            # up front and record WHY, a measurable signal of its own.
            allow, why = self.org_policy(spec["capability"])
            if not allow:
                refused_id = content_id({"refused": spec["capability"], "for": name,
                                         "by": principal, "n": self.weft.lamport})
                self._assert_task(principal, refused_id, {
                    "objective": objective, "delegator": delegator_cell.id,
                    "delegator_name": label, "worker": None, "worker_name": name,
                    "grant": None, "capability": spec["capability"], "parent": parent_task,
                    "depth": depth, "status": "refused", "steps": 0, "denials": 0,
                    "latency_ms": 0, "result": why,
                })
                lines.append(f"{label} ▸ ⊘ {why}")
                outcomes.append({"status": "refused", "steps": 0, "denials": 0})
                continue
            sub_id, grant_id, sub = self.spawn(delegator_cell, name, cap.id,
                                               {"budget": budget}, objective)
            task_id = content_id({"task": objective, "worker": sub_id,
                                  "by": principal, "n": self.weft.lamport})
            self._assert_task(principal, task_id, {
                "objective": objective, "delegator": delegator_cell.id,
                "delegator_name": label, "worker": sub_id, "worker_name": name,
                "grant": grant_id, "capability": cap.content["name"],
                "parent": parent_task, "depth": depth, "status": "assigned", "result": None,
                "steps": 0, "denials": 0, "latency_ms": 0,
            })
            lines.append(f"{label} ▸ ⇒ {name} ({sub.id[:8]}): "
                         f"{cap.content['name']}≤{budget}  brief: “{objective}”")
            sublines, outcome = self._run_agent(self.weave().get(sub_id),
                                                objective, name, depth, task_id)
            lines.extend("  " + s for s in sublines)
            done = self.weave().get(task_id)
            self._assert_task(principal, task_id, {**done.content,
                              "status": outcome["status"], "result": outcome.get("result"),
                              "steps": outcome.get("steps", 0),
                              "denials": outcome.get("denials", 0),
                              "latency_ms": outcome.get("latency_ms", 0)})
            outcomes.append(outcome)
        return lines, {"status": "delegated", "tasks": outcomes}

    def _governance_verdict(self, objective, capability) -> dict:
        """Consult B4 governance memory for this delegation (LOOP1). Checks both the
        objective and the capability name against recorded bans; returns the first
        DENY verdict (with evidence) or an allow. Only instruction-eligible governance
        binds (`governance_check`'s job), and empty governance is inert (allow). Lazy
        import keeps the kernel free of a memory import cycle."""
        from decima import memory
        for target in (objective, capability):
            if not target:
                continue
            v = memory.governance_check(self.weave(), target)
            if not v.get("allow", True):
                return v
        return {"allow": True, "reason": None, "evidence": []}

    def _run_agent(self, agent_cell, prompt, speaker, depth, parent_task):
        """One decide→act cycle for a worker. It may sub-delegate while depth allows.
        The worker reasons over only its own envelope; authorize() gates every INVOKE.
        Returns the worker's OWN leaf metrics (children self-record their own tasks)."""
        lines = []
        t0 = time.monotonic()
        action = self.brain.decide(prompt, self.weave(), agent_cell)
        if action.reasoning:
            lines.append(f"{speaker} ⟂ {action.reasoning}")
        ms = lambda: round((time.monotonic() - t0) * 1000, 1)

        if action.kind == "delegate":
            if depth >= self.MAX_DELEGATION_DEPTH:
                lines.append(f"{speaker} ▸ ✋ max delegation depth reached")
                return lines, {"status": "refused", "steps": 0, "denials": 1, "latency_ms": ms()}
            sub, _agg = self._delegate(agent_cell, action, depth + 1, speaker, parent_task)
            lines.extend(sub)
            return lines, {"status": "delegated", "steps": 0, "denials": 0, "latency_ms": ms()}
        if action.kind == "respond":
            lines.append(f"{speaker} ▸ {action.text}")
            return lines, {"status": "done", "steps": 0, "denials": 0,
                           "latency_ms": ms(), "result": action.text}
        res = self.invoke(agent_cell, action.cap, action.args)
        if "denied" in res:
            lines.append(f"{speaker} ▸ ✋ denied: {res['denied']}")
            return lines, {"status": "denied", "steps": 0, "denials": 1,
                           "latency_ms": ms(), "result": res["denied"]}
        cap = self.weave().get(action.cap)
        out = res["ok"].get("out", res["ok"])
        lines.append(f"{speaker} ▸ [{cap.content['name']}] {out}")
        return lines, {"status": "done", "steps": 1, "denials": 0,
                       "latency_ms": ms(), "result": res["result_cell"]}

    def org_score(self) -> dict:
        """Fold the task tree into an organization outcome — the first rung toward
        learned org policy: which topologies completed, how much they cost, what
        got denied. Each task records its worker's own leaf metrics, so straight
        sums don't double-count the tree."""
        by_status, steps, denials, latency = {}, 0, 0, 0.0
        for t in self.weave().of_type("task"):
            c = t.content
            by_status[c["status"]] = by_status.get(c["status"], 0) + 1
            steps += c.get("steps", 0)
            denials += c.get("denials", 0)
            latency += c.get("latency_ms", 0)
        done = by_status.get("done", 0)
        return {"workers": len(self.weave().of_type("task")), "steps": steps,
                "denials": denials, "latency_ms": round(latency, 1),
                "by_status": by_status, "completed": done}

    def org_signal(self, capability=None):
        """Sharpen `org_score` from a global tally into a PER-CAPABILITY outcome
        signal — the substrate a learned policy decides on. For each capability
        ever delegated, fold its `task` outcomes: completions, runtime denials,
        ungranted gaps, prior refusals, and a derived `distrusted` verdict. Folded
        from the Weave, so it is deterministic and time-travelable like all state.

        Distrust is earned by a HELD capability that keeps failing at the
        authorization gate (status 'denied'), NEVER by 'ungranted' gaps — a gap is
        a missing organ the forge loop fixes, not an untrustworthy one. Keeping the
        two apart is what lets the self-improvement loop (gap → forge → use) coexist
        with the policy that refuses a capability whose runtime record is bad."""
        empty = {"n": 0, "completed": 0, "denied": 0, "ungranted": 0,
                 "refused": 0, "distrusted": False}
        by_cap = {}
        for t in self.weave().of_type("task"):
            c = t.content
            cap = c.get("capability")
            if not cap:
                continue
            s = by_cap.setdefault(cap, dict(empty))
            s["n"] += 1
            st = c.get("status")
            if st == "done":
                s["completed"] += 1
            elif st == "denied":
                s["denied"] += 1
            elif st == "ungranted":
                s["ungranted"] += 1
            elif st == "refused":
                s["refused"] += 1
        for s in by_cap.values():
            s["distrusted"] = (s["completed"] == 0
                               and s["denied"] >= self.ORG_POLICY_DENIAL_LIMIT)
        if capability is not None:
            return by_cap.get(capability, dict(empty))
        return by_cap

    def org_policy(self, capability) -> tuple[bool, str]:
        """Learned org policy: should the orchestrator delegate `capability`, given
        its recorded track record? This is the first place org outcomes DRIVE a
        decision instead of merely being measured — `org_score` folded into a gate.
        Deterministic; reads only the folded signal. Returns (allow, reason)."""
        s = self.org_signal(capability)
        if s.get("distrusted"):
            return False, (f"org policy: '{capability}' failed {s['denied']} delegation(s) "
                           f"with 0 completions — refusing to spend another worker on it")
        return True, "ok"

    def task_tree(self) -> list[str]:
        """Render the delegation tree (Law 4 for orchestration): who briefed whom,
        with what capability, and the outcome — all folded from `task` cells."""
        w = self.weave()
        tasks = w.of_type("task")
        by_id = {t.id: t for t in tasks}
        kids = {}
        roots = []
        for t in tasks:
            p = t.content.get("parent")
            if p and p in by_id:
                kids.setdefault(p, []).append(t)
            else:
                roots.append(t)
        lines = []

        def render(t, indent):
            c = t.content
            lines.append(f"{'  ' * indent}• {c['delegator_name']} ⇒ {c['worker_name']}: "
                         f"{c['capability']} — “{c['objective']}” [{c['status']}]")
            for ch in kids.get(t.id, []):
                render(ch, indent + 1)

        for r in roots:
            render(r, 0)
        return lines

    # -- demos -------------------------------------------------------------
    def demo_attack(self) -> list[str]:
        """Law 2: an under-privileged sandbox agent with no grants is denied."""
        out = []
        imp = self.keyring.mint("intruder", "agent")
        intruder_id = content_id({"agent": "intruder"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": intruder_id, "type": "agent",
            "content": {"principal": imp.id, "objective": "exfiltrate",
                        "envelope": [], "sandbox": True},
        })
        intruder = self.weave().get(intruder_id)
        shell_cap = next(c.id for c in self.weave().of_type("capability")
                         if c.content.get("name") == "shell")
        res = self.invoke(intruder, shell_cap, {"cmd": "whoami"})
        out.append("intruder holds envelope: [] (nothing)")
        out.append(f"intruder INVOKE shell → {res.get('denied', res)}")
        out.append("→ no ambient authority. nothing to escalate toward.")
        return out

    def demo_delegation(self) -> list[str]:
        """Decima allots a downhill, signed, possession-proven grant to a
        subagent — and every way of abusing it fails at the right gate."""
        out = []
        decima = self.weave().get(self.decima_agent_id)
        shell = next(c for c in self.weave().of_type("capability")
                     if c.content.get("name") == "shell")

        # 1. Allot: a subagent with its OWN key and an attenuated grant.
        sub_id, grant_id, sub = self.spawn(
            decima, "Researcher", shell.id,
            stricter={"budget": 5, "requires_approval": True},
            objective="look something up",
        )
        out.append(f"Decima spawns Researcher (principal {sub.id[:8]}, own key)")
        out.append(f"  granted shell: budget 100→5, +requires_approval  [grant {grant_id[:8]}]")
        researcher = self.weave().get(sub_id)

        # 2. Morta approval gate.
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Researcher INVOKE shell:date → ✋ {r['denied']}")

        # 3. Approved → runs, signed by Researcher's OWN key.
        self.approve(grant_id)
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"approved → Researcher INVOKE → {r['ok']['out']}  "
                   f"(signed by {r['signer'][:8]} = Researcher, not Decima)")

        # 4. Budget caveat bites on the second call (3 spent, +3 > 5).
        r = self.invoke(researcher, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Researcher 2nd call → ✋ {r['denied']}")

        # 5. The id is public. An impostor copies it into its envelope — refused.
        bad = self.keyring.mint("Impostor", "agent")
        bad_id = content_id({"agent": "impostor-deleg"})
        self.weft.append(self.root.id, ASSERT, {
            "cell": bad_id, "type": "agent",
            "content": {"principal": bad.id, "objective": "steal",
                        "envelope": [grant_id], "sandbox": False},
        })
        impostor = self.weave().get(bad_id)
        r = self.invoke(impostor, grant_id, {"cmd": "date", "cost": 3})
        out.append(f"Impostor copies the public grant id → ✋ {r['denied']}")

        # 6. Researcher reaches past its grant — no edge, no authority.
        forge = next(c for c in self.weave().of_type("capability")
                     if c.content.get("name") == "forge")
        r = self.invoke(researcher, forge.id, {})
        out.append(f"Researcher reaches for forge (never granted) → ✋ {r['denied']}")

        # 7. Downhill: Researcher cannot re-widen what it sub-delegates.
        _, intern_grant, _ = self.spawn(
            researcher, "Intern", grant_id,
            stricter={"budget": 50}, objective="help",   # asks to WIDEN 5→50
        )
        eff = self.weave().get(intern_grant).content["caveats"].get("budget")
        out.append(f"Researcher sub-delegates to Intern asking budget 50 → clamped to {eff} (downhill)")
        return out

    def demo_replay(self) -> list[str]:
        """A captured proof can't be reused against a different request or frontier."""
        out = []
        decima = self.weave().get(self.decima_agent_id)
        holder = self.principal_for(decima)
        echo = next(c for c in self.weave().of_type("capability")
                    if c.content.get("name") == "echo")
        nonce = os.urandom(16).hex()
        parents = [self.weft.head] if self.weft.head else []
        body = {"cap": echo.id, "args": {"text": "transfer $10"}}
        proof = build_proof(self.weave(), self.keyring, holder, echo.id,
                            INVOKE, body, nonce, parents)
        ok, _ = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                             body, nonce, parents, 0, self.approvals)
        out.append(f"Decima signs a proof for echo “transfer $10” → verifies: {ok}")

        evil = {"cap": echo.id, "args": {"text": "transfer $1000000"}}
        _, why2 = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                               evil, nonce, parents, 0, self.approvals)
        out.append(f"same proof, args tampered to $1000000 → ✋ {why2}")

        self.say("echo advance the frontier")                     # move the causal frontier
        moved = [self.weft.head]
        _, why3 = verify_proof(self.weave(), self.keyring, decima, proof, INVOKE,
                               body, nonce, moved, 0, self.approvals)
        out.append(f"same proof, replayed at a new frontier → ✋ {why3}")
        out.append("→ the proof is bound to verb+body+nonce+parents; it cannot be moved.")
        return out

    def provenance(self, cell) -> list[str]:
        """Law 4: walk the events that built a cell, each naming its cap + author."""
        lines = []
        index = {ev.id: ev for ev in self.weft.events()}
        for eid in cell.provenance:
            ev = index.get(eid)
            if not ev:
                continue
            who = self.keyring.name_of(ev.author)
            cap = ev.authorized[:8] if ev.authorized else "—"
            lines.append(f"  e{ev.seq:<3} {ev.verb:<7} by {who:<10} via cap {cap}")
        return lines
