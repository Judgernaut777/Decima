"use strict";
/*
 * Self-extension (Nona) — the promotion boundary as one screen: candidate → evidence →
 * tier → promote / rollback.
 *
 * OWNER: nona lane (wave N6). This file is the lane's whole frontend surface; the only
 * shared frontend files it touches are the pre-wired seams every lane uses (one `reads`/
 * `commands` block in api.js, one <script> tag in index.html, the `.nona-*` rules in
 * app.css).
 *
 * TRUST: THE GENERATED SOURCE ON THIS SCREEN IS ATTACKER-AUTHORED BY CONSTRUCTION. That is
 * the screen's purpose — an organ's implementation is quarantined DATA on the log
 * (`source_is_data: True`, and the detail reader stamps it `trust: "untrusted"` /
 * `instruction_eligible: False`), and it is quarantined DATA here too. It is rendered
 * ONLY through `dom.zone("untrusted", …)` + `el("pre", {text: …})`, which is the workspace
 * lane's existing discipline for a unified diff, reused verbatim: `text` becomes a
 * textContent assignment (dom.js), so the bytes can never become markup, a handler or a
 * <script>. There is no second escaping scheme in this file, no innerHTML, no template
 * interpolation of candidate content into markup, and nothing on this screen is ever
 * handed to a model as prompt.
 *
 * HONESTY OF THE TIER LABEL. The tier → signer-policy mapping is NOT reimplemented here.
 * Every card reads `signer_policy` (from `promotion.signer_policy`) and `note` (from
 * `discovery.executability_note`) off the backend payload, so the label cannot disagree
 * with what the kernel gates on. A `network` organ therefore reads "NOT EXECUTABLE — no
 * mediated egress" (design Decision 2) and offers NO promote button: prompting for
 * something that can never run teaches people to click yes.
 *
 * THE EVIDENCE IS THE FOLDED FACTS. Metrics, findings, `verdict_reason` and
 * `promote_eligible` come from the `evaluation_result` Cell through the lane's readers —
 * the same fold the promote handler re-checks at enactment time — so the view cannot
 * disagree with enforcement. A model judge's opinion is displayed as recorded and
 * powerless, never as a reason anything was promoted.
 *
 * TWO BUTTONS, TWO CONSEQUENCES. Promote and Rollback are GATED commands: clicking them
 * submits a proposal that lands in the Approval inbox and changes nothing else, and the
 * decision is taken there (this screen hosts no approve control). Rollback is DEMOTION —
 * the organ re-quarantines and its grants, receipts and history survive — and it is
 * labelled that way, never as "revoke", which is the other command on the other screen.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  // Wording for the three signer policies the BACKEND reports. This is presentation only:
  // the tier→policy decision lives in promotion.SIGNER_POLICY and arrives as a token.
  var POLICY_LABEL = {
    automated: "AUTOMATED — the anchored Reckoner may sign this tier",
    human: "HUMAN attestation required before this tier may be promoted",
    not_executable: "NOT EXECUTABLE — no mediated egress"
  };
  var POLICY_KIND = { automated: "ok", human: "warn", not_executable: "bad" };

  // How a promotion of this tier is SHOWN (powerbox.inbox_surface): a low-blast-radius
  // promotion is reported, not solicited.
  var SURFACE_LABEL = {
    notification: "notification — recorded and revocable; no decision solicited",
    canary: "canary — a notification with a visible rollback affordance",
    approval: "explicit approval — the evidence is shown inline"
  };

  var DISCOVERY_LABEL = {
    use: "USE — something in the catalogue already does this",
    plug_in: "PLUG IN — a researched tool exists outside the catalogue",
    forge: "FORGE — nothing matched; the next step is proposing a candidate"
  };

  function policyPill(policy) {
    var key = String(policy || "");
    return ui.pill(POLICY_LABEL[key] || key || "—", POLICY_KIND[key] || "neutral");
  }

  // An id-bearing span. `.nona-id` may break INSIDE the token (app.css): a 56-char base32
  // id has no break opportunity and would otherwise widen the document.
  function idNode(value) {
    return el("span", { class: "nona-id", text: value || "—" });
  }

  function quarantineNode(candidate) {
    if (candidate.capability === null || candidate.capability === undefined) {
      return ui.pill("no organ grant yet", "neutral");
    }
    if (candidate.quarantined === false) {
      return ui.pill("PROMOTED — sandbox_only lifted", "ok");
    }
    return ui.pill("QUARANTINED — sandbox only", "warn");
  }

  // The honest sentence for an organ nothing can run, straight from the backend.
  function noteNode(note) {
    return note ? el("p", { class: "nona-note", text: note }) : null;
  }

  function intNode(value) {
    return el("span", { class: "nona-int", text: String(value) });
  }

  // ---- evidence (the folded evaluation_result the gate itself read) ------------
  function metricsNode(metrics) {
    var keys = Object.keys(metrics || {}).sort();
    if (!keys.length) {
      return D.dom.empty("No metrics recorded.");
    }
    return ui.fields(keys.map(function (k) {
      return [k.replace(/_/g, " "), intNode(metrics[k])];
    }));
  }

  function findingsNode(findings) {
    if (!findings || !findings.length) {
      return el("p", { class: "muted", text: "No security findings recorded." });
    }
    return el("ul", { class: "nona-findings" }, findings.map(function (f) {
      return el("li", { class: "nona-finding" }, [
        ui.pill(String(f.severity || "?").toUpperCase(),
          String(f.severity) === "high" ? "bad" : "warn"),
        el("span", { class: "nona-finding-rule", text: " " + (f.rule || "") + " — " }),
        el("span", { class: "nona-finding-detail", text: f.detail || "" })
      ]);
    }));
  }

  function evidenceCard(ev) {
    var judge = ev.model_judge || {};
    var children = [
      ui.sectionTitle("Evidence", ev.promote_eligible ? "promote-eligible" : "not eligible"),
      ui.fields([
        ["Evaluation", idNode(ev.evaluation)],
        ["Suite", idNode(ev.suite)],
        ["Judged digest", idNode(ev.implementation_digest)],
        ["Promote eligible", ui.pill(ev.promote_eligible ? "yes" : "no",
          ev.promote_eligible ? "ok" : "bad")],
        ["Verdict reason", ev.verdict_reason],
        ["Environment", idNode(ev.environment)]
      ]),
      ui.sectionTitle("Metrics (integers, as recorded)"),
      metricsNode(ev.metrics),
      ui.sectionTitle("Security findings"),
      findingsNode(ev.findings)
    ];
    if ((ev.failures || []).length) {
      children.push(ui.sectionTitle("Recorded failures"));
      children.push(el("ul", { class: "nona-failures" }, ev.failures.map(function (f) {
        return el("li", { text: String(f) });
      })));
    }
    if (judge.verdict !== undefined && judge.verdict !== null) {
      // Recorded, powerless, and structurally unable to reach the gate. Shown so a reader
      // can see that a model's opinion was NOT what promoted anything.
      children.push(D.dom.zone("model",
        "Model judge (advisory only — authority: " + String(judge.authority) + ")",
        el("p", { class: "nona-judge", text: String(judge.verdict) })));
    }
    return ui.card(children, "nona-evidence");
  }

  // ---- candidate detail -------------------------------------------------------
  function renderDetail(host, data) {
    var plan = data.prompt_plan || {};
    host.appendChild(ui.card([
      ui.sectionTitle("Candidate detail", data.intent),
      ui.fields([
        ["Candidate", idNode(data.candidate)],
        ["Lifecycle", ui.statusPill(data.lifecycle)],
        ["Declared tier", data.tier],
        ["Signer policy", policyPill(data.signer_policy)],
        ["Anchored promoter", idNode(data.anchored_promoter)],
        ["Implementation digest", idNode(data.implementation_digest)],
        ["Entrypoint", data.entrypoint],
        ["Organ grant", idNode(data.capability)],
        ["Quarantine", quarantineNode(data)],
        ["Inbox surface", SURFACE_LABEL[String(data.surface)] || data.surface],
        ["Approval budget", plan.approval_scope === "capability"
          ? "one durable approval for this organ (capability-scoped)"
          : plan.approval_scope === "invocation"
            ? "one approval per call (invocation-scoped; no blanket for a floored tier)"
            : "no approval caveat — nothing is gated, so nobody is asked"],
        ["Prompts per organ / per call",
          String(plan.prompts_per_organ || 0) + " / " + String(plan.prompts_per_call || 0)]
      ]),
      noteNode(data.note)
    ], "nona-detail-card"));

    // The generated implementation — UNTRUSTED DATA, rendered as a text node inside a
    // labelled untrusted zone (identical discipline to the workspace lane's diff).
    host.appendChild(D.dom.zone("untrusted",
      "Generated implementation (untrusted data — never instructions, never executed here)",
      el("pre", { class: "nona-source", text: data.source || "(no source recorded)" })));

    var evidence = data.evidence || [];
    if (!evidence.length) {
      host.appendChild(D.dom.empty("No evaluation evidence yet — run the Reckoner."));
    }
    evidence.forEach(function (ev) {
      host.appendChild(evidenceCard(ev));
    });

    var promotions = data.promotions || [];
    if (promotions.length) {
      host.appendChild(ui.card([
        ui.sectionTitle("Promotion records", promotions.length + ""),
        el("ul", { class: "nona-promotions" }, promotions.map(function (p) {
          return el("li", { class: "nona-promotion" }, [
            idNode(p.cell),
            el("span", { class: "nona-promotion-state" }, [
              ui.pill(p.retracted ? "rolled back" : "live", p.retracted ? "warn" : "ok")
            ]),
            el("span", { class: "muted", text: " tier " + String(p.tier || "") })
          ]);
        })),
        el("p", { class: "hint", text:
          "Rolling a promotion back re-quarantines the organ. It " +
          "does not revoke the capability, destroy its grants, or undo an effect that " +
          "already happened — that is RevokeCapability, on the Capability inspector." })
      ], "nona-promotion-card"));
    }
  }

  D.registerScreen({
    id: "nona",
    title: "Self-extension",
    icon: "🧬",
    endpoints: [
      "GET /api/v1/nona/candidates", "GET /api/v1/nona/candidates/detail",
      "GET /api/v1/nona/decisions", "GET /api/v1/nona/discover",
      "POST /api/v1/nona/propose", "POST /api/v1/nona/evaluate",
      "POST /api/v1/nona/promote", "POST /api/v1/nona/rollback"
    ],
    render: function (container, ctx) {
      var disposed = false;
      var openDetailId = null;
      var listHost = null;        // stable region: the candidate list re-renders here
      var detailHost = null;      // stable region: the open candidate's detail
      var decisionsHost = null;   // stable region: the pending gated decisions
      var discoverHost = null;    // stable region: the last discovery answer

      // A gated command's 202 is the SUCCESS path: the proposal is in the inbox and
      // nothing else moved. Same handling as the capability inspector's revoke/terminate.
      function handleGated(r, label) {
        if (r.status === 202 || (r.data && r.data.required_approval)) {
          ctx.toast(label + " sent to the Approval inbox — nothing has changed yet", "warn");
          ctx.refreshBadges();
        } else if (r.ok) {
          ctx.toast(label + " applied", "ok");
        } else {
          ctx.toast(label + " refused (" +
            ((r.data && r.data.reason_code) || r.status) + ")", "bad");
        }
      }

      function refusalText(r) {
        return "Refused (" + ((r.data && r.data.reason_code) || r.status) + "): " +
          ((r.data && r.data.error) || "");
      }

      // ---- candidate list -----------------------------------------------------
      function candidateCard(c) {
        // The operator's baseline cases, per candidate. They are BASELINE input, not
        // candidate-authored: the adversarial cases the organ is judged by come from the
        // lane's root-side constant and can never be supplied from here (Decision 6). A
        // class, not an id, so a list of candidates has no duplicate ids.
        var cases = el("textarea", { class: "nona-cases", rows: "2",
          placeholder: '[{"in": {"x": 1}, "out": 2}]',
          "aria-label": "Baseline cases as JSON" });
        var caseError = el("p", { class: "form-error nona-case-error", text: "" });

        var actions = [
          el("button", {
            type: "button", class: "btn nona-evaluate", dataset: { candidate: c.candidate },
            text: "Run the Reckoner",
            on: { click: async function () {
              caseError.textContent = "";
              var body = { candidate: c.candidate };
              var raw = (cases.value || "").trim();
              if (raw) {
                // Parsed HERE so a typo is a form error rather than a 400 from the API.
                // JSON.parse builds plain data — it is not an execution path, and the
                // parsed cases are sent as arguments, never rendered as markup.
                try {
                  body.cases = JSON.parse(raw);
                } catch (e) {
                  caseError.textContent = "Cases must be a JSON array: " + (e && e.message);
                  return;
                }
                if (!Array.isArray(body.cases)) {
                  caseError.textContent = "Cases must be a JSON array.";
                  return;
                }
              }
              var r = await ctx.api.commands.evaluateCandidate(body);
              if (r.ok) {
                ctx.toast((r.data && r.data.data && r.data.data.promote_eligible)
                  ? "Evaluation recorded: promote-eligible"
                  : "Evaluation recorded: NOT promote-eligible", "ok");
              } else {
                caseError.textContent = refusalText(r);
                ctx.toast(refusalText(r), "bad");
              }
              await renderCandidates();
              await renderDetailRegion();
            } }
          }),
          el("button", {
            type: "button", class: "btn nona-open", dataset: { candidate: c.candidate },
            text: openDetailId === c.candidate ? "Close detail" : "Open detail",
            on: { click: function () {
              openDetailId = openDetailId === c.candidate ? null : c.candidate;
              renderDetailRegion();
              renderCandidates();
            } }
          })
        ];
        // Promote is offered ONLY when something could actually run the organ AND eligible
        // evidence exists. A tier with no executor gets the honest note instead of a
        // button, because no approval can conjure an executor.
        if (c.executable && c.eligible_evaluation) {
          actions.push(el("button", {
            type: "button", class: "btn nona-promote", dataset: { candidate: c.candidate },
            text: "Propose promotion",
            on: { click: async function () {
              var r = await ctx.api.commands.promoteCandidate({
                candidate: c.candidate, evaluation: c.eligible_evaluation
              });
              handleGated(r, "Promotion");
              await renderCandidates();
              await renderDecisions();
            } }
          }));
        }
        if ((c.live_promotions || []).length) {
          actions.push(el("button", {
            type: "button", class: "btn btn-danger nona-rollback",
            dataset: { promotion: c.live_promotions[0] },
            text: "Propose rollback (demote)",
            on: { click: async function () {
              var r = await ctx.api.commands.rollbackPromotion({
                promotion: c.live_promotions[0]
              });
              handleGated(r, "Rollback");
              await renderCandidates();
              await renderDecisions();
            } }
          }));
        }

        var card = ui.card([
          el("div", { class: "row-head nona-head" }, [
            el("strong", { class: "nona-intent", text: c.intent || c.candidate }),
            ui.pill("tier " + String(c.tier || "—"), "neutral")
          ]),
          ui.fields([
            ["Candidate", idNode(c.candidate)],
            ["Lifecycle", ui.statusPill(c.lifecycle)],
            ["Signer policy", policyPill(c.signer_policy)],
            ["Quarantine", quarantineNode(c)],
            ["Evidence", (c.evaluations || []).length + " evaluation(s)" +
              (c.eligible_evaluation ? " — one is promote-eligible" : "")],
            ["Organ grant", idNode(c.capability)]
          ]),
          noteNode(c.note),
          el("label", { class: "nona-case-label", text: "Baseline cases (JSON array)" }),
          cases,
          el("div", { class: "actions nona-actions" }, actions),
          caseError
        ], "nona-candidate");
        card.dataset.candidate = c.candidate;
        return card;
      }

      var listGen = 0;
      async function renderCandidates() {
        if (!listHost) {
          return;
        }
        var gen = ++listGen;
        var r = await ctx.api.reads.nonaCandidates();
        if (disposed || gen !== listGen || !listHost) {
          return;
        }
        var items = (r.data && r.data.items) || [];
        D.dom.clear(listHost);
        if (!items.length) {
          listHost.appendChild(D.dom.empty(
            "No candidates yet — propose one above (the source is data, never a program)."));
        }
        items.forEach(function (c) {
          listHost.appendChild(candidateCard(c));
        });
      }

      var detailGen = 0;
      async function renderDetailRegion() {
        if (!detailHost) {
          return;
        }
        var gen = ++detailGen;
        if (!openDetailId) {
          D.dom.clear(detailHost);
          return;
        }
        var r = await ctx.api.reads.nonaCandidateDetail(openDetailId);
        if (disposed || gen !== detailGen) {
          return;
        }
        D.dom.clear(detailHost);
        if (!r.ok || !r.data) {
          detailHost.appendChild(D.dom.empty("Could not load candidate detail."));
          return;
        }
        renderDetail(detailHost, r.data);
      }

      // ---- pending gated decisions (decided in the Approval inbox) ------------
      function decisionCard(item) {
        var rows = [
          ["Inbox item", idNode(item.item)],
          ["Command", item.command],
          ["Status", ui.statusPill(item.status)]
        ];
        if (item.command === "PromoteCandidate") {
          rows.push(["Candidate", idNode(item.candidate)]);
          rows.push(["Intent", item.intent]);
          rows.push(["Declared tier", item.tier]);
          rows.push(["Signer policy", policyPill(item.signer_policy)]);
          rows.push(["Surface", SURFACE_LABEL[String(item.surface)] || item.surface]);
        } else {
          rows.push(["Promotion", idNode(item.promotion)]);
          rows.push(["Organ grant", idNode(item.capability)]);
          rows.push(["Effect", "demotion — the organ returns to needing a sandbox"]);
          rows.push(["Revokes the capability", "no"]);
        }
        var children = [
          el("div", { class: "row-head" }, [
            el("strong", { text: item.command === "PromoteCandidate"
              ? "Promotion awaiting a human decision"
              : "Rollback awaiting a human decision" }),
            ui.statusPill(item.status)
          ]),
          ui.fields(rows),
          noteNode(item.note)
        ];
        // The explicit surface owes the reader the evidence; a notification may collapse
        // it, but it is always available on the candidate's detail.
        if (item.evidence_inline && item.evidence && item.evidence.evaluation) {
          children.push(evidenceCard(item.evidence));
        }
        children.push(el("p", { class: "hint", text:
          "Decide this in the Approval inbox: it needs a fresh reauth and records a " +
          "signed, single-use decision. Submitting it here changed nothing else." }));
        return ui.card(children, "nona-decision");
      }

      var decisionsGen = 0;
      async function renderDecisions() {
        if (!decisionsHost) {
          return;
        }
        var gen = ++decisionsGen;
        var items = await ctx.api.reads.nonaDecisions();
        if (disposed || gen !== decisionsGen || !decisionsHost) {
          return;
        }
        D.dom.clear(decisionsHost);
        if (!items.length) {
          decisionsHost.appendChild(D.dom.empty("No promotion or rollback decisions pending."));
        }
        items.forEach(function (item) {
          decisionsHost.appendChild(decisionCard(item));
        });
      }

      // ---- discovery: plug-in-or-forge, in that order -------------------------
      function discoveryNode(answer) {
        var children = [
          ui.sectionTitle("Discovery answer",
            DISCOVERY_LABEL[String(answer.action)] || String(answer.action)),
          ui.fields([
            ["Goal", answer.goal],
            ["Mode", answer.mode],
            ["Threshold (integer)", intNode(answer.threshold)],
            ["Action", ui.pill(String(answer.action), "neutral")]
          ])
        ];
        if (answer.action === "use") {
          children.push(ui.fields([
            ["Capability", idNode(answer.capability)],
            ["Score", intNode(answer.score)],
            ["A grant is still required", answer.grant_required ? "yes" : "no"]
          ]));
          children.push(noteNode(answer.note));
        } else if (answer.reason) {
          children.push(el("p", { class: "nona-note", text: answer.reason }));
          children.push(el("p", { class: "hint",
            text: "Next step: " + String(answer.next_step || "ProposeCapability") +
              " — nothing is activated or installed by a search." }));
        }
        var matches = answer.matches || [];
        if (matches.length) {
          children.push(ui.sectionTitle("Ranked catalogue", matches.length + " match(es)"));
          children.push(el("ul", { class: "nona-matches" }, matches.map(function (m) {
            return el("li", { class: "nona-match" }, [
              idNode(m.capability),
              intNode(m.score),
              // WHY it ranked: the exact tokens that matched, not a bare number.
              el("span", { class: "nona-tokens",
                text: " matched: " + (m.matched_tokens || []).join(", ") }),
              ui.pill(m.executable ? "executable" : "not executable",
                m.executable ? "ok" : "bad")
            ]);
          })));
        }
        return ui.card(children, "nona-discovery");
      }

      // ---- the one-time skeleton (forms stay STABLE across re-renders) --------
      function buildDiscoverForm() {
        var goal = el("input", { id: "nona-goal", type: "text",
          placeholder: "what do you need done?", "aria-label": "Discovery goal" });
        var error = el("p", { class: "form-error", id: "nona-discover-error", text: "" });
        async function onSearch(event) {
          event.preventDefault();
          error.textContent = "";
          var q = (goal.value || "").trim();
          if (!q) {
            // Guarded client-side: an empty goal is a 400 from the reader, and firing a
            // request we know is invalid is not an honest search.
            error.textContent = "Enter a goal first.";
            return;
          }
          var r = await ctx.api.reads.nonaDiscover(q);
          if (disposed || !discoverHost) {
            return;
          }
          D.dom.clear(discoverHost);
          if (!r.ok || !r.data) {
            error.textContent = refusalText(r);
            return;
          }
          discoverHost.appendChild(discoveryNode(r.data));
        }
        return el("form", { class: "inline-form nona-discover-form",
          id: "nona-discover-form", on: { submit: onSearch } }, [
          goal,
          el("button", { type: "submit", id: "nona-discover", text: "Search the catalogue" }),
          error
        ]);
      }

      function buildProposeForm(effectClasses) {
        var intent = el("input", { id: "nona-intent", type: "text", required: "required",
          placeholder: "what should this organ do?", "aria-label": "Candidate intent" });
        var tier = el("select", { id: "nona-tier", "aria-label": "Declared effect class" },
          effectClasses.map(function (name) {
            return el("option", { value: name, text: name });
          }));
        var source = el("textarea", { id: "nona-source", rows: "6", required: "required",
          placeholder: "implementation source — stored as DATA, never executed here",
          "aria-label": "Implementation source" });
        // The declared output contract. Left empty on purpose when the operator has none:
        // the Reckoner then records a "no output schema" finding, which is the honest
        // evidence. Defaulting it to `any` would manufacture a contract that checks
        // nothing while looking like one.
        var outputType = el("input", { id: "nona-output-type", type: "text",
          placeholder: "int / str / bool / list / dict / null (blank = no contract)",
          "aria-label": "Declared output type" });
        var error = el("p", { class: "form-error", id: "nona-propose-error", text: "" });

        async function onPropose(event) {
          event.preventDefault();
          error.textContent = "";
          if (!(source.value || "").trim()) {
            // With no source and no bound codegen seam the backend refuses NOT_AVAILABLE
            // rather than emitting a stub organ; say so here instead of firing that call.
            error.textContent =
              "Supply the implementation as data: this install has no codegen seam bound, " +
              "and the lane refuses to emit a stub organ.";
            return;
          }
          var body = {
            intent: intent.value,
            effect_class: tier.value,
            source: source.value
          };
          var declared = (outputType.value || "").trim();
          if (declared) {
            body.output_schema = { type: declared };
          }
          var r = await ctx.api.commands.proposeCapability(body);
          if (!r.ok) {
            error.textContent = refusalText(r);
            return;
          }
          ctx.toast("Candidate proposed — born QUARANTINED, nothing runs yet", "ok");
          intent.value = "";
          source.value = "";
          await renderCandidates();
        }

        return el("form", { class: "stacked-form nona-propose-form", id: "nona-propose-form",
          on: { submit: onPropose } }, [
          el("label", { text: "Intent" }), intent,
          el("label", { text: "Declared effect class (tier)" }), tier,
          el("label", { text: "Declared output type" }), outputType,
          el("label", { text: "Implementation source (data)" }), source,
          el("button", { type: "submit", id: "nona-propose", text: "Propose candidate" }),
          error
        ]);
      }

      async function refresh() {
        var r = await ctx.api.reads.nonaCandidates();
        if (disposed) {
          return;
        }
        D.dom.clear(container);
        container.appendChild(ui.sectionTitle("Self-extension",
          "candidate → evidence → tier → promote / rollback"));
        container.appendChild(el("p", { class: "hint", text:
          "An organ is born quarantined and sandbox-only. Promotion and rollback are gated " +
          "commands: they are proposed here and decided, with a fresh reauth, in the " +
          "Approval inbox. Generated source is untrusted data — it is displayed, never run " +
          "in this browser and never used as instructions." }));

        if (!r.ok || !r.data) {
          container.appendChild(D.dom.empty("Could not load candidates."));
          return;
        }
        var promoters = r.data.anchored_promoters || {};
        var promoterIds = Object.keys(promoters).sort();
        container.appendChild(D.dom.zone("system", "Promotion authority anchored on this store",
          promoterIds.length
            ? el("ul", { class: "nona-promoters" }, promoterIds.map(function (p) {
                return el("li", { class: "nona-promoter" }, [
                  idNode(p),
                  el("span", { class: "muted",
                    text: " may sign: " + (promoters[p] || []).join(", ") })
                ]);
              }))
            : D.dom.empty(
                "No promoter is anchored on this store — no tier can be promoted here.")));
        container.appendChild(el("p", { class: "hint", text:
          "Signable tiers: " + (r.data.signable_tiers || []).join(", ") +
          ". Every other tier may be authored and evaluated but never promoted to " +
          "runnable — an approval cannot conjure an executor." }));

        container.appendChild(ui.card([
          ui.sectionTitle("Plug in before forging",
            "rank the catalogue first; most gaps are not gaps"),
          buildDiscoverForm()
        ], "nona-discover-card"));
        discoverHost = el("div", { class: "nona-discover-result", id: "nona-discover-result" });
        container.appendChild(discoverHost);

        container.appendChild(ui.card([
          ui.sectionTitle("Propose a candidate", "born DRAFT → QUARANTINED; nothing executes"),
          buildProposeForm(r.data.effect_classes || [])
        ], "nona-propose-card"));

        listHost = el("div", { class: "nona-candidates" });
        container.appendChild(ui.card([
          ui.sectionTitle("Candidates"),
          listHost
        ], "nona-candidates-card"));

        detailHost = el("div", { class: "nona-detail", id: "nona-detail" });
        container.appendChild(detailHost);

        decisionsHost = el("div", { class: "nona-decisions" });
        container.appendChild(ui.card([
          ui.sectionTitle("Gated decisions", "promote / rollback awaiting a human"),
          decisionsHost
        ], "nona-decisions-card"));

        await renderCandidates();
        await renderDetailRegion();
        await renderDecisions();
      }

      refresh();
      return function cleanup() {
        disposed = true;
      };
    }
  });
})(typeof window !== "undefined" ? window : this);
