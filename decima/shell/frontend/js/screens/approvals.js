"use strict";
/*
 * Approval inbox — the TRUSTED human-decision surface (invariant 5, handoff §9).
 *
 * This is the ONLY place approval action buttons exist. The buttons live in trusted Shell
 * chrome — never inside a rendered model/imported message — so an agent message can never
 * imitate this approval UI. Each pending card discloses, per the handoff: the requesting
 * agent, the effect, the exact target, the args, whether data leaves the machine, the
 * provider, the max cost, the expiry, reversibility, the causal step, and the reason.
 * Values the API does not disclose are shown explicitly as "not disclosed" rather than
 * invented. Actions: DENY, APPROVE ONCE, and APPROVE WITH STRICTER LIMITS. Approve is
 * reauth-gated: a trusted modal collects the pairing secret for that single call and never
 * stores it. There is deliberately NO standing "approve everything from this agent"
 * control — every gated effect is decided one at a time.
 *
 * Reads GET /api/v1/approvals; acts via POST /api/v1/approvals/deny and
 * POST /api/v1/approvals/approve (X-Reauth).
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  // What we can infer about a gated command when the API does not spell it out. This is a
  // conservative, display-only classification — it never changes what the backend does.
  var EFFECT_META = {
    TerminateAgent: { leaves: "no (local)", provider: "local kernel", reversible: "no — the agent is stopped" },
    RevokeCapability: { leaves: "no (local)", provider: "local kernel", reversible: "partial — re-granting is a new grant" },
    ImportArtifact: { leaves: "no — inbound only", provider: "local", reversible: "yes — retract the artifact" },
    ExportArtifact: { leaves: "YES — content leaves this machine", provider: "destination in args", reversible: "no — data has left" }
  };

  function commandOf(approval) {
    var cap = approval.capability || "";
    var m = /^local:(.+)$/.exec(cap);
    if (m) {
      return m[1];
    }
    var desc = approval.description || "";
    return desc.split(" ")[0] || "unknown effect";
  }

  function disclose(value) {
    return value === null || value === undefined || value === ""
      ? el("span", { class: "undisclosed", text: "not disclosed by API" })
      : String(value);
  }

  function pendingCard(approval, ctx) {
    var command = commandOf(approval);
    var meta = EFFECT_META[command] || {};
    var header = el("div", { class: "approval-head" }, [
      el("span", { class: "approval-effect", text: command }),
      ui.statusPill(approval.state)
    ]);
    // The full disclosure block, rendered as text (untrusted args never become markup).
    var body = ui.fields([
      ["Requesting agent", disclose(approval.requesting_agent)],
      ["Effect", command],
      ["Exact target", disclose(approval.capability)],
      ["Arguments", disclose(approval.description)],
      ["Data leaving machine", disclose(meta.leaves)],
      ["Provider", disclose(meta.provider)],
      ["Max cost", disclose(approval.max_cost)],
      ["Expiry (logical)", disclose(approval.expires_at)],
      ["Reversibility", disclose(meta.reversible)],
      ["Causal step", disclose(approval.causal_step)],
      ["Reason", disclose(approval.reason)],
      ["Approval item id", approval.item]
    ]);

    var actions = el("div", { class: "approval-actions" }, [
      el("button", {
        type: "button", class: "btn btn-danger", text: "Deny",
        on: { click: function () { deny(approval, ctx); } }
      }),
      el("button", {
        type: "button", class: "btn btn-primary", text: "Approve once",
        on: { click: function () { approve(approval, ctx, null); } }
      }),
      el("button", {
        type: "button", class: "btn", text: "Approve with stricter limits",
        on: { click: function () { approveStricter(approval, ctx); } }
      })
    ]);

    // The whole card is a trusted zone-human element. The "trusted" data-attribute is set
    // by Shell code only; a rendered message can never produce this chrome.
    var card = el("div", { class: "approval-card", dataset: { trusted: "1" } },
      [header, body, actions]);
    return card;
  }

  function decidedCard(approval) {
    return ui.card([
      el("div", { class: "row-head" }, [
        el("strong", { text: commandOf(approval) }),
        ui.statusPill(approval.state)
      ]),
      ui.fields([
        ["Item", approval.item],
        ["Decision", approval.decision],
        ["Approver", approval.approver],
        ["Ran", approval.ran ? "yes" : "no"],
        ["Target", approval.capability]
      ])
    ], "card-decided");
  }

  // -- decisions ---------------------------------------------------------
  async function deny(approval, ctx) {
    var reason = window.prompt("Reason for denying " + commandOf(approval) + " (optional):", "");
    if (reason === null) {
      return; // cancelled
    }
    var r = await ctx.api.commands.denyInvocation({ item: approval.item, reason: reason });
    if (r.ok) {
      ctx.toast("Denied", "ok");
      ctx.refreshActive();
      ctx.refreshBadges();
    } else {
      ctx.toast("Deny failed: " + reasonOf(r), "bad");
    }
  }

  async function approve(approval, ctx, limits) {
    var secret = await reauthPrompt(commandOf(approval));
    if (secret === null) {
      return;
    }
    var args = { item: approval.item };
    if (limits) {
      args.stricter_limits = limits;
    }
    var r = await ctx.api.commands.approveInvocation(args, secret);
    if (r.ok) {
      ctx.toast(limits ? "Approved with stricter limits" : "Approved once", "ok");
      ctx.refreshActive();
      ctx.refreshBadges();
    } else {
      ctx.toast("Approve failed: " + reasonOf(r), "bad");
    }
  }

  function approveStricter(approval, ctx) {
    openModal("Approve with stricter limits", function (modalBody, close) {
      var maxCost = el("input", { type: "number", class: "input", placeholder: "max cost", min: "0" });
      var expiry = el("input", { type: "number", class: "input", placeholder: "expiry (logical steps)", min: "0" });
      modalBody.appendChild(el("p", {
        text: "These tighter limits are recorded with your approval. The effect still runs " +
          "through the kernel's gate — the Shell cannot widen authority, only narrow intent."
      }));
      modalBody.appendChild(el("label", { class: "field-label", text: "Max cost" }));
      modalBody.appendChild(maxCost);
      modalBody.appendChild(el("label", { class: "field-label", text: "Expiry (logical)" }));
      modalBody.appendChild(expiry);
      modalBody.appendChild(el("div", { class: "modal-actions" }, [
        el("button", { type: "button", class: "btn", text: "Cancel", on: { click: close } }),
        el("button", {
          type: "button", class: "btn btn-primary", text: "Continue to reauth",
          on: { click: function () {
            var limits = {};
            if (maxCost.value !== "") { limits.max_cost = Number(maxCost.value); }
            if (expiry.value !== "") { limits.expiry = Number(expiry.value); }
            close();
            approve(approval, ctx, limits);
          } }
        })
      ]));
    });
  }

  function reasonOf(r) {
    return (r.data && (r.data.reason_code || r.data.error)) || r.status;
  }

  // -- trusted reauth modal (never stores the secret) --------------------
  function reauthPrompt(effectName) {
    return new Promise(function (resolve) {
      openModal("Reauthenticate to approve", function (modalBody, close) {
        var input = el("input", {
          type: "password", class: "input", id: "reauth-secret",
          placeholder: "pairing secret", autocomplete: "off"
        });
        modalBody.appendChild(el("p", {
          text: "Approving \"" + effectName + "\" clears a kernel gate and requires a fresh " +
            "reauth. Re-enter the local pairing secret for this one action."
        }));
        modalBody.appendChild(input);
        modalBody.appendChild(el("div", { class: "modal-actions" }, [
          el("button", {
            type: "button", class: "btn",
            text: "Cancel", on: { click: function () { close(); resolve(null); } }
          }),
          el("button", {
            type: "button", class: "btn btn-primary", text: "Approve",
            on: { click: function () {
              var v = input.value;
              close();
              resolve(v || null);
            } }
          })
        ]));
        setTimeout(function () { input.focus(); }, 0);
      });
    });
  }

  function openModal(title, build) {
    var host = document.getElementById("modal-host");
    D.dom.clear(host);
    var body = el("div", { class: "modal-body" });
    var close = function () {
      D.dom.clear(host);
      host.hidden = true;
    };
    var modal = el("div", { class: "modal", role: "dialog", "aria-modal": "true" }, [
      el("div", { class: "modal-title", text: title }),
      body
    ]);
    var backdrop = el("div", { class: "modal-backdrop" }, [modal]);
    host.appendChild(backdrop);
    host.hidden = false;
    build(body, close);
  }

  D.registerScreen({
    id: "approvals",
    title: "Approval inbox",
    icon: "🛡️",
    endpoints: [
      "GET /api/v1/approvals", "POST /api/v1/approvals/deny", "POST /api/v1/approvals/approve"
    ],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.approvals, function (approvals) {
        var pending = approvals.filter(function (a) { return a.state === "pending"; });
        var decided = approvals.filter(function (a) { return a.state !== "pending"; });
        container.appendChild(el("div", { class: "trusted-banner" }, [
          el("span", { class: "trusted-mark", text: "TRUSTED" }),
          el("span", { text: " Decisions here are made by you and recorded to the Weft. " +
            "Action buttons appear only in this panel." })
        ]));
        container.appendChild(ui.sectionTitle("Pending", pending.length + " awaiting decision"));
        if (!pending.length) {
          container.appendChild(D.dom.empty("No pending approvals."));
        }
        pending.forEach(function (a) { container.appendChild(pendingCard(a, ctx)); });
        if (decided.length) {
          container.appendChild(ui.sectionTitle("Decided", decided.length + ""));
          decided.forEach(function (a) { container.appendChild(decidedCard(a)); });
        }
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
