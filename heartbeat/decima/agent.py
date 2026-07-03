"""Agents — actors that observe the Weave, decide, and INVOKE capabilities.

The "brain" is the decision seam. Two implementations:

  RuleBrain  — deterministic pattern matching. Zero network, fully reproducible.
  ModelBrain — a real Claude call (claude-opus-4-8 by default) that reasons over
               the utterance and the agent's *held* capabilities, returning a
               structured decision. Falls back to RuleBrain with no key or on any
               error, so the Heartbeat still runs offline.

`make_brain()` picks based on the environment.

Crucial property: the brain only *proposes*. `capability.authorize` gates every
INVOKE regardless of which brain chose it — so a model (or a prompt-injected one)
has no more authority than the rule stub. Law 2 holds above the LLM.
"""
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from decima import verifier
from decima.quarantine import (DATA_LAW, FENCE_OPEN, Quarantined, QuarantineBypass,
                               instruction_stream, require_quarantined)
from decima.router import (Router, describe_task, make_router,
                           Engine, default_engines, TaskDescriptor)


@dataclass
class Action:
    kind: str                       # "invoke" | "respond" | "delegate"
    cap: str | None = None          # resolved capability id (invoke)
    args: dict | None = None
    text: str | None = None
    reasoning: str | None = None    # the brain's stated why (model brain)
    tasks: list | None = None       # delegate: [{capability, subagent, objective, budget}, …]
                                    # one entry per worker; several entries fan out


def _delegation_specs(raw):
    """Normalize a list of delegate-task dicts (lowercase cap name, int budget)."""
    out = []
    for spec in raw or []:
        b = spec.get("budget")
        out.append({
            "capability": (spec.get("capability") or "").strip().lower(),
            "subagent": (spec.get("subagent") or "Worker").strip() or "Worker",
            "objective": (spec.get("objective") or "").strip() or "(no objective)",
            "budget": int(b) if b else None,
        })
    return out


# ── QUARANTINE BOUNDARY (Phase 1: Enforcement) ──────────────────────────────
# External/engine-derived content reaches a brain through exactly ONE door:
# `present()`. It accepts ONLY a `Quarantined` (minted by `quarantine.admit`,
# which records the taint on the Weft) and renders it as a fenced, neutralized
# DATA block. A raw string here is a bypass and RAISES — the unquarantined path
# does not exist. Inside the brains, `quarantine.instruction_stream` strips every
# data block before any pattern/orientation/planning can match, so untrusted
# bytes are structurally DATA, never instructions.

def _screen(utterance):
    """The brains' shared instruction/data separator. A `Quarantined` fed directly
    to a brain is a bypass (present() is the door); a str is screened so fenced
    data blocks never reach pattern-matching, orientation, or planning."""
    if isinstance(utterance, Quarantined):
        raise QuarantineBypass(
            "quarantined content must be presented via agent.present(), "
            "never fed to a brain as the instruction stream")
    return instruction_stream(utterance)


def present(k, agent_cell, brain, external, *, question=None):
    """THE mandatory chokepoint: present external/engine-derived content to a brain.

    `external` MUST be a `Quarantined` from `quarantine.admit()` — its taint and
    provenance are already on the Weft. Anything else (a raw str, bytes, a dict)
    raises `QuarantineBypass`: there is no unquarantined path to the brain. The
    brain sees the content only as `as_data()` — a fenced, neutralized DATA block —
    while the instruction stream is the caller's own trusted `question`."""
    q = require_quarantined(external)
    if isinstance(question, Quarantined):
        raise QuarantineBypass("the question is the TRUSTED instruction stream — "
                               "quarantined content cannot be the question")
    ask = ("consider the quarantined external data below"
           if question is None else question).strip()
    prompt = ask + "\n" + q.as_data()
    return brain.decide(prompt, k.weave(), agent_cell)


def admit_engine_output(k, run, *, source=None):
    """Quarantine an engine result (`TaskRun`/anything with `.output`): the wiring
    that keeps engine-derived text on the data plane. Returns the `Quarantined`
    handle to hand to `present()` — never the raw output."""
    from decima import quarantine
    text = getattr(run, "output", run)
    model_name = getattr(run, "model", None)
    return quarantine.admit(k, source or f"engine:{model_name or 'unknown'}", text)


def held_capabilities(weave, agent_cell):
    """The active, non-quarantined capabilities in the agent's envelope."""
    env = set(agent_cell.content.get("envelope", []))
    out = []
    for c in weave.of_type("capability"):
        if c.id in env and not c.content.get("quarantined"):
            out.append(c)
    return out


def _find_named(weave, agent_cell, name):
    name = (name or "").strip().lower()
    for c in held_capabilities(weave, agent_cell):
        if c.content.get("name") == name:
            return c
    return None


