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


@dataclass
class Action:
    kind: str                       # "invoke" | "respond"
    cap: str | None = None
    args: dict | None = None
    text: str | None = None
    reasoning: str | None = None    # the brain's stated why (model brain)


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

        return Action("respond", text=f"heard “{text}” — no capability matched")


# ── ModelBrain ──────────────────────────────────────────────────────────────
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_ACT_TOOL = {
    "name": "act",
    "description": "Decide how Decima handles the user's utterance: invoke one "
                   "HELD capability, or respond with a short spoken reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["invoke", "respond"]},
            "capability": {"type": "string",
                           "description": "name of the capability to INVOKE (action=invoke); "
                                          "must be one you currently hold"},
            "args": {"type": "object",
                     "description": "arguments for the capability, e.g. {\"text\": \"...\"} for "
                                    "echo/transform caps, {\"cmd\": \"date\"} for shell"},
            "text": {"type": "string", "description": "the spoken reply (action=respond)"},
            "reasoning": {"type": "string", "description": "one short sentence on why"},
        },
        "required": ["action", "reasoning"],
    },
}


class ModelBrain:
    """A real Claude call. Uses stdlib urllib only — no SDK — to preserve the
    Heartbeat's zero-dependency property (the SEAM where the official `anthropic`
    SDK would go in a non-constrained build)."""

    def __init__(self, api_key, model="claude-opus-4-8", timeout=30):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.fallback = RuleBrain()

    def decide(self, utterance: str, weave, agent_cell) -> Action:
        caps = held_capabilities(weave, agent_cell)
        catalog = "\n".join(
            f"  - {c.content['name']} (effect: {c.content['effect']})" for c in caps
        ) or "  (none)"
        system = (
            "You are Decima, the orchestrator core of an agent operating system. "
            "You hold a fixed set of capabilities and nothing more. Given the user's "
            "utterance, either INVOKE exactly one capability you currently hold, or "
            "RESPOND with a brief spoken reply. Never name a capability that is not in "
            "your held set. Keep replies short and plain — they will be spoken aloud.\n\n"
            f"Capabilities you currently hold:\n{catalog}"
        )
        body = {
            "model": self.model,
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
        if decision.get("action") == "invoke":
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
