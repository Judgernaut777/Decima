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

    # -- the core action path: authorize -> INVOKE -> execute -> ASSERT ----
    def invoke(self, agent_cell, cap_id, args) -> dict:
        w = self.weave()
        holder = self.principal_for(agent_cell)
        spent = self.spent.get(agent_cell.id, 0.0)
        # Bind the proof to THIS exact request: verb + body + nonce + frontier.
        nonce = os.urandom(16).hex()
        parents = [self.weft.head] if self.weft.head else []
        body = {"cap": cap_id, "args": args}
        proof = build_proof(w, self.keyring, holder, cap_id, INVOKE, body, nonce, parents)
        ok, reason = verify_proof(w, self.keyring, agent_cell, proof, INVOKE, body,
                                  nonce, parents, spent, self.approvals)
        if not ok:
            return {"denied": reason}

        cap = w.get(cap_id)
        # The INVOKE carries its AuthorizationProof and is signed by the holder's key.
        inv = self.weft.append(holder, INVOKE,
                               {**body, "nonce": nonce, "proof": proof}, authorized=cap_id)
        result = executor.execute(cap.content["effect"], cap.content.get("impl"), args)
        self.spent[agent_cell.id] = spent + float(args.get("cost", 0))
        # The completion is a separate ASSERT (WEFT §6): the `result` cell is an
        # EffectReceipt (WEFT §8) causally descending from the INVOKE. It carries
        # `status` — SUCCEEDED / FAILED / UNKNOWN — so an ambiguous effect is
        # recorded as UNKNOWN, never a fabricated outcome (FOLD §11 #8). The
        # idempotency key is the invocation nonce (one logical op = one INVOKE);
        # effect_class travels on the capability's caveats (defaults to READ).
        status = result.get("status", executor.SUCCEEDED)
        rid = content_id({"result_of": inv.id})
        receipt = {"of": inv.id, "cap": cap.content["name"], **result,
                   "status": status, "executor": self.executor.id, "attempt": 0,
                   "idempotency": nonce,
                   "effect_class": cap.content.get("caveats", {}).get("effect_class", "READ")}
        self.weft.append(self.executor.id, ASSERT, {
            "cell": rid, "type": "result", "content": receipt,
        })
        return {"ok": result, "status": status, "result_cell": rid,
                "invoke_event": inv.id, "signer": holder}

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
        """Observe a URL (untrusted) and ingest it into memory across the trust
        boundary: the page text becomes a CLAIM whose instruction_eligibility
        follows the source (False for the web), linked by `supported_by` to the
        observation receipt. The web becomes provenance-stamped DATA, never an
        instruction — the capability→memory path of specs/BROWSER_WORKER.md §6."""
        obs = _find_named(self.weave(), agent_cell, "browser.observe")
        if obs is None:
            return {"denied": "no browser.observe capability"}
        res = self.invoke(agent_cell, obs.id, {"url": url})
        if "denied" in res:
            return res
        out, receipt = res["ok"], res["result_cell"]
        elig = bool(out.get("instruction_eligible", False))   # trust flows from the source
        claim = memory.remember(self.weft, self.principal_for(agent_cell),
                                out["out"], receipt, instruction_eligible=elig)
        return {"observed": out["out"], "receipt": receipt,
                "claim": claim, "instruction_eligible": elig}

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