def _orient(weave, agent_cell, utterance):
    """Consult the Orientation lens (OR1) before deciding — interpret the request
    through the user's values + governance + horizon. Lazy import avoids any module
    cycle; orientation must NEVER break the brain, so any failure is inert. Returns
    the Orientation, or None when unavailable. With no profile/governance on the
    Weave it carries no constraints, so the brain behaves exactly as before."""
    try:
        from decima import orientation
        return orientation.orient(weave, agent_cell, utterance)
    except Exception:  # noqa: BLE001 — the lens advises; it must not crash decide
        return None


def _oriented_block(o):
    """If orientation refuses the situation (a governance rule fires), the brain
    chooses not to act and says why — citing the rule. Refusal is not authority:
    it only ever *declines*; authorize() still gates anything chosen."""
    if o is not None and o.blocked:
        return Action("respond", text=f"✋ {o.refusal()}",
                      reasoning="orientation: conflicts with a governance rule")
    return None


def _orientation_prompt(o) -> str:
    """A short system-prompt addendum giving the model the user's binding values, so
    the model brain acts FROM the user's orientation too — not just the rule brain.
    Empty when nothing binds, so the prompt is unchanged on an un-oriented agent."""
    if o is None or not o.values:
        return ""
    vals = "; ".join(f"{k}={v['value']}" for k, v in sorted(o.values.items()))
    return ("\n\nThe user's standing preferences (act from these): " + vals)


# ── pattern-awareness: PATTERN1/DISPATCH1 + PLAN1 as a thin, inert hook (BRAIN1) ──
# Mirrors OR1's `_orient`: lazy import, any failure → inert (None), NEVER raises into
# the brain's decide/say loop. The existing single-agent path is untouched; this only
# *adds* an orchestration choice (and, for a complex/multi-step task, a decomposed
# plan) ALONGSIDE the brain's decision, recorded with provenance on the Weft.
#
# Crucially this is ADVICE, exactly like the router and PATTERN1 itself: choosing a
# pattern or shaping a plan grants nothing — `capability.authorize` still gates every
# real INVOKE. So a pattern-aware brain has no more authority than the bare rule stub.

# Heuristic surface for "this task warrants planning/dispatch". Deterministic, pure
# string inspection — no model call — so the hook stays reproducible and offline-safe.
_MULTISTEP_HINTS = (
    " then ", " and then ", " after ", "; ", " followed by ",
    " step ", " steps", "first", "finally", " pipeline", " plan ",
)
_COMPLEX_HINTS = (
    "complex", "multi-step", "multistep", "orchestrate", "decompose",
    "review", "approve", "compliance", "audit", "end-to-end", "workflow",
)


def _classify_task(utterance: str):
    """Map a raw utterance onto a `patterns.Task` of deciding features — purely from
    the text, deterministically. Returns (patterns.Task, multi_step: bool). A simple,
    bounded request yields a bare Task (→ single-agent-loop) and multi_step=False, so
    the existing path stays in force; a complex/sequential request flags the features
    that steer PATTERN1 to a richer shape and warrants a PLAN1 decomposition."""
    from decima import patterns as P
    low = (utterance or "").strip().lower()
    name = (utterance or "task").strip()[:64] or "task"

    multi_step = any(h in low for h in _MULTISTEP_HINTS)
    complex_ = any(h in low for h in _COMPLEX_HINTS)
    regulatory = any(h in low for h in ("compliance", "audit", "regulat"))
    quality = any(h in low for h in ("quality", "polish", "draft and edit", "critique"))

    task = P.Task(
        name=name,
        predictability=P.EMERGENT if multi_step else P.PREDEFINED,
        emergent_subtasks=multi_step,
        complex=complex_,
        regulatory=regulatory,
        quality_critical=quality,
    )
    return task, (multi_step or complex_ or regulatory or quality)


def _decompose_subtasks(utterance: str):
    """Split a multi-step utterance into ordered subtask specs for PLAN1 — a linear
    chain (each step depends on the previous), deterministic. Pure string work; never
    raises (a single clause → a one-step chain, which the caller treats as 'simple')."""
    low = (utterance or "")
    # Split on the cheap, common connectives, in priority order.
    parts = None
    for sep in (";", " then ", " and then ", " after that ", " followed by "):
        if sep in low.lower():
            # case-insensitive split on the literal connective span
            import re
            parts = [p.strip() for p in re.split(re.escape(sep), low, flags=re.IGNORECASE)]
            break
    if parts is None:
        parts = [low.strip()]
    parts = [p for p in parts if p]
    specs = []
    prev = None
    for i, clause in enumerate(parts):
        key = f"s{i}"
        spec = {"key": key, "objective": clause[:120] or f"step {i}"}
        if prev is not None:
            spec["depends_on"] = [prev]
        specs.append(spec)
        prev = key
    return specs


