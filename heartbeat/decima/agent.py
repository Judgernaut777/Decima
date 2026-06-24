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


# ── RuleBrain ───────────────────────────────────────────────────────────────
class RuleBrain:
    """Deterministic decider. The offline default and the model brain's fallback."""

    def decide(self, utterance: str, weave, agent_cell) -> Action:
        text = utterance.strip()

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

    def __init__(self, api_key, model="claude-opus-4-8", timeout=30, router=None):
        self.api_key = api_key
        self.model = model                       # the frontier-tier default engine
        self.timeout = timeout
        self.fallback = RuleBrain()
        # The configured model becomes this brain's frontier tier, so an explicit
        # DECIMA_BRAIN_MODEL still pins the high end while the router can drop to
        # cheaper lanes when the task allows.
        self.router = router or make_router(frontier_model=model)

    def route(self, utterance: str, weave, agent_cell):
        """Pick a tier for this turn. Pure/observable: returns a Routing, performs
        no effect. Exposed so callers (and the smoke oracle) can see the choice."""
        caps = held_capabilities(weave, agent_cell)
        descriptor = describe_task(utterance, [c.content["name"] for c in caps])
        return self.router.route(descriptor)

    def decide(self, utterance: str, weave, agent_cell) -> Action:
        caps = held_capabilities(weave, agent_cell)
        routing = self.route(utterance, weave, agent_cell)   # tier choice (advice only)
        catalog = "\n".join(
            f"  - {c.content['name']} (effect: {c.content['effect']})" for c in caps
        ) or "  (none)"
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
        )
        body = {
            "model": routing.model,              # the router-selected tier's engine
            "max_tokens": 1024,
            "system": system,
            "tools": [_ACT_TOOL],
            "tool_choice": {"type": "tool", "name": "act"},
            "messages": [{"role": "user", "content": utterance}],
        }
        try:
            data = self._post(body)
        except Exception:  # noqa: BLE001 - any failure → deterministic fallback
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

    # -- transport ---------------------------------------------------------
    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            _ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

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


def live_engine_fn(api_key, *, timeout=30, max_tokens=1024):
    """The REAL generation seam: returns an `Engine.fn` that calls the provider
    (Anthropic messages) over the same stdlib-urllib transport ModelBrain uses — no
    SDK, preserving the zero-dependency property. Wire it in with
    `default_engines(tiers, fn=live_engine_fn(key))`. Raises on any transport error
    so the caller can fall back to a stub engine."""
    def _fn(prompt, descriptor, model, tier):
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            _ENDPOINT, data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    return _fn
