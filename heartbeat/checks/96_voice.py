"""VOX1 — voice contract slice: voice-in proposal, Morta-gated voice-out, audio = DATA.

Proves the voice contract on the live kernel (deterministic stub, no real audio):
  - voice-IN from the authenticated owner yields a PROPOSAL Cell the brain may act
    on (a user turn, never itself a kernel verb);
  - voice-IN from ambient / third-party audio is UNTRUSTED data
    (instruction_eligible=False) — even a clip that embeds an injection is stored,
    never obeyed (the same law the browser receipt obeys);
  - voice-OUT (speech) is a Morta-gated outward effect: denied without approval,
    allowed after.

Real whisper.cpp / Piper engines wrap behind this same contract later; brain
wiring on proposals is deferred to a core cycle. Contract: run(k, line). Fail loud.
"""
from decima import voice


def run(k, line):
    line("\n== VOICE CONTRACT (in→proposal · out Morta-gated · untrusted audio = DATA) ==")
    caps = voice.install(k)

    # 1. voice-IN, authenticated owner → a proposal the brain MAY act on.
    p = voice.transcribe(k, "audio:hello", trusted=True)
    pcell = k.weave().get(p["proposal"])
    line(f"  owner voice → proposal {p['proposal'][:8]}: “{p['text']}” "
         f"instruction_eligible={p['instruction_eligible']}")
    assert pcell.type == voice.PROPOSAL, pcell.type
    assert pcell.content["instruction_eligible"] is True, "owner turn should be actionable"
    assert pcell.content["receipt"], "proposal must link its transcription receipt (provenance)"

    # 2. voice-IN, ambient / third-party → UNTRUSTED data, never an instruction.
    a = voice.transcribe(k, "audio:ambient")            # default: untrusted
    acell = k.weave().get(a["proposal"])
    line(f"  ambient voice → proposal {a['proposal'][:8]}: “{a['text'][:38]}…” "
         f"instruction_eligible={a['instruction_eligible']}")
    assert acell.content["instruction_eligible"] is False, "overheard audio must be DATA"
    assert "ignore your instructions" in acell.content["text"], "the clip's payload is stored…"
    line("    (the clip embeds an injection — stored as DATA, never obeyed)")

    # 3. voice-OUT → outward effect, Morta-gated: denied, then approved.
    denied = voice.speak(k, "Decima online.")
    line(f"  speak (no approval) → ✋ {denied['denied']}")
    assert "denied" in denied and "ok" not in denied, denied
    k.approve(caps["speak"])                              # Morta approval
    ok = voice.speak(k, "Decima online.")
    line(f"  approved → speak → {ok['ok']['out']} (status {ok['status']})")
    assert "ok" in ok and ok["status"] == "SUCCEEDED", ok

    line("  → voice-in is a PROPOSAL (untrusted audio = DATA); voice-out is "
         "Morta-gated. Real whisper.cpp/Piper engines: deferred behind this contract.")