def plan_and_dispatch(k, utterance: str, *, author=None, execute: bool = False) -> dict | None:
    """The BRAIN1 hook: consult PATTERN1/DISPATCH1 to choose an orchestration pattern
    for `utterance`, and for a complex/multi-step task use PLAN1 to decompose it into
    ordered subtasks BEFORE acting. The chosen pattern + plan are recorded on the Weft
    with provenance (dispatch records a `dispatch_run`→`dispatched`→`pattern_choice`
    chain; a complex task also gets a `plan` DAG).

    `execute` (DISPATCH1): when True AND the task decomposed into a multi-step plan,
    dispatch runs THAT plan FOR REAL through the kernel's gated delegation — one plan,
    executed once (no duplicate of the brain's decomposition). The execution transcript
    comes back as `lines`. When False (default) the hook is pure advice: it records the
    plan + pattern choice and runs the deterministic stub strategy, exactly as before.

    Returns a dict {pattern, reason, run, choice, plan, plan_steps, multi_step,
    dispatched, executed, lines} on success, or None if the task does not warrant it OR
    anything in the new path fails — so it is INERT, exactly like `_orient`. It NEVER
    raises into the brain's decide loop (a quarantine BYPASS is the one exception —
    that raise IS the boundary), and (in advice mode) grants no authority; in
    execute mode every effect is gated by `execute_plan`/`authorize`."""
    # QUARANTINE BOUNDARY: quarantined data blocks can neither WARRANT a plan nor
    # become worker objectives — planning decomposes only the trusted instruction
    # stream. (Without this, '; '/'then' inside fetched text would spawn workers
    # briefed with attacker-authored objectives.)
    utterance = _screen(utterance)
    try:
        from decima import dispatch as D
        from decima import planning as PL

        task, warrants = _classify_task(utterance)
        if not warrants:
            return None  # a simple, bounded task → leave the existing path untouched

        # Decompose FIRST (a complex/multi-step task is planned before acting).
        plan_id = None
        plan_steps = []
        specs = _decompose_subtasks(utterance)
        if len(specs) >= 2:
            p = PL.plan(k, f"brain:{task.name}", specs, author=author)
            plan_id = p["plan"]
            plan_steps = list(p["topo"])

        # Select the orchestration pattern and — when executing — RUN the brain plan
        # for real through dispatch (records provenance + the real run on the Weft).
        run_real = bool(execute and plan_id is not None)
        result = D.dispatch(k, task, author=author, real=run_real, plan=plan_id if run_real else None)
        return {
            "pattern": result["pattern"],
            "reason": result["reason"],
            "run": result["run"],
            "choice": result["choice"],
            "plan": plan_id,
            "plan_steps": plan_steps,
            "multi_step": len(plan_steps) >= 2,
            "dispatched": True,
            "executed": result.get("executed"),
            "lines": result.get("lines", []),
        }
    except Exception:  # noqa: BLE001 — the hook advises; it must NEVER break the brain
        return None


# ── DISCOVERY-DRIVEN capability choice (BRAIN-DISC) ───────────────────────────
# When the HELD capability set does not cover a goal, the driver consults the
# discovery catalog (decima/discovery.py) to SURFACE a fitting capability — the
# PLUG-IN-OR-FORGE policy made available to the brain. Crucially a surfaced
# capability is DATA, a *suggestion*: it grants nothing. A capability the agent does
# not hold is never resolvable by `_find_named` (which only sees held caps), and
# `capability.authorize` still gates every INVOKE — so the model can PROPOSE from
# discovered capabilities without being handed any authority (no ambient authority).

def suggest_capabilities(k, weave, agent_cell, goal, *, threshold: int = 300,
                         research=None):
    """Surface a fitting capability for `goal` from the discovery catalog when the HELD
    set does not already cover it. Returns a suggestion dict (DATA, `held=False`,
    `instruction_eligible=False`) — action 'use' names a catalog capability the agent
    does NOT hold; a forge/plug_in action names the last-resort path. Returns None
    (inert) for chitchat, an already-held match, an empty catalog, or any failure.

    A suggestion GRANTS NOTHING. It is a hint the model may propose; activating it still
    runs through authorize/Morta. QUARANTINE: the goal is screened first, so quarantined
    /engine-derived data can neither BE the goal nor drive discovery — only the trusted
    instruction stream can (a Quarantined handle here RAISES)."""
    goal = _screen(goal)
    try:
        from decima import discovery
        held_names = {c.content["name"] for c in held_capabilities(weave, agent_cell)}
        d = discovery.discover(k, goal, threshold=int(threshold), research=research)
    except Exception:  # noqa: BLE001 — discovery ADVISES; it must never break decide
        return None
    action = d.get("action")
    if action == "use":
        name = d.get("name")
        if name in held_names:
            return None                      # already covered — nothing to surface
        return {"action": "use", "name": name, "score": int(d.get("score", 0)),
                "manifest": d.get("manifest"), "held": False,
                "instruction_eligible": False}
    if action in ("forge", "forged", "plug_in"):
        return {"action": action, "goal": goal, "name": d.get("name"),
                "candidate": d.get("candidate"), "held": False,
                "instruction_eligible": False}
    return None


