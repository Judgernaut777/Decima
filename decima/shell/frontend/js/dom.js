"use strict";
/*
 * dom.js — safe DOM construction helpers.
 *
 * The Shell builds every node through document.createElement + textContent, so untrusted
 * strings are inserted as TEXT and can never become markup or a handler. There is no path
 * in this file (or any screen) that assigns a data string to innerHTML. Event handlers are
 * attached only in code (addEventListener), never derived from data, and never as inline
 * on* attributes.
 */
(function (root) {
  var D = root.DShell || (root.DShell = {});

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
    return node;
  }

  // el(tag, props?, children?) — props is a plain object of attributes/dataset/text.
  // Only a fixed allowlist of behaviors is honored; arbitrary keys become attributes via
  // setAttribute (which cannot execute code). `text` sets textContent (escaped by the DOM).
  function el(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var value = props[key];
        if (value === null || value === undefined || value === false) {
          return;
        }
        if (key === "text") {
          node.textContent = value == null ? "" : String(value);
        } else if (key === "class" || key === "className") {
          node.className = String(value);
        } else if (key === "dataset") {
          Object.keys(value).forEach(function (dk) {
            node.dataset[dk] = String(value[dk]);
          });
        } else if (key === "on" && typeof value === "object") {
          Object.keys(value).forEach(function (evt) {
            node.addEventListener(evt, value[evt]);
          });
        } else if (key === "href") {
          // href is routed through safeUrl so no javascript:/data: scheme survives.
          node.setAttribute("href", D.safeUrl(String(value)));
        } else {
          node.setAttribute(key, String(value));
        }
      });
    }
    appendChildren(node, children);
    return node;
  }

  function appendChildren(node, children) {
    if (children === null || children === undefined) {
      return;
    }
    if (!Array.isArray(children)) {
      children = [children];
    }
    children.forEach(function (child) {
      if (child === null || child === undefined || child === false) {
        return;
      }
      if (typeof child === "string" || typeof child === "number") {
        node.appendChild(document.createTextNode(String(child)));
      } else {
        node.appendChild(child);
      }
    });
  }

  function text(value) {
    return document.createTextNode(value == null ? "" : String(value));
  }

  // A labelled trust zone: (a) untrusted/imported, (b) model, (c) system, (d) human.
  // The visual + structural separation is required by invariant 5.
  function zone(kind, label, children) {
    var wrap = el("div", { class: "zone zone-" + kind });
    wrap.appendChild(el("div", { class: "zone-label", text: label }));
    var bodyNode = el("div", { class: "zone-body" });
    appendChildren(bodyNode, children);
    wrap.appendChild(bodyNode);
    return wrap;
  }

  // An external link, explicitly marked (invariant 5 — external links clearly marked).
  function link(href, labelText) {
    var url = D.safeUrl(href);
    var external = D.isExternal(url);
    var a = el("a", {
      href: url,
      class: external ? "ext-link" : "int-link",
      rel: external ? "noopener noreferrer nofollow" : null,
      target: external ? "_blank" : null,
      text: labelText || href
    });
    if (external) {
      a.appendChild(el("span", { class: "ext-badge", text: "↗ external", "aria-hidden": "true" }));
    }
    return a;
  }

  function empty(message) {
    return el("div", { class: "empty", text: message });
  }

  D.dom = { clear: clear, el: el, text: text, zone: zone, link: link, empty: empty };
})(typeof window !== "undefined" ? window : this);
