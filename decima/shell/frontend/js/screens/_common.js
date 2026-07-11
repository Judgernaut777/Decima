"use strict";
/*
 * screens/_common.js — small presentation helpers shared by the screen modules.
 * Everything renders untrusted values as text (via dom.el's `text`), never as markup.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});
  var el = D.dom.el;

  function card(children, extraClass) {
    return el("div", { class: "card" + (extraClass ? " " + extraClass : "") }, children);
  }

  // A definition list of label/value rows; values are always rendered as text.
  function fields(pairs) {
    var dl = el("dl", { class: "fields" });
    pairs.forEach(function (pair) {
      if (pair === null || pair === undefined) {
        return;
      }
      var label = pair[0];
      var value = pair[1];
      dl.appendChild(el("dt", { text: label }));
      var dd = el("dd", {});
      if (value && value.nodeType) {
        dd.appendChild(value);
      } else {
        dd.textContent = value === null || value === undefined || value === ""
          ? "—" : String(value);
      }
      dl.appendChild(dd);
    });
    return dl;
  }

  function pill(textValue, kind) {
    return el("span", { class: "pill pill-" + (kind || "neutral"), text: textValue });
  }

  function statusPill(status) {
    var kind = "neutral";
    var s = String(status || "").toLowerCase();
    if (s.indexOf("succeed") >= 0 || s === "active" || s === "approved" || s === "ready") {
      kind = "ok";
    } else if (s.indexOf("fail") >= 0 || s === "denied" || s === "expired" ||
               s === "terminated" || s === "blocked") {
      kind = "bad";
    } else if (s === "pending" || s === "paused" || s === "running") {
      kind = "warn";
    }
    return pill(status || "—", kind);
  }

  function sectionTitle(t, sub) {
    return el("div", { class: "section-title" }, [
      el("h3", { text: t }),
      sub ? el("span", { class: "section-sub", text: sub }) : null
    ]);
  }

  function loading() {
    return el("div", { class: "loading", text: "Loading…" });
  }

  async function withData(container, loader, renderer) {
    container.appendChild(loading());
    var data = await loader();
    D.dom.clear(container);
    renderer(data);
  }

  D.ui = {
    card: card,
    fields: fields,
    pill: pill,
    statusPill: statusPill,
    sectionTitle: sectionTitle,
    loading: loading,
    withData: withData
  };
})(typeof window !== "undefined" ? window : this);