def _suggestion_prompt(suggestions) -> str:
    """A system-prompt addendum offering a DISCOVERED capability the agent does not
    hold — as ADVICE only. It states plainly that naming the capability authorizes
    nothing: it must be granted + approved before any INVOKE. Empty when nothing was
    surfaced, so the prompt is unchanged on a covered/chitchat turn."""
    if not suggestions:
        return ""
    if suggestions.get("action") == "use":
        return ("\n\nDISCOVERED but NOT HELD — a SUGGESTION only. Naming it authorizes "
                "NOTHING; you cannot INVOKE it until it is granted and approved: "
                f"“{suggestions.get('name')}” (catalog match {suggestions.get('score')}). "
                "If it fits, RESPOND proposing the user approve it — do not attempt to "
                "invoke it.")
    return ("\n\nNo held or catalog capability covers this goal. The honest move is to "
            "RESPOND that a new capability must be forged (Nona) and approved first.")


# ── MULTI-TURN transcript (BRAIN-MT) — accumulating context across turns ──────
def _collapse_messages(messages) -> list:
    """Fold a transcript into a valid alternating message list: consecutive same-role
    turns are concatenated (so a fenced DATA turn and the trusted instruction that
    follows it ride in one user message, exactly as `present()` fences data ahead of a
    question). Pure and deterministic."""
    out = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if out and out[-1]["role"] == role:
            out[-1]["content"] = out[-1]["content"] + "\n" + content
        else:
            out.append({"role": role, "content": content})
    return out


def _reply_text(action) -> str:
    """A short, deterministic assistant-turn summary for the running transcript — DATA
    about what the brain decided, carried so the next turn has context. It is never
    re-interpreted as an instruction (it is the assistant's own trusted turn)."""
    if action.kind == "invoke":
        return f"[invoked {action.cap}] {action.reasoning or ''}".strip()
    if action.kind == "delegate":
        return f"[delegated {len(action.tasks or [])} task(s)] {action.reasoning or ''}".strip()
    return action.text or "(no reply)"


class BrainSession:
    """Accumulating multi-turn context for a driver.

    The brain reasons over a running transcript, not just the current utterance, so it
    can carry a task across turns and drive EXEC1/DISPATCH1. Two kinds of turn:

      • TRUSTED — the user's instructions and the assistant's own replies: the
        instruction stream. Orientation, routing and planning consult only these.
      • DATA — external / engine-derived content, folded in ONLY via `observe()`, which
        requires a `Quarantined` and carries it as a fenced, neutralized `as_data()`
        block. Prior untrusted content therefore stays DATA on EVERY later turn — an
        injection embedded in an earlier engine result is never instruction-eligible.

    Deterministic under an injected transport, so the oracle stays offline. `k`+agent are
    optional; when present the session also surfaces discovered capabilities per turn
    (advice — no authority)."""

    def __init__(self, brain, k=None, agent_cell=None, *, discovery_threshold: int = 300):
        self.brain = brain
        self.k = k
        self._agent_cell = agent_cell
        self.threshold = int(discovery_threshold)
        self.messages = []          # [{"role","content"}] — trusted text OR fenced DATA
        self.turns = 0

    def observe(self, quarantined):
        """Fold an untrusted engine/external result into the transcript as DATA — the
        ONLY door for external content into the running conversation. It must be a
        `Quarantined` (from quarantine.admit / agent.admit_engine_output) and is carried
        as a fenced, neutralized block, never as an instruction turn."""
        q = require_quarantined(quarantined)
        self.messages.append({"role": "user", "content": q.as_data()})
        return q

    def decide(self, utterance, weave, agent_cell=None, *, discover: bool = True) -> Action:
        """Decide the current TRUSTED utterance in the context of the accumulated
        transcript. Prior turns (incl. any observed DATA) are passed as history; the
        current utterance and the resulting reply are appended AFTER deciding."""
        agent_cell = agent_cell if agent_cell is not None else self._agent_cell
        suggestions = None
        if discover and self.k is not None and agent_cell is not None:
            suggestions = self.brain.suggest_capabilities(
                self.k, weave, agent_cell, utterance, threshold=self.threshold)
        action = self.brain.decide(utterance, weave, agent_cell,
                                   history=list(self.messages), suggestions=suggestions)
        self.messages.append({"role": "user", "content": utterance})
        self.messages.append({"role": "assistant", "content": _reply_text(action)})
        self.turns += 1
        return action


