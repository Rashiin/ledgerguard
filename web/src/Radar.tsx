import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type Alert,
  type FinancingMatch,
  type ProjectedWeek,
  type ScenarioResult,
} from "./api";
import { Badge, Card, Rows, Spinner, eur, plot } from "./ui";

const COLORS = { contracted: "#0f172a", expected: "#2563eb", all: "#94a3b8", after: "#b91c1c" };

function axis(v: number): string {
  return `${v < 0 ? "-" : ""}€${Math.round(Math.abs(v) / 1000)}k`;
}

export function Radar() {
  const [weeks, setWeeks] = useState<ProjectedWeek[] | null>(null);
  const [cash, setCash] = useState<string>("");
  const [asOf, setAsOf] = useState<string>("");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.projection(), api.alerts()]).then(
      ([p, a]) => {
        setWeeks(p.weeks);
        setCash(p.cash_on_hand);
        setAsOf(p.as_of);
        setAlerts(a.alerts);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  if (error) return <p className="text-sm text-block">{error}</p>;
  if (!weeks) return <Spinner />;

  const low = weeks.reduce((a, w) => (plot(w.closing_contracted) < plot(a.closing_contracted) ? w : a));
  const data = weeks.map((w) => ({
    week: `W${w.week}`,
    contracted: plot(w.closing_contracted),
    expected: plot(w.closing_with_expected),
    all: plot(w.closing),
    raw: w,
  }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Cash on hand" value={eur(cash)} note={`ledger as of ${asOf}`} />
        <Stat
          label="Lowest week, contracted lines"
          value={eur(low.closing_contracted)}
          note={`week ${low.week}, ending ${low.ends_on}`}
          tone={plot(low.closing_contracted) < 0 ? "block" : undefined}
        />
        <Stat label="Week 13, all lines" value={eur(weeks[weeks.length - 1].closing)} note="contracted, expected and modelled" />
        <Stat label="High alerts" value={String(alerts.filter((a) => a.severity === "high" || a.severity === "critical").length)} note={`${alerts.length} alerts in all`} />
      </div>

      <Card title="13-week projection by confidence class">
        <p className="mb-3 text-xs text-slate-500">
          Contracted lines are issued invoices and signed orders. Expected adds draft orders at run
          rate. All adds the modelled seasonal fill. The three are never merged into one number.
        </p>
        <div className="h-72">
          <ResponsiveContainer>
            <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="week" tick={{ fontSize: 11 }} />
              <YAxis tickFormatter={axis} tick={{ fontSize: 11 }} width={56} />
              <Tooltip
                formatter={(_v, name, item) => {
                  const w = (item.payload as { raw: ProjectedWeek }).raw;
                  const value = name === "Contracted" ? w.closing_contracted : name === "With expected" ? w.closing_with_expected : w.closing;
                  return [eur(value), name];
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <ReferenceLine y={0} stroke="#b91c1c" strokeDasharray="4 4" />
              <Line isAnimationActive={false} dataKey="contracted" name="Contracted" stroke={COLORS.contracted} strokeWidth={2} dot={false} />
              <Line isAnimationActive={false} dataKey="expected" name="With expected" stroke={COLORS.expected} strokeWidth={2} dot={false} />
              <Line isAnimationActive={false} dataKey="all" name="All lines" stroke={COLORS.all} strokeWidth={2} strokeDasharray="5 3" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card title="Alerts">
        <ul className="space-y-2">
          {alerts.map((a) => (
            <li key={a.id} className="flex items-start gap-3 border-b border-slate-50 pb-2 text-sm">
              <span className="w-20 shrink-0">
                <Badge tone={a.severity === "high" || a.severity === "critical" ? "block" : a.severity === "medium" ? "warn" : "slate"}>
                  {a.severity}
                </Badge>
              </span>
              <div className="min-w-0 flex-1">
                <div>{a.headline}</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span>{a.kind}</span>
                  <span>{a.lead_days > 0 ? `${a.lead_days} days ahead` : "now"}</span>
                  {a.classes.length > 0 && <span>driven by {a.classes.join(", ").toLowerCase()}</span>}
                  <Rows ids={a.rows} />
                </div>
              </div>
            </li>
          ))}
        </ul>
      </Card>

      <ScenarioPanel />
      <FinancingPanel />
    </div>
  );
}

function Stat({ label, value, note, tone }: { label: string; value: string; note: string; tone?: "block" }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${tone === "block" ? "text-block" : ""}`}>{value}</div>
      <div className="text-xs text-slate-500">{note}</div>
    </div>
  );
}

// ------------------------------------------------------------------ scenarios

type ScenarioType = "customer_pays_late" | "delay_payable" | "draw_credit_line" | "contracted_inflows_only";

function ScenarioPanel() {
  const [type, setType] = useState<ScenarioType>("customer_pays_late");
  const [counterparty, setCounterparty] = useState("cp_brennan");
  const [invoice, setInvoice] = useState("inv_0665");
  const [days, setDays] = useState("30");
  const [amount, setAmount] = useState("50000.00");
  const [on, setOn] = useState("2026-11-20");
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function scenario(): Record<string, unknown> {
    if (type === "customer_pays_late") return { type, counterparty_id: counterparty, days: Number(days) };
    if (type === "delay_payable") return { type, invoice_id: invoice, days: Number(days) };
    if (type === "draw_credit_line") return { type, amount, on };
    return { type };
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.scenario(scenario()));
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const field = "rounded border border-slate-200 px-2 py-1 text-sm";
  const data = result?.before.map((b, i) => ({
    week: `W${b.week}`,
    before: plot(b.closing),
    after: plot(result.after[i].closing),
    rawBefore: b.closing,
    rawAfter: result.after[i].closing,
  }));

  return (
    <Card title="What-if scenario">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-xs text-slate-500">
          Scenario
          <select className={field} value={type} onChange={(e) => setType(e.target.value as ScenarioType)}>
            <option value="customer_pays_late">A customer pays late</option>
            <option value="delay_payable">Pay a supplier invoice later</option>
            <option value="draw_credit_line">Draw on the credit line</option>
            <option value="contracted_inflows_only">Stress test: contracted receipts only</option>
          </select>
        </label>
        {type === "customer_pays_late" && (
          <label className="flex flex-col text-xs text-slate-500">
            Customer id
            <input className={`${field} w-36 font-mono`} value={counterparty} onChange={(e) => setCounterparty(e.target.value)} />
          </label>
        )}
        {type === "delay_payable" && (
          <label className="flex flex-col text-xs text-slate-500">
            Supplier invoice id
            <input className={`${field} w-36 font-mono`} value={invoice} onChange={(e) => setInvoice(e.target.value)} />
          </label>
        )}
        {(type === "customer_pays_late" || type === "delay_payable") && (
          <label className="flex flex-col text-xs text-slate-500">
            Days
            <input className={`${field} w-20`} value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} />
          </label>
        )}
        {type === "draw_credit_line" && (
          <>
            <label className="flex flex-col text-xs text-slate-500">
              Amount, EUR
              <input className={`${field} w-32`} value={amount} onChange={(e) => setAmount(e.target.value)} />
            </label>
            <label className="flex flex-col text-xs text-slate-500">
              On
              <input type="date" className={field} value={on} onChange={(e) => setOn(e.target.value)} />
            </label>
          </>
        )}
        <button type="button" onClick={run} disabled={busy} className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40">
          {busy ? <Spinner /> : "Run"}
        </button>
        <span className="text-xs text-slate-500">Runs on a copy of the projection. Nothing in the ledger changes.</span>
      </div>
      {error && <p className="mt-3 text-sm text-block">{error}</p>}
      {result && data && (
        <div className="mt-4">
          <p className="text-sm font-medium">{result.description}</p>
          <div className="mt-2 h-64">
            <ResponsiveContainer>
              <LineChart data={data} margin={{ left: 8, right: 8, top: 8 }}>
                <CartesianGrid stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="week" tick={{ fontSize: 11 }} />
                <YAxis tickFormatter={axis} tick={{ fontSize: 11 }} width={56} />
                <Tooltip
                  formatter={(_v, name, item) => {
                    const p = item.payload as { rawBefore: string; rawAfter: string };
                    return [eur(name === "Before" ? p.rawBefore : p.rawAfter), name];
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <ReferenceLine y={0} stroke="#b91c1c" strokeDasharray="4 4" />
                <Line isAnimationActive={false} dataKey="before" name="Before" stroke={COLORS.all} strokeWidth={2} dot={false} />
                <Line isAnimationActive={false} dataKey="after" name="After" stroke={COLORS.after} strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <AlertDelta title="New alerts" alerts={result.new_alerts} tone="block" />
            <AlertDelta title="Cleared alerts" alerts={result.cleared_alerts} tone="ok" />
          </div>
        </div>
      )}
    </Card>
  );
}

function AlertDelta({ title, alerts, tone }: { title: string; alerts: Alert[]; tone: "block" | "ok" }) {
  return (
    <div>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</div>
      {alerts.length === 0 && <p className="text-xs text-slate-500">None</p>}
      {alerts.map((a) => (
        <div key={a.id} className="mb-1 text-xs">
          <Badge tone={tone}>{a.severity}</Badge> {a.headline}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ financing

const FACTORS = [
  ["cost", "Total cost"],
  ["coverage", "Covers the gap"],
  ["speed", "Funded in time"],
  ["flexibility", "Flexibility"],
  ["collateral", "No collateral"],
] as const;

function FinancingPanel() {
  const [weights, setWeights] = useState<Record<string, string>>({
    cost: "40",
    coverage: "20",
    speed: "15",
    flexibility: "15",
    collateral: "10",
  });
  const [match, setMatch] = useState<FinancingMatch | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      api.financing(weights).then(
        (m) => {
          setMatch(m);
          setError(null);
        },
        (e: Error) => setError(e.message),
      );
    }, 200);
    return () => clearTimeout(t);
  }, [weights]);

  return (
    <Card title="Financing options for the projected gap" right={<Badge tone="warn">synthetic catalogue</Badge>}>
      {match?.gap && (
        <p className="mb-3 text-sm">
          The gap to fund is <span className="font-semibold">{eur(match.gap.amount)}</span>, from {match.gap.need_by} to{" "}
          {match.gap.until}, {match.gap.days} days. Funding has to arrive within {match.gap.lead_days} days.
        </p>
      )}
      <div className="mb-4 grid gap-3 sm:grid-cols-5">
        {FACTORS.map(([key, label]) => (
          <label key={key} className="text-xs text-slate-500">
            {label}: <span className="font-medium text-slate-800">{weights[key]}</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={weights[key]}
              onChange={(e) => setWeights({ ...weights, [key]: e.target.value })}
              className="w-full accent-accent"
            />
          </label>
        ))}
      </div>
      {error && <p className="text-sm text-block">{error}</p>}
      {match && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
                <th className="py-1 pr-2">Score</th>
                <th className="py-1 pr-2">Option</th>
                <th className="py-1 pr-2 text-right">Amount</th>
                <th className="py-1 pr-2 text-right">Total cost</th>
                <th className="py-1">Notes</th>
              </tr>
            </thead>
            <tbody>
              {match.options.map((o) => (
                <tr key={o.product_id} className={`border-b border-slate-50 align-top ${o.eligible ? "" : "text-slate-400"}`}>
                  <td className="py-1.5 pr-2 font-mono">{o.score ?? "out"}</td>
                  <td className="py-1.5 pr-2">
                    <div>{o.provider}, {o.name}</div>
                    <div className="mt-0.5"><Rows ids={[o.product_id]} /></div>
                  </td>
                  <td className="py-1.5 pr-2 text-right">{o.eligible ? eur(o.amount) : ""}</td>
                  <td className="py-1.5 pr-2 text-right font-medium">{o.eligible ? eur(o.total_cost) : ""}</td>
                  <td className="py-1.5 text-xs text-slate-600">
                    {o.eligible ? (
                      <>
                        <div>
                          {o.days} days drawn, funded in {o.funded_in_days} days{" "}
                          {o.in_time ? <Badge tone="ok">in time</Badge> : <Badge tone="warn">too slow</Badge>}
                        </div>
                        {o.full_term_months && <div>Costed over the full {o.full_term_months}-month term.</div>}
                        <div className="text-slate-400">
                          {o.breakdown.map((b) => `${b.item} ${eur(b.amount)}`).join(", ")}
                        </div>
                      </>
                    ) : (
                      o.reasons.join("; ")
                    )}
                  </td>
                </tr>
              ))}
              <tr className="bg-slate-50 align-top">
                <td className="py-1.5 pr-2 font-mono">base</td>
                <td className="py-1.5 pr-2" colSpan={4}>
                  <span className="font-medium">Do nothing, {eur(match.do_nothing.total_cost)}. </span>
                  {match.do_nothing.consequence}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-slate-500">
        Informational only: this is not regulated financial advice. Products here are synthetic. Accepting
        an offer is a T3 action that needs approval and a typed confirmation.
      </p>
    </Card>
  );
}
