// Horizontal bar chart in plain SVG. Draws only the numeric bars it is
// given; categories without a comparable value are listed with a text
// label instead of a bar (never drawn as zero).

import { h, svg } from "./dom.js";
import { formatNumber } from "./format.js";

export function barChart(items, unit, { title } = {}) {
  // items: [{label, value (number|null), text, calc, note}]
  const width = 760, labelW = 190, valueW = 150, rowH = 30, pad = 10;
  const plotW = width - labelW - valueW - pad * 2;
  const nums = items.filter((i) => typeof i.value === "number");
  const max = Math.max(0, ...nums.map((i) => i.value));
  const min = Math.min(0, ...nums.map((i) => i.value));
  const span = max - min || 1;
  const x0 = labelW + pad + ((0 - min) / span) * plotW;
  const height = items.length * rowH + pad * 2;
  const rows = items.map((it, i) => {
    const y = pad + i * rowH;
    const label = svg("text", { x: labelW - 6, y: y + rowH / 2 + 4, "text-anchor": "end",
      text: it.label.length > 30 ? it.label.slice(0, 29) + "…" : it.label });
    const parts = [svg("title", { text: it.label }), label];
    if (typeof it.value === "number") {
      const x1 = labelW + pad + ((it.value - min) / span) * plotW;
      parts.push(svg("rect", { class: `bar${it.calc ? " calc" : ""}`, x: Math.min(x0, x1),
        y: y + 6, width: Math.max(1, Math.abs(x1 - x0)), height: rowH - 12, rx: 3 }));
      parts.push(svg("text", { x: labelW + pad + plotW + 8, y: y + rowH / 2 + 4,
        text: `${formatNumber(it.text) ?? it.value} ${unit || ""}`.trim() }));
    } else {
      parts.push(svg("text", { class: "gap-label", x: x0 + 6, y: y + rowH / 2 + 4,
        text: it.note || "not available" }));
    }
    return svg("g", {}, ...parts);
  });
  const axis = svg("line", { class: "axis", x1: x0, x2: x0, y1: pad, y2: height - pad });
  const el = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": title || "bar chart" }, axis, ...rows);
  return h("div", { class: "chart" }, el);
}