# ── RuleBrain ───────────────────────────────────────────────────────────────
class RuleBrain:
    """Deterministic decider. The offline default and the model brain's fallback."""

    def plan_and_dispatch(self, k, utterance: str, *, author=None, execute: bool = False):
        """Pattern-aware planning/dispatch for a turn (BRAIN1). Thin, additive, inert:
        delegates to the module hook, which is guaranteed never to raise into the brain
        and returns None for a simple task or any failure in the new path. `execute`
        (DISPATCH1) runs the decomposed plan for real through gated delegation."""
        return plan_and_dispatch(k, utterance, author=author, execute=execute)

    def suggest_capabilities(self, k, weave, agent_cell, goal, *, threshold: int = 300,
                             research=None):
        """Discovery-driven suggestion (BRAIN-DISC). Delegates to the module hook; a
        surfaced capability is DATA and grants nothing (authorize still gates INVOKE)."""
        return suggest_capabilities(k, weave, agent_cell, goal, threshold=threshold,
                                    research=research)

    def session(self, k=None, agent_cell=None, *, discovery_threshold: int = 300):
        """A multi-turn session over this brain (see BrainSession)."""
        return BrainSession(self, k=k, agent_cell=agent_cell,
                            discovery_threshold=discovery_threshold)

    def decide(self, utterance, weave, agent_cell, *, history=None, suggestions=None) -> Action:
        # QUARANTINE BOUNDARY: strip fenced data blocks BEFORE anything — pattern
        # matching, orientation, preference steering — can see them. Untrusted
        # bytes inside a fence structurally cannot select a capability, trigger a
        # governance verdict, or shape this decision. A Quarantined handle fed
        # here directly raises (present() is the only door).
        text = _screen(utterance).strip()

        # Orient before deciding: a request that conflicts with a governance rule is
        # refused HERE, with the rule cited — before any capability is matched.
        o = _orient(weave, agent_cell, text)
        blocked = _oriented_block(o)
        if blocked:
            return blocked

        low0 = text.lower()
        if low0.startswith("delegate "):
            # "delegate <cap> as <name>: <objective>"  — separate several with ';' to fan out
            specs = []
            for part in text[len("delegate "):].split(";"):
                head, _, objective = part.partition(":")
                if " as " in head:
                    capn, subn = head.split(" as ", 1)
                else:
                    capn, subn = head, "Worker"
                specs.append({"capability": capn, "subagent": subn,
                              "objective": objective, "budget": None})
            return Action("delegate", tasks=_delegation_specs(specs))

        if ":" in text:                          # "<capname>: payload"
            name, payload = text.split(":", 1)
            cap = _find_named(weave, agent_cell, name)
            if cap:
                return Action("invoke", cap=cap.id, args={"text": payload.strip()})

        low = text.lower()
        if low.startswith("echo "):
            cap = _find_named(weave, agent_cell, "echo")
            if cap:
                return Action("invoke", cap=cap.id, args={"text": text[5:]})
        if low in ("date", "time", "what time is it"):
            cap = _find_named(weave, agent_cell, "shell")
            if cap:
                return Action("invoke", cap=cap.id, args={"cmd": "date"})
        if low.startswith("browse "):
            cap = _find_named(weave, agent_cell, "browser.observe")
            if cap:
                return Action("invoke", cap=cap.id, args={"url": text[len("browse "):].strip()})
        if low.startswith("publish"):
            payload = text.split(":", 1)[1] if ":" in text else text[len("publish"):]
            cap = _find_named(weave, agent_cell, "browser.publish")
            if cap:
                return Action("invoke", cap=cap.id, args={"text": payload.strip()})

        # Nothing matched — let a user PREFERENCE shape the fallback: if orientation
        # names a preferred capability the agent holds, steer to it (a stated value
        # changing the chosen action). Inert when no preference is on the Weave.
        if o is not None:
            pref = o.preferred_capability()
            if pref:
                cap = _find_named(weave, agent_cell, pref)
                if cap:
                    return Action("invoke", cap=cap.id, args={"text": text},
                                  reasoning=f"orientation: preference steers to “{pref}”")

        return Action("respond", text=f"heard “{text}” — no capability matched")


