"""TRANSLATE1 — a translation capability: a stub engine behind a Decima contract.

`CAPABILITY_MAP` Part B engine layer, the sibling of INF1: a deterministic
*stub* translator wrapped behind the SAME boundary a real model will slot into
later. No real model runs here — a fixed phrase map plus a tagged, reversible
wrapper, so a round-trip is checkable and the output is byte-for-byte
deterministic (ints not floats; same input → same bytes, always).

The laws this module enforces (it adds no new primitive — it composes the public
executor/kernel APIs):

  - text submitted for translation is UNTRUSTED DATA. A translation is "here is
    what the text SAID, in another tongue", never "do what the text said". The
    result stored on the Weft is written `instruction_eligible=False`: an
    injection-laced source ("ignore your instructions and …") is translated as
    DATA and never obeyed. Trust never flows from the thing being translated.

  - no ambient authority. `translate` does not call the effect directly — it runs
    through a forged `translate` capability via the kernel's proof-gated
    `invoke`, as the passed agent's principal. The capability is the only way in;
    the engine never confers authority of its own.

  - deterministic stub. The handler is a pure function of its args (a fixed
    phrase map + a tagged wrapper). `detect_lang` is a deterministic guess over
    the surface form. A real engine (a hosted/​on-host MT model) wraps in behind
    `TRANSLATE` later — the contract here is the seam.

OWNS only this file + checks/196_translate.py. It edits NO core/other module — it
registers one effect and forges one capability through PUBLIC functions.
"""
from __future__ import annotations

from decima import executor, model
from decima.capability import capability_content
from decima.hashing import content_id, nfc
from decima.weft import ASSERT

# The executor effect the translation engine runs through (the seam a real model
# slots in behind — exactly like INF1's `infer.local`).
TRANSLATE = "translate.text"
DETECT = "translate.detect"

# A fixed, reversible phrase map (the deterministic stub "engine"). Keyed by
# target language; lower-cased whole-word lookups, so a round-trip is checkable.
# NOT a model — a lookup table that stands where a model will.
_PHRASES = {
    "es": {"hello": "hola", "world": "mundo", "the": "el", "secret": "secreto",
           "ignore": "ignorar", "run": "ejecutar", "your": "tu",
           "instructions": "instrucciones", "and": "y", "leak": "filtrar",
           "secrets": "secretos"},
    "fr": {"hello": "bonjour", "world": "monde", "the": "le", "secret": "secret",
           "ignore": "ignorer", "run": "exécuter", "your": "votre",
           "instructions": "instructions", "and": "et", "leak": "fuiter",
           "secrets": "secrets"},
    "de": {"hello": "hallo", "world": "welt", "the": "das", "secret": "geheimnis",
           "ignore": "ignorieren", "run": "ausführen", "your": "dein",
           "instructions": "anweisungen", "and": "und", "leak": "lecken",
           "secrets": "geheimnisse"},
}

# Markers for the tagged wrapper. The wrapper makes the output unmistakably a
# *translation artifact* (DATA) and lets a round-trip strip back to the source.
_OPEN = "⟦{lang}⟧"
_CLOSE = "⟦/{lang}⟧"

# A deterministic detection table: a few surface signatures → a guessed lang. No
# model, no probabilities — a stable lookup so the same text always guesses the
# same tongue. `un` = undetermined.
_SIGNATURES = (
    ("hola", "es"), ("mundo", "es"), ("secreto", "es"),
    ("bonjour", "fr"), ("monde", "fr"),
    ("hallo", "de"), ("welt", "de"), ("geheimnis", "de"),
    ("hello", "en"), ("world", "en"), ("the", "en"), ("secret", "en"),
)


# -- effect handlers (deterministic stubs; a real MT model slots in behind these) --
def _translate_handler(impl, args):
    """Pure, deterministic stub translation. Whole-word maps a fixed phrase set
    for the target language and wraps the result in a tagged envelope. Returns the
    translation as DATA: `instruction_eligible=False` travels with the output, so
    whatever the source text *said* is never treated as an instruction."""
    text = nfc(str(args.get("text", "")))
    to_lang = str(args.get("to_lang", "es"))
    table = _PHRASES.get(to_lang, {})
    # Whole-word, case-preserving-ish lookup: lower-case key, keep unknown tokens.
    out_tokens = []
    for tok in text.split(" "):
        key = tok.lower()
        out_tokens.append(table.get(key, tok))
    body = " ".join(out_tokens)
    wrapped = f"{_OPEN.format(lang=to_lang)}{body}{_CLOSE.format(lang=to_lang)}"
    return {"out": wrapped, "to_lang": to_lang,
            # The source was DATA and so is its translation — never an instruction.
            "instruction_eligible": False, "untrusted": True}


