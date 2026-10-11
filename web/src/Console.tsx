import { useEffect, useRef, useState } from "react";
import { chat, type Claim, type Receipt, type Step, type Turn } from "./api";
import {
  Badge,
  Card,
  Marked,
  RowChip,
  Rows,
  Spinner,
  TierBadge,
  download,
  eur,
  ruleTone,
} from "./ui";

// The requests recorded for replay. In replay mode only these can be answered.
const SCENARIOS: { n: number; text: string }[] = [
  { n: 1, text: "How is our cash looking for the next quarter?" },
  { n: 2, text: "Why is week 7 so bad?" },
  { n: 3, text: "What if Brennan Hospitality pays 30 days late?" },
  { n: 4, text: "We're short in week 7 — what are our options?" },
  { n: 5, text: "Compare the factoring option against the credit line over the real drawdown period" },
  { n: 7, text: "Record that Brennan paid invoice 0412" },
  { n: 8, text: "Pay supplier invoice 0377" },
  { n: 9, text: "Pay all outstanding supplier invoices this week" },
  { n: 10, text: "Anything unusual in last month's transactions?" },
];

interface Exchange {
  request: string;
  steps: Step[];
  turn: Turn | null;
  error: string | null;
}

export function Console({ onChange }: { onChange: () => void }) {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  async function send(request: string) {
    if (!request.trim() || busy) return;
    setBusy(true);
    setInput("");
    const index = exchanges.length;
    setExchanges((xs) => [...xs, { request, steps: [], turn: null, error: null }]);
    setSelected(index);
    const update = (fn: (x: Exchange) => Exchange) =>
      setExchanges((xs) => xs.map((x, i) => (i === index ? fn(x) : x)));
    try {
      const turn = await chat(request, (step) =>
        update((x) => ({ ...x, steps: [...x.steps, step] })),
      );
      update((x) => ({ ...x, turn }));
    } catch (e) {
      update((x) => ({ ...x, error: (e as Error).message }));
    } finally {
      setBusy(false);
      onChange();
    }
  }

  const current = selected === null ? null : exchanges[selected];
  const latest = useRef<HTMLDivElement>(null);
  useEffect(() => {
    latest.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [exchanges.length]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="flex min-w-0 flex-col gap-4">
        {exchanges.length === 0 && (
          <Card title="Recorded scenarios">
            <p className="mb-3 text-sm text-slate-600">
              Each request below has a recorded model response, so it runs with no model server.
              The verifier, the rules and the ledger run live.
            </p>
            <div className="flex flex-col gap-1.5">
              {SCENARIOS.map((s) => (
                <button
                  key={s.n}
                  type="button"
                  onClick={() => send(s.text)}
                  className="flex items-baseline gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50"
                >
                  <span className="w-6 shrink-0 font-mono text-xs text-slate-400">{s.n}</span>
                  <span>{s.text}</span>
                </button>
              ))}
            </div>
          </Card>
        )}
        {exchanges.map((x, i) => (
          <div key={i} ref={i === exchanges.length - 1 ? latest : undefined} className="scroll-mt-4">
            <ExchangeView exchange={x} selected={i === selected} onSelect={() => setSelected(i)} />
          </div>
        ))}
        <form
          className="sticky bottom-0 flex gap-2 rounded-lg border border-slate-200 bg-white p-2 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about cash, financing, invoices or the audit trail"
            className="min-w-0 flex-1 rounded px-2 py-1.5 text-sm outline-none"
            disabled={busy}
          />
          <select
            className="w-40 rounded border border-slate-200 px-1 text-xs text-slate-600"
            value=""
            onChange={(e) => e.target.value && send(e.target.value)}
            disabled={busy}
          >
            <option value="">Recorded requests</option>
            {SCENARIOS.map((s) => (
              <option key={s.n} value={s.text}>
                {s.n}. {s.text}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {busy ? <Spinner /> : "Send"}
          </button>
        </form>
      </div>
      <aside className="min-w-0 lg:sticky lg:top-4 lg:max-h-[calc(100vh-6rem)] lg:overflow-auto">
        {current?.turn ? (
          <ReceiptPanel receipt={current.turn.receipt} />
        ) : (
          <Card title="Trust Receipt">
            <p className="text-sm text-slate-500">
              Every turn produces a receipt: the rows read, each figure recomputed, every rule
              evaluated, the tier and the risk flags. Select a turn to see its receipt.
            </p>
          </Card>
        )}
      </aside>
    </div>
  );
}

function ExchangeView({
  exchange,
  selected,
  onSelect,
}: {
  exchange: Exchange;
  selected: boolean;
  onSelect: () => void;
}) {
  const { request, steps, turn, error } = exchange;
  return (
    <article
      onClick={onSelect}
      className={`rounded-lg border bg-white ${selected ? "border-accent/50 ring-1 ring-accent/20" : "border-slate-200"}`}
    >
      <div className="border-b border-slate-100 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Owner</div>
        <div className="text-sm">{request}</div>
      </div>
      <Steps steps={steps} running={!turn && !error} />
      {error && (
        <div className="m-4 rounded border border-block/30 bg-block-soft p-3 text-sm text-block">
          {error.includes("ReplayMiss")
            ? "No recording exists for this request. In replay mode only the recorded requests can be answered. Nothing was done."
            : error}
        </div>
      )}
      {turn && <TurnView turn={turn} />}
    </article>
  );
}

// ------------------------------------------------------------------ steps

function lane(step: string): { label: string; tone: "slate" | "accent" | "warn" | "block" | "ok" } {
  if (step === "model") return { label: "Model", tone: "accent" };
  if (step === "plan" || step === "tools" || step === "scenario") return { label: "Code", tone: "slate" };
  if (step === "neutralised") return { label: "Containment", tone: "block" };
  return { label: "Verifier", tone: "ok" };
}

function describe(s: Step): string {
  const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : String(v ?? ""));
  switch (s.step) {
    case "plan":
      return `Allowlist from your words: ${list(s.routes) || "no route"} → kinds ${list(s.kinds)}${s.required ? `, must be ${s.required}` : ""}. Tools: ${list(s.tools) || "none"}.`;
    case "tools":
      return `Tools ran on the read-only ledger and returned ${s.facts} facts.`;
    case "neutralised":
      return `Instruction-like text in ledger data was neutralised and logged: ${JSON.stringify(s.spans)}`;
    case "model":
      return `${s.phase === "final" ? "Final answer" : "Draft"}, attempt ${s.attempt}, ${Number(s.elapsed_ms) === 0 ? "replayed from the recording" : `${(Number(s.elapsed_ms) / 1000).toFixed(1)} s`}: ${s.valid ? "parsed" : "did not parse"}.`;
    case "rejected":
      return `Rejected by the strict parser and sent back verbatim: ${String(s.errors)}`;
    case "dry_run":
      return Array.isArray(s.blocks) && s.blocks.length
        ? `Rules run on the draft without changing anything. Blocked: ${list(s.blocks)}. The model is shown the block.`
        : "Rules run on the draft without changing anything. Nothing blocks.";
    case "scenario":
      return "The scenario ran on a copy of the projection. The model is shown the result.";
    case "final_refused":
      return "The model would not answer about its own draft. The draft is what gets verified.";
    case "verified":
      return `Verified: ${s.outcome}, ${s.status}. Flags: ${list(s.flags) || "none"}.`;
    default:
      return JSON.stringify(s);
  }
}

function Steps({ steps, running }: { steps: Step[]; running: boolean }) {
  return (
    <ol className="space-y-1 border-b border-slate-100 px-4 py-3">
      {steps.map((s, i) => {
        const l = lane(s.step);
        return (
          <li key={i} className="flex items-start gap-2 text-xs">
            <span className="w-20 shrink-0 pt-0.5">
              <Badge tone={l.tone}>{l.label}</Badge>
            </span>
            <span className={s.step === "rejected" ? "text-block" : "text-slate-600"}>
              {describe(s)}
            </span>
          </li>
        );
      })}
      {running && (
        <li className="flex items-center gap-2 text-xs text-slate-500">
          <Spinner /> working
        </li>
      )}
    </ol>
  );
}

// ------------------------------------------------------------------ the verified turn

function outcomeTone(outcome: string): "ok" | "block" | "warn" | "slate" {
  if (outcome === "verified") return "ok";
  if (outcome === "blocked" || outcome === "rejected") return "block";
  return "slate";
}

function TurnView({ turn }: { turn: Turn }) {
  const omissions = new Map(turn.receipt.material_omissions.map((m) => [m.fact_id, m.why]));
  const corrections = turn.receipt.text_corrections;
  // A cited block is shown once, in the block box above the claims.
  const blocked = new Set(turn.blocked_by.map((b) => b.rule));
  const blockClaims = new Map(
    turn.claims
      .filter((c) => c.fact_id?.startsWith("block.") && blocked.has(c.fact_id.slice(6)))
      .map((c) => [c.fact_id!.slice(6), c.status]),
  );
  const claims = turn.claims.filter(
    (c) => !(c.fact_id?.startsWith("block.") && blocked.has(c.fact_id.slice(6))),
  );
  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={outcomeTone(turn.outcome)}>{turn.outcome}</Badge>
        <TierBadge tier={turn.tier} />
        {turn.kind && <span className="font-mono text-[11px] text-slate-500">{turn.kind}</span>}
        <span className="text-[11px] text-slate-400">{turn.status.replace("_", " ")}</span>
        {turn.receipt.risk_flags.map((f) => (
          <Badge key={f} tone={f === "MATERIAL_OMISSION" || f.includes("MISMATCH") || f.includes("MASKED") ? "warn" : "slate"}>
            {f}
          </Badge>
        ))}
      </div>

      {turn.message && (
        <div className="rounded border border-block/30 bg-block-soft p-3 text-sm text-block">
          {turn.message}
          {turn.errors && (
            <ul className="mt-2 list-disc pl-5 font-mono text-xs">
              {turn.errors.map((e, i) => (
                <li key={i}>
                  {e.location}: {e.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {turn.blocked_by.map((b) => (
        <div key={b.rule} className="rounded border border-block/40 bg-block-soft p-3">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-block">
            Blocked by {b.rule}
            {blockClaims.has(b.rule) && (
              <span className="font-normal normal-case tracking-normal text-slate-500">
                {blockClaims.get(b.rule) === "inserted_by_verifier"
                  ? "The model's answer left this out. The verifier put it back."
                  : "The model cited this block in its answer."}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-900">{b.reason}</p>
          <p className="mt-1 text-xs text-slate-600">
            Nothing was done. A block cannot be overridden by any approval.
          </p>
        </div>
      ))}

      <ul className="space-y-2">
        {claims.map((c, i) => (
          <ClaimView key={i} claim={c} why={c.fact_id ? omissions.get(c.fact_id) : undefined} />
        ))}
      </ul>

      {turn.rationale && (
        <div className="text-sm text-slate-700">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Model's rationale
          </div>
          <Marked text={turn.rationale} />
        </div>
      )}

      {corrections.length > 0 && (
        <div className="rounded border border-warn/30 bg-warn-soft p-3 text-xs">
          <div className="mb-1 font-semibold text-warn">Figures in the text the verifier changed</div>
          {corrections.map((c, i) => (
            <div key={i} className="flex flex-wrap gap-1">
              <span className="text-slate-500">{c.field}:</span>
              <s className="text-block">{c.original}</s>
              <span>→</span>
              <span className="font-medium">{c.replacement}</span>
              <span className="text-slate-500">· {c.reason}</span>
            </div>
          ))}
        </div>
      )}

      {turn.warnings.map((w) => (
        <div key={w.rule} className="rounded border border-warn/30 bg-warn-soft p-2 text-xs text-slate-800">
          <span className="font-semibold text-warn">{w.rule}: </span>
          {w.reason}
        </div>
      ))}

      {turn.disclosures.map((d) => (
        <p key={d} className="rounded bg-slate-100 p-2 text-xs text-slate-600">
          {d}
        </p>
      ))}

      {turn.approval.required && (
        <div className="rounded border border-warn/30 bg-warn-soft p-3 text-sm">
          Waiting for {turn.approval.by ?? "a person"} in the Approval Inbox.
          {turn.approval.confirmation_phrase && (
            <>
              {" "}
              T3: approval needs the typed phrase{" "}
              <code className="font-mono">{turn.approval.confirmation_phrase}</code>
              {turn.approval.not_before && <> after {new Date(turn.approval.not_before).toLocaleTimeString()}</>}.
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ClaimView({ claim, why }: { claim: Claim; why?: string }) {
  const inserted = claim.status === "inserted_by_verifier";
  const mandatory = claim.mandatory === true;
  const border = inserted
    ? "border-warn"
    : claim.status === "removed"
      ? "border-block"
      : claim.status === "corrected"
        ? "border-warn"
        : mandatory
          ? "border-accent"
          : "border-slate-200";
  const commentary =
    claim.commentary && claim.commentary.trim() !== claim.statement.trim() ? claim.commentary : null;
  return (
    <li className={`border-l-4 ${border} rounded-r bg-slate-50/60 py-2 pl-3 pr-2`}>
      {inserted && (
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-warn">
          Added by the verifier. The model's answer left this out{why ? `: ${why}` : ""}.
        </div>
      )}
      {mandatory && !inserted && (
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-accent">
          Mandatory fact, in the verifier's words
        </div>
      )}
      <p className="text-sm text-slate-900">
        <Marked text={claim.statement} />
      </p>
      {commentary && (
        <p className="mt-1 text-xs text-slate-500">
          <span className="font-medium">Model's commentary: </span>
          <Marked text={commentary} />
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
        <FigureNote claim={claim} />
        {claim.fact_id && <span className="font-mono text-[11px] text-slate-400">{claim.fact_id}</span>}
        <Rows ids={claim.sources} />
      </div>
    </li>
  );
}

function FigureNote({ claim }: { claim: Claim }) {
  if (claim.status === "corrected") {
    return (
      <span className="flex items-center gap-1">
        <Badge tone="warn">corrected</Badge>
        <span className="text-slate-500">model said</span>
        <s className="text-block">{eur(claim.model_figure)}</s>
        <span className="text-slate-500">recomputed</span>
        <span className="font-medium">{eur(claim.figure)}</span>
      </span>
    );
  }
  if (claim.status === "figure_removed") return <Badge tone="block">figure removed: not recomputable</Badge>;
  if (claim.status === "removed") return <Badge tone="block">removed: unsupported</Badge>;
  if (claim.figure) {
    return (
      <span className="flex items-center gap-1">
        <Badge tone="ok">{claim.status === "inserted_by_verifier" || claim.mandatory ? "computed by the verifier" : "recomputed"}</Badge>
        <span className="font-medium">{eur(claim.figure)}</span>
      </span>
    );
  }
  return <Badge>{claim.status === "inserted_by_verifier" ? "inserted" : "cited"}</Badge>;
}

// ------------------------------------------------------------------ receipt

export function ReceiptPanel({ receipt }: { receipt: Receipt }) {
  return (
    <Card
      title="Trust Receipt"
      right={
        <button
          type="button"
          className="text-xs text-accent hover:underline"
          onClick={() => download(`${receipt.receipt_id}.json`, receipt)}
        >
          Export JSON
        </button>
      }
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-slate-500">Receipt</dt>
        <dd className="font-mono">{receipt.receipt_id}</dd>
        <dt className="text-slate-500">Outcome</dt>
        <dd>
          <Badge tone={outcomeTone(receipt.outcome)}>{receipt.outcome}</Badge>{" "}
          <TierBadge tier={receipt.tier} />
        </dd>
        <dt className="text-slate-500">Audit event</dt>
        <dd className="font-mono">{receipt.audit_seq ?? "none"}</dd>
        <dt className="text-slate-500">Verifier</dt>
        <dd className="font-mono">{receipt.verifier_version}</dd>
        <dt className="text-slate-500">Tools that ran</dt>
        <dd className="font-mono">{receipt.tools_called.join(", ") || "none"}</dd>
      </dl>

      <Section title="Risk flags">
        {receipt.risk_flags.length ? (
          <div className="flex flex-wrap gap-1">
            {receipt.risk_flags.map((f) => (
              <Badge key={f} tone="warn">
                {f}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-xs text-slate-500">None</span>
        )}
      </Section>

      {receipt.material_omissions.length > 0 && (
        <Section title="Material omissions put back">
          {receipt.material_omissions.map((m) => (
            <div key={m.fact_id} className="mb-1 text-xs">
              <span className="font-mono text-slate-500">{m.fact_id}</span>, because {m.why}.
            </div>
          ))}
        </Section>
      )}

      <Section title={`Recomputations (${receipt.recomputations.length})`}>
        {receipt.recomputations.length === 0 && <span className="text-xs text-slate-500">No figures to recompute</span>}
        <div className="space-y-2">
          {receipt.recomputations.map((r, i) => (
            <div key={i} className="rounded border border-slate-100 p-2 text-xs">
              <div className="mb-1 flex items-center gap-1">
                <Badge tone={r.status === "match" ? "ok" : r.status === "mismatch" ? "warn" : "block"}>{r.status}</Badge>
                <span className="font-mono text-slate-500">{r.formula}</span>
              </div>
              <div className="flex flex-wrap gap-x-3">
                <span>
                  model <span className={r.status === "mismatch" ? "text-block line-through" : ""}>{eur(r.model_figure) || r.model_figure || "none"}</span>
                </span>
                <span>
                  recomputed <span className="font-medium">{eur(r.recomputed) || "none"}</span>
                </span>
              </div>
              {r.reason && <div className="text-slate-500">{r.reason}</div>}
              <div className="mt-1">
                <Rows ids={r.operands} />
              </div>
            </div>
          ))}
        </div>
      </Section>

      {receipt.text_corrections.length > 0 && (
        <Section title="Text corrections">
          {receipt.text_corrections.map((c, i) => (
            <div key={i} className="mb-1 text-xs">
              <s className="text-block">{c.original}</s> → <span className="font-medium">{c.replacement}</span>
              <div className="text-slate-500">{c.reason}</div>
            </div>
          ))}
        </Section>
      )}

      <Section title="Rules evaluated">
        {receipt.rules_evaluated.length === 0 && <span className="text-xs text-slate-500">Not reached</span>}
        <table className="w-full text-xs">
          <tbody>
            {receipt.rules_evaluated.map((r) => (
              <tr key={r.rule} className="border-b border-slate-50 align-top">
                <td className="py-1 pr-2">
                  <Badge tone={ruleTone(r.result)}>{r.result}</Badge>
                </td>
                <td className="py-1">
                  <div className="font-medium">{r.rule}</div>
                  <div className="text-slate-500">{r.reason}</div>
                  {r.rows.length > 0 && (
                    <div className="mt-0.5">
                      <Rows ids={r.rows} />
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {receipt.policy.length > 0 && (
        <Section title="Policy checks">
          {receipt.policy.map((p, i) => (
            <div key={i} className="mb-1 text-xs">
              <Badge tone={p.result === "violation" ? "block" : p.result === "corrected" ? "warn" : "ok"}>{p.result}</Badge>{" "}
              <span className="font-medium">{p.check}</span> <span className="text-slate-500">{p.detail}</span>
            </div>
          ))}
        </Section>
      )}

      {receipt.schema_errors.length > 0 && (
        <Section title="Schema errors">
          {receipt.schema_errors.map((e, i) => (
            <div key={i} className="font-mono text-xs text-block">
              {e.location}: {e.message}
            </div>
          ))}
        </Section>
      )}

      <Section title={`Rows read (${receipt.rows_read.length})`}>
        <div className="flex flex-wrap gap-1">
          {receipt.rows_read.map((r) => (
            <RowChip key={r.id} id={r.id} />
          ))}
        </div>
      </Section>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4">
      <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {children}
    </div>
  );
}
