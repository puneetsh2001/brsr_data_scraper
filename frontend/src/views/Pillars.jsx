import { api } from "../lib/api.js";
import { fy, unitLabel } from "../lib/format.js";
import { useAsync } from "../lib/useAsync.js";
import { AsyncView, Empty, SectionTitle, Spacer } from "../components/common.jsx";
import { BarChart } from "../components/BarChart.jsx";
import { GenderGrid, MetricCard, MetricTable, MissingTable } from "../components/metrics.jsx";

const themesOf = (state, id) => state.catalog.get(id)?.themes || [];

// ---------------------------------------------------------------- environmental
const ENV_THEMES = [["energy", "Energy"], ["emissions", "GHG emissions"], ["water", "Water"], ["waste", "Waste"]];

// energy mix chart: only when every bar is a normalised value in one unit
function EnergyChart({ groups }) {
  const items = groups.filter((g) => g.expected_unit === "energy")
    .map((g) => ({ label: g.metric_name, v: g.values.find((x) => !x.breakdown) || g.values[0] }));
  if (!items.length) return null;
  const units = new Set(items.filter((i) => i.v.value_numeric !== null).map((i) => i.v.unit));
  const allNormalised = items.every((i) => i.v.value_numeric !== null);
  if (units.size !== 1 || !allNormalised) {
    return (
      <p className="note">Energy chart not shown: one or more energy values are not normalised to a common unit
        {" "}(see the table). Values are never combined across different or unknown units.</p>
    );
  }
  const unit = [...units][0];
  return (
    <div>
      <div className="small muted">{`Energy consumption by source (${unitLabel(unit)})`}</div>
      <BarChart title="Energy by source" unit={unitLabel(unit)}
        items={items.map((i) => ({ label: i.label, value: i.v.value_numeric, text: i.v.value,
          calc: i.v.value_basis.startsWith("CALCULATED") }))} />
    </div>
  );
}

export function Environmental({ state }) {
  const { company, year } = state;
  const id = company.company.company_id;
  const data = useAsync(() => api.pillar(id, year.fiscal_year, "environmental"), `${id}|${year.fiscal_year}`);
  return (
    <>
      <SectionTitle title={`Environmental · ${fy(year.fiscal_year)}`} cls="env" />
      <div>
        <AsyncView state={data}>{(d) => {
          const used = new Set();
          const panels = ENV_THEMES.map(([theme, title]) => {
            const groups = d.metrics.filter((g) => g.themes.includes(theme));
            groups.forEach((g) => used.add(g.requirement_id));
            const miss = d.missing.filter((m) => themesOf(state, m.requirement_id)[0] === theme);
            miss.forEach((m) => used.add(m.requirement_id));
            return (
              <div key={theme}>
                <section className="panel">
                  <h2>{title}</h2>
                  {groups.length ? <>{theme === "energy" ? <EnergyChart groups={groups} /> : null}<MetricTable groups={groups} /></> : null}
                  {miss.length ? <><h3>Not available</h3><MissingTable missing={miss} /></> : null}
                  {!groups.length && !miss.length ? <Empty>No metrics in this group.</Empty> : null}
                </section>
                <Spacer />
              </div>
            );
          });
          const rest = d.metrics.filter((g) => !used.has(g.requirement_id));
          const restMiss = d.missing.filter((m) => !used.has(m.requirement_id));
          return (
            <>
              {panels}
              {rest.length || restMiss.length ? (
                <section className="panel">
                  <h2>Other environmental metrics</h2>
                  {rest.length ? <MetricTable groups={rest} /> : null}
                  <MissingTable missing={restMiss} />
                </section>
              ) : null}
            </>
          );
        }}</AsyncView>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- social
export function Social({ state }) {
  const { company, year } = state;
  const id = company.company.company_id;
  const key = `${id}|${year.fiscal_year}`;
  const workforce = useAsync(() => api.workforce(id, year.fiscal_year), key);
  const posh = useAsync(() => api.posh(id, year.fiscal_year), key);
  const rest = useAsync(() => api.pillar(id, year.fiscal_year, "social"), key);
  return (
    <>
      <SectionTitle title={`Social · ${fy(year.fiscal_year)}`} cls="soc" />
      <h3 className="muted">Workforce</h3>
      <div>
        <AsyncView state={workforce}>{(d) => (
          <>
            {d.metrics.map((g) => (
              <div key={g.requirement_id}>
                <section className="panel">
                  <h2>{g.metric_name}</h2>
                  {g.gender_grid && g.gender_grid.length ? <GenderGrid group={g} /> : <MetricTable groups={[g]} />}
                </section>
                <Spacer />
              </div>
            ))}
            {d.missing.length ? (
              <section className="panel"><h2>Workforce metrics not available</h2><MissingTable missing={d.missing} /></section>
            ) : null}
          </>
        )}</AsyncView>
      </div>
      <SectionTitle title="POSH (prevention of sexual harassment)" cls="soc" />
      <div>
        <AsyncView state={posh}>{(d) => (
          <section className="panel">
            {d.metrics.length ? <MetricTable groups={d.metrics} showBreakdown={false} /> : null}
            <MissingTable missing={d.missing} />
            <p className="note">BRSR XBRL reports complaints filed, pending at year end, reported and upheld.
              {" "}A &apos;resolved&apos; count is not reported in the filing and is not derived here.</p>
          </section>
        )}</AsyncView>
      </div>
      <SectionTitle title="Other social metrics" cls="soc" />
      <div>
        <AsyncView state={rest}>{(d) => {
          const groups = d.metrics.filter((g) => !g.themes.some((t) => t === "workforce" || t === "posh"));
          const miss = d.missing.filter((m) => {
            const t = themesOf(state, m.requirement_id);
            return !t.includes("workforce") && !t.includes("posh");
          });
          return (
            <section className="panel">
              {groups.length ? <MetricTable groups={groups} /> : <Empty>No other social metrics.</Empty>}
              {miss.length ? <><h3>Not available</h3><MissingTable missing={miss} /></> : null}
            </section>
          );
        }}</AsyncView>
      </div>
    </>
  );
}

// ---------------------------------------------------------------- governance
export function Governance({ state }) {
  const { company, year } = state;
  const id = company.company.company_id;
  const data = useAsync(() => api.pillar(id, year.fiscal_year, "governance"), `${id}|${year.fiscal_year}`);
  return (
    <>
      <SectionTitle title={`Governance · ${fy(year.fiscal_year)}`} cls="gov" />
      <div>
        <AsyncView state={data}>{(d) => {
          const board = d.metrics.filter((g) => g.themes.includes("board"));
          const others = d.metrics.filter((g) => !g.themes.includes("board"));
          const boardMiss = d.missing.filter((m) => themesOf(state, m.requirement_id).includes("board"));
          const otherMiss = d.missing.filter((m) => !themesOf(state, m.requirement_id).includes("board"));
          return (
            <>
              <section className="panel">
                <h2>Board composition</h2>
                {board.length ? <div className="cards">{board.map((g) => <MetricCard key={g.requirement_id} group={g} />)}</div>
                  : <Empty>Board composition not available.</Empty>}
                <MissingTable missing={boardMiss} />
              </section>
              <Spacer />
              <section className="panel">
                <h2>Ethics, complaints and other governance metrics</h2>
                {others.length ? <MetricTable groups={others} /> : <Empty>No other governance metrics.</Empty>}
                {otherMiss.length ? <><h3>Not available</h3><MissingTable missing={otherMiss} /></> : null}
              </section>
            </>
          );
        }}</AsyncView>
      </div>
    </>
  );
}
