# GOLIVE — the operator runbook

How to flip Decima from the offline reference posture to LIVE — deliberately,
credential-gated, one grant at a time. Everything below rides gates that already
exist; nothing here creates a new authority path. If you skip a step, the system
does not half-work: it **fails closed** (see the guarantees at the end).

The rail: `heartbeat/decima/golive.py` · shell commands `live`, `grant`,
`secrets` (plus the existing `inbox` / `approve` / `deny`) · boot wiring in
`heartbeat/run.py`. Proof: `heartbeat/checks/418_golive.py`.

---

## (a) Flip the BRAIN live (the model driver over api.anthropic.com)

Copy-paste, in order:

```sh
cd heartbeat
export ANTHROPIC_API_KEY=sk-ant-...      # your real key — read once, held by the broker
python3 run.py                            # warm start (or --fresh from genesis)
```

Boot announces a redacted intake and tells you what is still missing:

```
[go-live] secret 'anthropic': stored (value held by the broker — never on the Weft, never shown)
[go-live] brain: model configured but NOT live — no approved egress grant for api.anthropic.com; ...
```

Then, at the `decima›` prompt:

```
decima› grant api.anthropic.com
   ⏸ grant request for https://api.anthropic.com queued for approval #<id> — nothing is live yet
decima› inbox
   #<id>  [egress.grant:api.anthropic.com]  grant LIVE egress to https://api.anthropic.com — ...
decima› approve <id>
   [Morta] approved → effect ran: egress grant LIVE: {scheme https, host api.anthropic.com} — ...
decima› live
   brain: model — bound to approved egress grant <cap> (api.anthropic.com, https only, wire-gated per call)
   ...
decima› say what can you do right now?
```

That's it. `say` now drives the ModelBrain over a real, wire-gated connection.

Notes:
- The approval is a **Weft event** — it survives restart. On the next boot with
  the key exported, the brain binds automatically; no re-approval needed.
- If you restarted **between** `grant` and `approve`, run `grant api.anthropic.com`
  once more first — it is idempotent (returns the same pending item) and
  re-registers the enactor for this process; then `approve <id>`.
- `deny <id>` records the denial and nothing goes live.
- To shut it off: `revoke <cap-prefix>` (Morta) — the very next call is refused
  at the wire, and re-granting the host requires a **fresh** human approval (a
  revoked grant is never silently resurrected).

## (b) Flip an ENGINE live (Lane B's construction — shape only)

> Engine go-live is owned by Lane B (this cycle's parallel lane). The shape it
> follows is the same rail:

1. **Credential in the broker** — `export DECIMA_SECRET_STRIPE=sk_live_...`,
   then `secrets intake` in the shell (or restart — boot intakes). The name is
   the part after `DECIMA_SECRET_`, lowercased (`stripe`). The broker holds the
   value; the engine receives it only by APPLICATION (`use_secret` runs the
   authenticated call inside the broker — CRED1, dispense-don't-disclose).
2. **Egress grant for the provider host** — `grant api.stripe.com`, then
   `inbox` / `approve <id>` exactly as in (a). The grant confers exactly
   {scheme https, that host} at the wire, per call.
3. **Per-engine test-mode off** — Lane B's flip: the engine is constructed with
   a wire-gated transport (`egress.live_transport(k, agent, cap)`) instead of
   its injected fake, and registers itself in the `k.live_engines` registry so
   `live` reports it honestly. Until that registry entry exists, `live` shows
   `engines live: (none)` — which is the truth.
4. **Verify** — `live` (the doctor), then a small real action, watching the
   `inbox` for its Morta-gated effects.

## (c) What each step does, and the fail-closed guarantees

| Step | What actually happens |
|---|---|
| `export` + boot / `secrets intake` | An **explicit** intake: the value goes into the SecretsBroker's in-memory store only. The Weft records a `credential` REFERENCE (name + content digest, `disclosed: False`) — the CRED1 audit trail with zero secret bytes. Idempotent: an unchanged value appends nothing. |
| `grant <host>` | Forges a per-host egress capability (allowlist = exactly that host) **unapproved**, plus a Morta-gated grant-enactor, and enqueues a durable `inbox_item`. Nothing is live yet; nothing auto-approves. |
| `approve <id>` | The human decision, carried to the SAME authorize/Morta spine as any effect (pinned nonce — approving this grant can never enact anything else). The enactment records the capability-scoped approval the wire gate demands. |
| `live` | The doctor: binds an already-approved brain grant (idempotent, confers nothing) and reports honest, redacted state — wire armed, grants, driver, key **presence**, secret **names**, engines live-vs-test. |
| a live call | Every single call re-runs the wire gate: capability in envelope (no ambient authority) → https-only → host on allowlist → Morta approval live → `wire_decision` Cell on the Weft — **then** the socket. |

Fail-closed guarantees:

- **No grant → no socket.** The wire guard is armed on `import decima`; a bare
  `urlopen`, an engine's raw default transport, or an unapproved/unallowlisted/
  cleartext/revoked call raises `EgressDenied` *before* DNS, before any packet.
  The brain then falls back to the deterministic RuleBrain rather than half-acting.
- **Secrets never touch the Weft.** Not in any Cell, receipt, log line,
  exception, or printout — presence is only ever reported redacted
  ("present"/"held"). Keys are APPLIED inside the broker, never disclosed;
  losing the process loses the raw value (re-run intake), never leaks it.
- **Approval is human-only.** The grant exists only as an inbox item until a
  human `approve`s it; unknown/already-decided items refuse; a revoked grant
  needs a fresh decision. The inbox confers no authority — the ocap/Morta gate
  still decides at enactment.
- **Everything is auditable.** Credential references, inbox items, decisions,
  approvals, and every wire allow/deny land on the Weft with provenance.