# ── ModelBrain ──────────────────────────────────────────────────────────────
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_ACT_TOOL = {
    "name": "act",
    "description": "Decide how Decima handles the user's utterance: INVOKE one HELD "
                   "capability yourself, DELEGATE to a fresh worker you brief and "
                   "grant one capability to, or RESPOND with a short spoken reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["invoke", "respond", "delegate"]},
            "capability": {"type": "string",
                           "description": "for invoke: the capability to call. for delegate: the "
                                          "capability to GRANT the worker. Either way it must be "
                                          "one you currently hold."},
            "args": {"type": "object",
                     "description": "arguments for INVOKE, e.g. {\"text\": \"...\"} for "
                                    "echo/transform caps, {\"cmd\": \"date\"} for shell"},
            "tasks": {
                "type": "array",
                "description": "for DELEGATE: one entry per worker. List several entries to fan "
                               "out a multi-part job across workers. Each worker reasons over only "
                               "the one capability you grant it (and may itself delegate further).",
                "items": {
                    "type": "object",
                    "properties": {
                        "capability": {"type": "string", "description": "capability to grant (must be one you hold)"},
                        "subagent": {"type": "string", "description": "short worker name"},
                        "objective": {"type": "string", "description": "the brief for this worker"},
                        "budget": {"type": "integer", "description": "optional budget cap on the grant"},
                    },
                },
            },
            "text": {"type": "string", "description": "the spoken reply (action=respond)"},
            "reasoning": {"type": "string", "description": "one short sentence on why"},
        },
        "required": ["action", "reasoning"],
    },
}


