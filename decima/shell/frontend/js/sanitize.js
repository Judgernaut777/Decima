"use strict";
/*
 * sanitize.js — the untrusted-content boundary of the Shell (invariant 5).
 *
 * Every string that originates from a model, an imported artifact, or any API payload is
 * DATA, never markup and never code. This module is the single choke point that turns
 * such a string into something safe to display:
 *
 *   - escapeHtml(value)  -> HTML-entity-escapes & < > " ' and backtick, so the string
 *                           can never introduce a tag, attribute, or handler even if it
 *                           is ever interpolated into markup. The Shell primarily renders
 *                           via textContent (see dom.js), but escapeHtml is the auditable
 *                           primitive and is unit-tested against hostile inputs.
 *   - safeUrl(value)     -> returns an http/https/mailto URL unchanged, else "#". This
 *                           blocks javascript:, data:, and vbscript: link payloads.
 *   - isExternal(value)  -> true for an http(s) URL, so the UI can mark it as leaving.
 *
 * The module loads in both the browser (defining window.DShell) and Node (module.exports)
 * so the escape function is testable outside a browser. It performs NO DOM work and NO
 * network I/O; it is a pure transform.
 */
(function (root) {
  var HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
    "`": "&#96;",
    "=": "&#61;",
    "/": "&#47;"
  };
  var HTML_RE = /[&<>"'`=/]/g;

  function escapeHtml(value) {
    if (value === null || value === undefined) {
      return "";
    }
    var s = typeof value === "string" ? value : String(value);
    return s.replace(HTML_RE, function (ch) {
      return HTML_ESCAPES[ch];
    });
  }

  var SAFE_SCHEME = /^(https?:|mailto:)/i;
  var UNSAFE_SCHEME = /^[a-z0-9.+-]*:/i;

  function safeUrl(value) {
    if (typeof value !== "string") {
      return "#";
    }
    var trimmed = value.trim();
    // Reject control chars that browsers strip when parsing a scheme.
    var normalized = trimmed.replace(/[ -\s]/g, "");
    if (SAFE_SCHEME.test(normalized)) {
      return trimmed;
    }
    // A relative URL (no scheme at all) is same-origin and allowed.
    if (!UNSAFE_SCHEME.test(normalized) && !normalized.startsWith("//")) {
      return trimmed;
    }
    return "#";
  }

  function isExternal(value) {
    return typeof value === "string" && /^https?:/i.test(value.trim());
  }

  var api = { escapeHtml: escapeHtml, safeUrl: safeUrl, isExternal: isExternal };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.DShell = Object.assign(root.DShell || {}, api);
})(typeof globalThis !== "undefined" ? globalThis : this);
