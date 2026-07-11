"use strict";
/*
 * Knowledge — notes and imported content. Each note is rendered by TRUST: an item that is
 * not instruction-eligible or carries imported provenance is UNTRUSTED/imported content
 * (zone-untrusted, escaped as text). A note the operator authored is model/system content.
 * Text is NEVER rendered as markup, so an imported note that contains "<script>…" is shown
 * literally (invariant 5). Reads GET /api/v1/notes; creates/retracts via the note commands.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;
  var ui = D.ui;
  var zone = D.dom.zone;

  function isUntrusted(note) {
    if (note.instruction_eligible === false) {
      return true;
    }
    var prov = note.provenance || [];
    return prov.some(function (p) {
      return String(p).toLowerCase().indexOf("import") >= 0 ||
        String(p).toLowerCase().indexOf("external") >= 0;
    });
  }

  function noteBody(note) {
    return [
      el("p", { class: "note-text", text: note.text || "(empty)" }),
      ui.fields([
        ["Id", note.id],
        ["Type", note.type],
        ["Trust", note.trust],
        ["Instruction-eligible", note.instruction_eligible ? "yes" : "no"],
        ["Provenance", (note.provenance || []).join(", ")]
      ]),
      el("div", { class: "actions" }, [
        el("button", {
          type: "button", class: "btn btn-danger", text: "Retract",
          on: { click: async function () {
            var r = await D.api.commands.retractNote({ id: note.id });
            if (r.ok) {
              D.app.toast("Note retracted", "ok");
              D.app.ctx.refreshActive();
            } else {
              D.app.toast("Retract failed", "bad");
            }
          } }
        })
      ])
    ];
  }

  function noteCard(note) {
    if (isUntrusted(note)) {
      return zone("untrusted", "Imported / untrusted content — not instructions", noteBody(note));
    }
    return zone("model", "Note", noteBody(note));
  }

  function createForm(ctx) {
    var input = el("textarea", {
      class: "input textarea", id: "new-note-text",
      placeholder: "New note text (rendered as plain text)", rows: "2"
    });
    var eligible = el("input", { type: "checkbox", id: "new-note-eligible" });
    var form = el("form", { class: "stacked-form" }, [
      input,
      el("label", { class: "checkline" }, [eligible, el("span", { text: " instruction-eligible" })]),
      el("button", { type: "submit", class: "btn btn-primary", text: "Add note" })
    ]);
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var textVal = input.value.trim();
      if (!textVal) {
        return;
      }
      var r = await ctx.api.commands.createNote({
        text: textVal, instruction_eligible: eligible.checked
      });
      if (r.ok) {
        input.value = "";
        eligible.checked = false;
        ctx.toast("Note added", "ok");
        ctx.refreshActive();
      } else {
        ctx.toast("Add failed: " + ((r.data && r.data.reason_code) || r.status), "bad");
      }
    });
    return form;
  }

  D.registerScreen({
    id: "knowledge",
    title: "Knowledge",
    icon: "📚",
    endpoints: ["GET /api/v1/notes", "POST /api/v1/notes", "POST /api/v1/notes/retract"],
    render: function (container, ctx) {
      return ui.withData(container, ctx.api.reads.notes, function (notes) {
        container.appendChild(createForm(ctx));
        container.appendChild(ui.sectionTitle("Notes", notes.length + ""));
        if (!notes.length) {
          container.appendChild(D.dom.empty("No notes yet."));
        }
        notes.forEach(function (n) { container.appendChild(noteCard(n)); });
      });
    }
  });
})(typeof window !== "undefined" ? window : this);