class ModelBrain:
    """A real Claude call. Uses stdlib urllib only — no SDK — to preserve the
    Heartbeat's zero-dependency property (the SEAM where the official `anthropic`
    SDK would go in a non-constrained build).

    The brain consults a `Router` to pick a model *tier* per turn (cheap/local,
    retrieval-assisted, frontier, or judge) from a vendor-neutral task descriptor.
    The router only *advises* an engine; `capability.authorize` still gates every
    INVOKE, so routing confers no authority (Law 2 holds above the LLM AND above
    the tier choice)."""

    def __init__(self, api_key, model="claude-opus-4-8", timeout=30, router=None,
                 transport=None):
        self.api_key = api_key
        self.model = model                       # the frontier-tier default engine
        self.timeout = timeout
        self.fallback = RuleBrain()
        # The configured model becomes this brain's frontier tier, so an explicit
        # DECIMA_BRAIN_MODEL still pins the high end while the router can drop to
        # cheaper lanes when the task allows.
        self.router = router or make_router(frontier_model=model)
        # EGRESS SEAM (Phase 1 consistency): the live _post routes through the egress
        # gate, never a bare urlopen (which the armed wire guard now refuses). `transport`
        # is an injected wire-gated transport (the oracle's fake, or wire.real_transport);
        # `egress` is a (k, agent_cell, cap_id) binding from which _post builds the gated
        # transport when none is injected. With neither, a live call is impossible → the
        # brain falls back to RuleBrain (the offline default).
        self.transport = transport
        self.egress = None

    def bind_egress(self, k, agent_cell, cap_id):
        """Bind the egress grant the live _post must pass: subsequent live calls build
        their transport via `egress.live_transport` (the wire gate) from this binding, so
        a call without the grant is denied (EgressDenied) and one with it is recorded as a
        `wire_decision` on the Weft. Returns self for chaining."""
        self.egress = (k, agent_cell, cap_id)
        return self

    def suggest_capabilities(self, k, weave, agent_cell, goal, *, threshold: int = 300,
                             research=None):
        """Discovery-driven suggestion (BRAIN-DISC). A surfaced capability is DATA and
        grants nothing; `capability.authorize` still gates every INVOKE."""
        return suggest_capabilities(k, weave, agent_cell, goal, threshold=threshold,
                                    research=research)

    def session(self, k=None, agent_cell=None, *, discovery_threshold: int = 300):
        """A multi-turn session over this brain (BrainSession): the model reasons over an
        accumulating transcript, with prior untrusted content kept as fenced DATA."""
        return BrainSession(self, k=k, agent_cell=agent_cell,
                            discovery_threshold=discovery_threshold)

    def plan_and_dispatch(self, k, utterance: str, *, author=None, execute: bool = False):
        """Pattern-aware planning/dispatch for a turn (BRAIN1). Identical thin, inert
        hook as RuleBrain: the model brain too consults PATTERN1/DISPATCH1 + PLAN1
        deterministically (no extra model call), recording the choice with provenance
        and never breaking the decide loop. `execute` (DISPATCH1) runs the decomposed
        plan for real through gated delegation."""
        return plan_and_dispatch(k, utterance, author=author, execute=execute)

    def route(self, utterance: str, weave, agent_cell):
        """Pick a tier for this turn. Pure/observable: returns a Routing, performs
        no effect. Exposed so callers (and the smoke oracle) can see the choice."""
        caps = held_capabilities(weave, agent_cell)
        descriptor = describe_task(utterance, [c.content["name"] for c in caps])
        return self.router.route(descriptor)

    def decide(self, utterance: str, weave, agent_cell, *, history=None,
               suggestions=None) -> Action:
        # QUARANTINE BOUNDARY: a Quarantined handle raises (present() is the only
        # door). The model DOES see fenced data blocks — that is the point: the
        # fence + neutralization is the structural data/instruction distinction,
        # and DATA_LAW below binds it in the system prompt — but orientation and
        # routing consult only the trusted instruction stream. `history` (prior turns)
        # is context ONLY — any DATA in it stays fenced/neutralized; it never reaches
        # orientation, routing or planning, which see only the current trusted stream.
        stream = _screen(utterance)
        caps = held_capabilities(weave, agent_cell)
        # Orient first: refuse a governance-conflicting request before spending a
        # call, and surface the user's values to the model so it acts from them.
        o = _orient(weave, agent_cell, stream)
        blocked = _oriented_block(o)
        if blocked:
            return blocked
        routing = self.route(stream, weave, agent_cell)      # tier choice (advice only)
        catalog = "\n".join(
            f"  - {c.content['name']} (effect: {c.content['effect']})" for c in caps
        ) or "  (none)"
        # MULTI-TURN: the model reasons over the accumulated transcript (prior turns)
        # PLUS the current utterance. Any fenced DATA block — in history or this turn —
        # arms DATA_LAW so the model is told, structurally and in words, what is untrusted.
        history = history or []
        has_data = (utterance != stream
                    or any(FENCE_OPEN in (m.get("content") or "") for m in history))
        system = (
            "You are Decima, the orchestrator core of an agent operating system. "
            "You hold a fixed set of capabilities and nothing more. For each utterance, "
            "choose ONE:\n"
            "  • INVOKE — handle it yourself with one capability you hold.\n"
            "  • DELEGATE — spawn a worker: name it, give it an objective, and grant it "
            "ONE capability you hold (optionally budget-capped). The worker reasons over "
            "only that capability. Prefer this when the task is a self-contained job worth "
            "handing to a dedicated worker.\n"
            "  • RESPOND — a brief spoken reply.\n"
            "Never name a capability outside your held set. Replies are spoken aloud — keep "
            "them short and plain.\n\n"
            f"Capabilities you currently hold:\n{catalog}"
            + _orientation_prompt(o)
            + _suggestion_prompt(suggestions)
            + ("\n\n" + DATA_LAW if has_data else "")
        )
        messages = _collapse_messages(
            list(history) + [{"role": "user", "content": utterance}])
        body = {
            "model": routing.model,              # the router-selected tier's engine
            "max_tokens": 1024,
            "system": system,
            "tools": [_ACT_TOOL],
            "tool_choice": {"type": "tool", "name": "act"},
            "messages": messages,
        }
        try:
            data = self._post(body)
        except Exception:  # noqa: BLE001 - any failure (incl. EgressDenied) → deterministic fallback
            return self.fallback.decide(utterance, weave, agent_cell)

        decision = self._extract_tool_input(data)
        if decision is None:
            return self.fallback.decide(utterance, weave, agent_cell)

        reasoning = decision.get("reasoning")
        act = decision.get("action")
        if act == "invoke":
            cap = _find_named(weave, agent_cell, decision.get("capability"))
            if cap:
                args = decision.get("args") or {}
                return Action("invoke", cap=cap.id, args=args, reasoning=reasoning)
            # model chose a capability it doesn't hold — authorize() would deny it
            # anyway; respond instead of attempting an unauthorized INVOKE.
            return Action("respond",
                          text=decision.get("text") or
                          f"I don't hold a “{decision.get('capability')}” capability.",
                          reasoning=reasoning)
        if act == "delegate":
            return Action("delegate", tasks=_delegation_specs(decision.get("tasks")),
                          reasoning=reasoning)
        return Action("respond", text=decision.get("text") or "(no reply)", reasoning=reasoning)

    # -- transport (routed THROUGH the egress gate, never a bare urlopen) ----
    def _transport(self):
        """The wire-gated transport the live call must use: an injected one (the
        oracle's fake, or `wire.real_transport`) or one built from the egress binding
        via `egress.live_transport`. NEVER a bare `urllib.request.urlopen` — the Phase-1
        wire guard, armed on `import decima`, refuses ungated egress at http/https_open.
        With no transport and no binding, a live call is impossible → RuntimeError, and
        `decide` falls back to the deterministic RuleBrain (the offline default)."""
        if self.transport is not None:
            return self.transport
        if self.egress is not None:
            from decima import egress as _eg
            k, agent_cell, cap_id = self.egress
            return _eg.live_transport(k, agent_cell, cap_id, timeout=self.timeout)
        raise RuntimeError(
            "no egress-gated transport bound — a live LLM call must pass the egress gate "
            "(bind_egress / inject transport); refusing to reach the network ungated")

    def _post(self, body: dict) -> dict:
        """The live Anthropic call, funneled through the egress gate. A denial (no grant,
        unapproved, off-allowlist) raises `wire.EgressDenied` BEFORE any socket; an
        allowed call is recorded as a `wire_decision` on the Weft and returns the parsed
        response. The oracle injects a fake socket seam, so no real network is touched."""
        transport = self._transport()
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        status, payload = transport(
            _ENDPOINT, headers, json.dumps(body).encode("utf-8"))
        if status != 200:
            raise RuntimeError(f"anthropic http {status}: {payload}")
        return payload

    @staticmethod
    def _extract_tool_input(data: dict):
        if data.get("stop_reason") == "refusal":
            return None
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "act":
                return block.get("input") or {}
        return None


