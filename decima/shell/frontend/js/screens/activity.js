"use strict";
/*
 * Activity timeline — the trusted, append-only record of what happened, folded from the
 * Weft. Each entry is a SYSTEM fact (author, verb, target, who authorized it), rendered as
 * text. Reads GET /api/v1/activity.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;

  function entryRow(entry) {
    return el("li", { class: "timeline-entry" }, [
      el("span", { class: "tl-seq", text: "#" + entry.seq }),
      el("div", { class: "tl-main" }, [
        el("div", { class: "tl-line" }, [
          el("span", { class: "tl-author", text: entry.author || "system" }),
          el("span", { class: "tl-verb", text: " " + (entry.verb_word || entry.verb || "") + " " }),
          el("span", { class: "tl-desc", text: entry.description || "" })
        ]),
        el("div", { class: "tl-meta" }, [
          entry.cell_type ? ui.pill(entry.cell_type, "neutral") : null,
          entry.authorized_by ? el("span", { class: "tl-auth",
            text: "authorized by " + entry.authorized_by }) : null,
          entry.provenance ? el("span", { class: "tl-prov", text: "· " + entry.provenance }) : null
        ])
      ])
    ]);
  }

  D.registerScreen({
    id: "activity",
    title: "Activity timeline",
    icon: "📜",
    endpoints: ["GET /api/v1/activity"],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.activity, function (entries) {
        container.appendChild(ui.sectionTitle("Timeline", entries.length + " event(s)"));
        if (!entries.length) {
          container.appendChild(D.dom.empty("No activity recorded yet."));
          return;
        }
        var list = el("ul", { class: "timeline" });
        entries.slice().reverse().forEach(function (e) { list.appendChild(entryRow(e)); });
        container.appendChild(list);
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