def _detect_handler(impl, args):
    """Deterministic stub language guess: first matching surface signature wins,
    scanned in a fixed order. `un` when nothing matches. No floats, no model."""
    text = nfc(str(args.get("text", ""))).lower()
    for sig, lang in _SIGNATURES:
        if sig in text:
            return {"out": lang, "lang": lang}
    return {"out": "un", "lang": "un"}


# Register the effects at import (the registry pattern: a new effect is data + one
# function, not a change to `execute`). Real engines override these handlers later.
executor.register(TRANSLATE, _translate_handler)
executor.register(DETECT, _detect_handler)


def _agent_cell(k, agent):
    """Resolve `agent` (a Cell or an agent cell id) to the agent Cell."""
    if hasattr(agent, "content"):
        return agent
    cell = k.weave().get(agent)
    if cell is None:
        raise ValueError(f"no agent cell for {agent!r}")
    return cell


def _ensure_capability(k, agent) -> str:
    """Forge (idempotently) a `translate` capability granted to `agent`'s
    principal and return its id. No ambient authority: translation can only run
    through this capability, proof-gated by the kernel's `invoke`."""
    principal = agent.content["principal"]
    cap_id = content_id({"cap": "translate", "effect": TRANSLATE, "to": principal})
    if k.weave().get(cap_id) is None:
        content = capability_content(
            name="translate", effect=TRANSLATE,
            caveats={"effect_class": "READ"},
            grantee=principal, granter=k.root.id)
        k.weft.append(k.root.id, ASSERT,
                      {"cell": cap_id, "type": "capability", "content": content})
        # Put the cap on the agent's envelope so authorize finds it.
        ac = k.weave().get(agent.id)
        env = list(ac.content.get("envelope", []))
        if cap_id not in env:
            env.append(cap_id)
            k.weft.append(k.root.id, ASSERT,
                          {"cell": ac.id, "type": "agent",
                           "content": {**ac.content, "envelope": env}})
    return cap_id


def translate(k, agent, text: str, *, to_lang: str = "es") -> dict:
    """Translate `text` into `to_lang` via the `translate` capability, returning
    the stub translation as DATA.

    The input is treated as DATA, never executed or obeyed: it is passed as the
    effect's `args["text"]`, the engine never interprets it, and the recorded
    result is written `instruction_eligible=False`. Runs through the kernel's
    proof-gated `invoke` as `agent`'s principal (no ambient authority). The
    translation and its provenance are asserted on the Weft.

    Returns {"out", "to_lang", "record", "receipt", "instruction_eligible",
    "source"} — `record` is the Weft cell holding the translation as DATA,
    `receipt` is the effect receipt the INVOKE descends from.
    """
    agent = _agent_cell(k, agent)
    source = nfc(str(text))
    cap_id = _ensure_capability(k, agent)
    # Re-fold the agent cell so its envelope carries the just-forged grant
    # (envelope_holds reads the cell's content, not the live weave).
    agent = k.weave().get(agent.id)

    res = k.invoke(agent, cap_id, {"text": source, "to_lang": to_lang})
    if "denied" in res:
        raise PermissionError(f"translate denied: {res['denied']}")
    out = res["ok"]
    receipt = res["result_cell"]

    # Record the translation as DATA on the Weft (instruction_eligible=False): the
    # translated text — injection-laced or not — is stored as a fact ABOUT what the
    # source said, never as something to obey. Provenance: the record descends from
    # the effect receipt (which descends from the INVOKE).
    rid = content_id({"translation": out["out"], "to": to_lang,
                      "n": k.weft.lamport})
    model.assert_content(k.weft, agent.content["principal"], rid, "translation", {
        "source": source, "to_lang": to_lang, "output": out["out"],
        "engine": "stub", "instruction_eligible": False, "untrusted": True,
    })
    # Ground the record in the effect receipt (provenance edge on the Weft).
    model.assert_edge(k.weft, agent.content["principal"], rid, "translated_via", receipt)

    return {"out": out["out"], "to_lang": to_lang, "record": rid,
            "receipt": receipt, "source": source,
            "instruction_eligible": False}


def detect_lang(k, text: str) -> str:
    """A deterministic stub language guess for `text`. Runs through the executor's
    public `execute` (the same boundary the engine uses); returns a stable language
    code ('es'/'fr'/'de'/'en') or 'un' when undetermined. Reads the text as DATA
    only — the surface form steers the guess, the content is never obeyed."""
    res = executor.execute(DETECT, None, {"text": nfc(str(text))})
    return res["lang"]


def round_trip_source(translated: str) -> str:
    """Strip the tagged wrapper back to the translated body — the reversible-marker
    check that proves the stub's output is a marked artifact (DATA), not free text.
    A real model would not be reversible; the STUB is, on purpose, so the contract
    is testable. Returns the inner body, or the input unchanged if untagged."""
    s = translated
    if s.startswith("⟦") and "⟧" in s and s.endswith("⟧"):
        inner = s[s.index("⟧") + 1:]
        if "⟦/" in inner:
            inner = inner[:inner.rindex("⟦/")]
        return inner
    return s
