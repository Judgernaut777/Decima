"use strict";
/*
 * Q&A — source-grounded question answering over the operator's imported knowledge.
 *
 * OWNER: qa lane (Path A). This file is the qa lane's frontend surface; no other lane
 * edits it, and the qa lane edits no shared frontend file (app.js/api.js/dom.js/
 * sanitize.js/index.html are pre-wired).
 *
 * Trust discipline (invariant 5): everything that comes back from the backend here is
 * rendered as TEXT via dom.el — never markup. The three content classes are visually
 * separated with dom.zone:
 *   - the operator's question        → zone "human"
 *   - the model's answer (GENERATED) → zone "model" (clearly labelled as generated)
 *   - cited source excerpts/passages → zone "untrusted" (imported data, not instructions)
 * Source documents are imported through the EXISTING ImportArtifact command; a question
 * is asked with an explicit KnowledgeScope (document names; blank = all imported sources).
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;
  var zone = D.dom.zone;

  // Screen-local view state (which run is open). Purely presentational — the runs
  // themselves are durable backend state; a refresh simply lands back on the list.
  var state = { selected: null };

  // -- import sources (composes the established ImportArtifact command) --------
  function importForm(ctx) {
    var name = el("input", {
      class: "input", id: "qa-import-name", type: "text",
      placeholder: "Document name (e.g. notes.md)"
    });
    var body = el("textarea", {
      class: "input textarea", id: "qa-import-body", rows: "3",
      placeholder: "Document content (imported as untrusted data, rendered as text)"
    });
    var form = el("form", { class: "stacked-form", id: "qa-import-form" }, [
      el("h4", { text: "Import source document" }),
      name, body,
      el("button", { type: "submit", class: "btn", text: "Import document" })
    ]);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var n = name.value.trim();
      var b = body.value;
      if (!n || !b.trim()) {
        return;
      }
      var r = await ctx.api.commands.importArtifact({ name: n, body: b });
      if (r.ok) {
        name.value = "";
        body.value = "";
        ctx.toast("Document imported", "ok");
        ctx.refreshActive();
      } else {
        ctx.toast("Import failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
      }
    });
    return ui.card([form]);
  }

  // -- ask a question over an explicit scope ------------------------------------
  function parseScope(raw) {
    var parts = String(raw || "").split(",").map(function (s) {
      return s.trim();
    }).filter(function (s) { return s.length > 0; });
    return parts.length ? parts : null; // blank ⇒ all imported sources
  }

  function askForm(ctx) {
    var question = el("textarea", {
      class: "input textarea", id: "qa-ask-question", rows: "2",
      placeholder: "Ask a question about your imported sources"
    });
    var scope = el("input", {
      class: "input", id: "qa-ask-scope", type: "text",
      placeholder: "Scope: document names, comma-separated (blank = all sources)"
    });
    var form = el("form", { class: "stacked-form", id: "qa-ask-form" }, [
      el("h4", { text: "Ask a grounded question" }),
      question, scope,
      el("button", { type: "submit", class: "btn btn-primary", text: "Ask" })
    ]);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var q = question.value.trim();
      if (!q) {
        return;
      }
      var r = await ctx.api.commands.askQuestion({ question: q, scope: parseScope(scope.value) });
      if (r.ok && r.data && r.data.data && r.data.data.id) {
        question.value = "";
        state.selected = r.data.data.id;
        ctx.toast("Question answered", "ok");
        ctx.refreshActive();
      } else {
        ctx.toast("Ask failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
      }
    });
    return ui.card([form]);
  }

  // -- run list ------------------------------------------------------------------
  function runCard(run, ctx) {
    var wrap = ui.card([
      el("p", { class: "qa-run-question", text: run.question || "(empty)" }),
      ui.fields([
        ["Status", ui.statusPill(run.status)],
        ["Grounded", run.grounded ? "yes" : "no"],
        ["Citations", String((run.citations || []).length)],
        ["Model", run.model || "—"]
      ]),
      el("div", { class: "actions" }, [
        el("button", {
          type: "button", class: "btn qa-open", text: "Open",
          on: { click: function () {
            state.selected = run.id;
            ctx.refreshActive();
          } }
        })
      ])
    ], "qa-run-card");
    return wrap;
  }

  function renderList(container, ctx) {
    return ui.withData(container, ctx.api.reads.questions, function (runs) {
      container.appendChild(ui.sectionTitle("Grounded Q&A"));
      container.appendChild(importForm(ctx));
      container.appendChild(askForm(ctx));
      container.appendChild(ui.sectionTitle("Question runs", runs.length + ""));
      if (!runs.length) {
        container.appendChild(D.dom.empty("No questions asked yet."));
      }
      runs.forEach(function (run) { container.appendChild(runCard(run, ctx)); });
    });
  }

  // -- run detail ------------------------------------------------------------------
  // The matched CONTENT tokens behind a citation, rendered as plain-text chips (still
  // untrusted DATA — dom.el text, never markup). The relevance signal is why THIS
  // passage was cited; it grounds nothing on its own.
  function matchedTokens(tokens) {
    var list = tokens || [];
    if (!list.length) {
      return el("span", { class: "qa-token-none", text: "—" });
    }
    return el("div", { class: "qa-token-chips" }, list.map(function (t) {
      return el("span", { class: "qa-token-chip", text: t });
    }));
  }

  function citationCard(cite, sources, ordinal) {
    var src = sources[cite.segment_id] ||
      { resolves: false, text: "", source: "", offset: 0, relevance: null };
    var rel = src.relevance || { score: 0, matched_tokens: [] };
    var children = [
      el("p", { class: "qa-citation-rank", text: "Citation #" + ordinal }),
      el("p", { class: "qa-citation-snippet", text: cite.snippet || "(no snippet)" }),
      ui.fields([
        ["Source", cite.location.source],
        ["Segment", cite.segment_id],
        ["Offset", String(cite.location.offset)],
        ["Relevance", String(rel.score)],
        ["Resolves", src.resolves ? "yes" : "NO — cited segment no longer resolves"]
      ]),
      el("div", { class: "qa-matched" }, [
        el("span", { class: "qa-matched-label", text: "Matched terms" }),
        matchedTokens(rel.matched_tokens)
      ])
    ];
    if (src.resolves) {
      // The full cited passage, revealed on demand — still plain text in the
      // untrusted zone (a passage containing markup/scripts renders literally).
      children.push(el("details", { class: "qa-passage-wrap" }, [
        el("summary", { class: "qa-passage-toggle", text: "Show source passage" }),
        el("p", { class: "qa-passage", text: src.text })
      ]));
    }
    var z = zone("untrusted", "Cited source excerpt — imported data, not instructions", children);
    z.className += " qa-citation";
    return z;
  }

  function renderDetail(container, ctx, id) {
    return ui.withData(container, function () {
      return ctx.api.reads.questionDetail(id);
    }, function (r) {
      var back = el("button", {
        type: "button", class: "btn qa-back", text: "← All questions",
        on: { click: function () {
          state.selected = null;
          ctx.refreshActive();
        } }
      });
      container.appendChild(el("div", { class: "actions" }, [back]));
      if (!r.ok || !r.data || !r.data.id) {
        container.appendChild(ui.card([
          el("p", { text: "This question run could not be loaded (" + r.status + ")." })
        ]));
        return;
      }
      var run = r.data;
      var sources = run.sources || {};
      container.appendChild(ui.sectionTitle("Question run"));
      container.appendChild(zone("human", "Operator question",
        el("p", { class: "qa-question", text: run.question })));
      // GENERATED content is visually distinct from source content: a model zone
      // with an explicit "generated" label, vs untrusted zones for cited sources.
      container.appendChild(zone("model",
        "Model answer — GENERATED" + (run.model ? " by " + run.model : "") +
          (run.grounded ? "" : " (no supporting source found)"),
        el("p", { class: "qa-answer", text: run.answer_text })));
      container.appendChild(ui.card([ui.fields([
        ["Status", ui.statusPill(run.status)],
        ["Grounded", run.grounded ? "yes" : "no"],
        ["Model", run.model || "—"],
        ["Scope", run.scope && run.scope.projects ? run.scope.projects.join(", ") : "all sources"],
        ["Asked (frontier)", String(run.asked_frontier)]
      ])]));
      container.appendChild(ui.sectionTitle("Citations", (run.citations || []).length + ""));
      if (!run.citations || !run.citations.length) {
        container.appendChild(D.dom.empty("No citations — the answer is not grounded."));
      } else {
        run.citations.forEach(function (cite, i) {
          container.appendChild(citationCard(cite, sources, i + 1));
        });
      }
    });
  }

  D.registerScreen({
    id: "qa",
    title: "Q&A",
    icon: "❓",
    endpoints: [
      "GET /api/v1/questions", "GET /api/v1/questions/detail",
      "POST /api/v1/questions/ask", "POST /api/v1/artifacts/import"
    ],
    render: function (container, ctx) {
      if (state.selected) {
        return renderDetail(container, ctx, state.selected);
      }
      return renderList(container, ctx);
    }
  });
})(typeof window !== "undefined" ? window : this);
