"use strict";
/*
 * Q&A — source-grounded question answering over the operator's imported knowledge.
 *
 * OWNER: qa lane (Path A). This file is the qa lane's frontend surface; no other lane
 * edits it, and the qa lane edits no shared frontend file (app.js/api.js/dom.js/
 * sanitize.js/index.html are pre-wired).
 *
 * TODO(lane-qa): replace the stub panel with the real screen — ask a question via
 * ctx.api.commands.askQuestion, list runs via ctx.api.reads.questions, show a run's
 * answer + citations via ctx.api.reads.questionDetail. Answer text and citation
 * snippets are UNTRUSTED model/source content: render as text only (dom.el `text`),
 * never as markup, and make each citation resolve to its source segment id.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  D.registerScreen({
    id: "qa",
    title: "Q&A",
    icon: "❓",
    endpoints: [
      "GET /api/v1/questions", "GET /api/v1/questions/detail",
      "POST /api/v1/questions/ask"
    ],
    render: function (container) {
      container.appendChild(ui.sectionTitle("Grounded Q&A"));
      container.appendChild(ui.card([
        el("p", { text: "Not yet implemented." }),
        el("p", {
          class: "muted",
          text: "This is the reserved surface for source-grounded question answering " +
            "with resolving citations (qa lane). The backend contract is frozen; " +
            "asking a question currently returns NOT_IMPLEMENTED (501)."
        })
      ]));
    }
  });
})(typeof window !== "undefined" ? window : this);