# ── selection ─────────────────────────────────────────────────────────────
# Back-compat alias: the kernel historically imported `Brain`.
Brain = RuleBrain


def make_brain():
    """Pick a brain from the environment.

    DECIMA_BRAIN="rules" forces the offline rule brain. Otherwise, if
    ANTHROPIC_API_KEY is set, use the model brain (default claude-opus-4-8,
    override with DECIMA_BRAIN_MODEL); else fall back to rules.
    """
    if os.environ.get("DECIMA_BRAIN") == "rules":
        return RuleBrain()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return ModelBrain(key, model=os.environ.get("DECIMA_BRAIN_MODEL", "claude-opus-4-8"))
    return RuleBrain()


# ── engine pipeline: route → generate → verify-or-judge (C2) ─────────────────
# C1 picks a *tier*; C2 makes the tier DO something. `run_task` is the composition:
# route a task to a tier, invoke that tier's engine to generate a candidate, then
# either VERIFY it deterministically (when the task names a checker) or fall back to
# a JUDGE/critic. It is pure with respect to the world — it generates and checks
# text and performs NO effect, so it confers no authority: `capability.authorize`
# still gates every real act, exactly as before the router existed.
@dataclass
class Task:
    """A unit of work to route and run: what to generate, and how to check it."""
    descriptor: TaskDescriptor          # drives tier selection (router.route)
    prompt: str = ""                    # what the engine generates from
    verifier: str | None = None         # name of a deterministic verifier, if any
    spec: dict | None = None            # verifier params (expected / pattern / op+input…)


@dataclass
class TaskRun:
    """The outcome of routing → generating → verifying. Note what is ABSENT: no
    capability, no grant, no principal — a TaskRun carries no authority."""
    tier: str
    model: str
    output: str
    verdict: object                     # verifier.Verdict
    routing: object                     # router.Routing
    stub: bool = True                   # was the engine an offline stub?


def run_task(task: Task, router=None, *, judge=None) -> TaskRun:
    """Route the task, invoke its tier's engine to GENERATE a candidate, then VERIFY
    (deterministically if the task names a checker, else judge/critic fallback).
    Offline-safe: with stub engines and the default judge, the whole pipeline is
    deterministic; the real provider call slots into the engine fn (`live_engine_fn`)
    and a real critic into `judge`."""
    router = router or make_router()
    routing = router.route(task.descriptor)
    engine = router.engine_for(routing)
    result = engine.generate(task.prompt, task.descriptor)
    verdict = verifier.verify(result.output, verifier=task.verifier,
                              spec=task.spec, judge=judge)
    return TaskRun(tier=routing.tier, model=engine.model, output=result.output,
                   verdict=verdict, routing=routing, stub=result.stub)


def live_engine_fn(api_key, *, timeout=30, max_tokens=1024, transport=None):
    """The REAL generation seam: returns an `Engine.fn` that calls the provider
    (Anthropic messages) — no SDK, preserving the zero-dependency property. Wire it
    in with `default_engines(tiers, fn=live_engine_fn(key, transport=t))`.

    PHASE 2 (GO LIVE): the provider call rides a WIRE-GATED `transport(url,
    headers, body) -> (status, json)` — built via `live_wire.gated_transport(k,
    agent_cell, cap_id)` / `egress.live_transport` from a granted, Morta-approved
    egress capability — never a bare urlopen, which the armed wire guard
    (decima/wire.py) refuses. Constructing the live engine fn WITHOUT a gated
    transport fails CLOSED here, with the sanctioned path named, before any
    socket. Raises on any transport error so the caller can fall back to a stub
    engine. `timeout` rides the gated transport itself (see `live_wire`)."""
    if transport is None:                            # fail closed at construction
        from decima import live_wire
        raise live_wire.NoGatedTransport("agent.live_engine_fn")

    def _fn(prompt, descriptor, model, tier):
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        status, data = transport(
            _ENDPOINT,
            {"content-type": "application/json", "x-api-key": api_key,
             "anthropic-version": "2023-06-01"},
            json.dumps(body).encode("utf-8"))
        if status != 200:
            raise RuntimeError(f"anthropic http {status}: {data}")
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    return _fn
