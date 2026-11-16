// Minimal DOM helpers. Text is always set through textContent, never
// innerHTML, so API strings cannot inject markup.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function safeLink(url, text) {
  if (!url || !/^https?:\/\//i.test(url)) return h("span", { class: "muted" }, "—");
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text || url);
}

export function svg(tag, attrs, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null) continue;
    if (k === "text") el.textContent = v;
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) if (c) el.appendChild(c);
  return el;
}
