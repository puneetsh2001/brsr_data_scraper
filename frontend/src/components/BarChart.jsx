// Horizontal bar chart in plain SVG. Draws only the numeric bars it is
// given; categories without a comparable value are listed with a text label
// instead of a bar (never drawn as zero).
import { formatNumber } from "../lib/format.js";

export function BarChart({ items, unit, title }) {
  const width = 760, labelW = 190, valueW = 150, rowH = 30, pad = 10;
  const plotW = width - labelW - valueW - pad * 2;
  const nums = items.filter((i) => typeof i.value === "number");
  const max = Math.max(0, ...nums.map((i) => i.value));
  const min = Math.min(0, ...nums.map((i) => i.value));
  const span = max - min || 1;
  const x0 = labelW + pad + ((0 - min) / span) * plotW;
  const height = items.length * rowH + pad * 2;
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title || "bar chart"}>
        <line className="axis" x1={x0} x2={x0} y1={pad} y2={height - pad} />
        {items.map((it, i) => {
          const y = pad + i * rowH;
          const label = it.label.length > 30 ? it.label.slice(0, 29) + "…" : it.label;
          let body;
          if (typeof it.value === "number") {
            const x1 = labelW + pad + ((it.value - min) / span) * plotW;
            body = (
              <>
                <rect className={`bar${it.calc ? " calc" : ""}`} x={Math.min(x0, x1)} y={y + 6}
                  width={Math.max(1, Math.abs(x1 - x0))} height={rowH - 12} rx={3} />
                <text x={labelW + pad + plotW + 8} y={y + rowH / 2 + 4}>
                  {`${formatNumber(it.text) ?? it.value} ${unit || ""}`.trim()}
                </text>
              </>
            );
          } else {
            body = <text className="gap-label" x={x0 + 6} y={y + rowH / 2 + 4}>{it.note || "not available"}</text>;
          }
          return (
            <g key={`${it.label}-${i}`}>
              <title>{it.label}</title>
              <text x={labelW - 6} y={y + rowH / 2 + 4} textAnchor="end">{label}</text>
              {body}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
