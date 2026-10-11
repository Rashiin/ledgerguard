import { useEffect, useRef, useState } from "react";
import { api, chat, type Attachment, type Catalogue, type Claim, type Receipt, type Step, type Turn } from "./api";
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

interface Exchange {
  request: string;
  attachments: Attachment[];
  steps: Step[];
  turn: Turn | null;
  error: string | null;
}

interface Entry {
  key: string;
  label: string;
  title: string;
  turns: { request: string; attachments: Attachment[] }[];
}

// Demo order: read-only scenarios first, then the fixtures, then the approvals.
// Approving changes the ledger the recordings were made against (risk R-22).
function groups(c: Catalogue): { title: string; note: string; entries: Entry[] }[] {
  const scenario = (phase: string) =>
    c.scenarios
      .filter((s) => s.phase === phase)
      .map((s) => ({ key: s.id, label: String(s.n), title: s.title, turns: s.turns }));
  const fixtures = c.adversarial
    .filter((a) => a.turns)
    .map((a) => ({ key: a.id, label: a.id.slice(0, 2), title: `Injection in a ${a.where}`, turns: a.turns! }));
  return [
    { title: "Read-only scenarios", note: "Run these first.", entries: scenario("read") },
    {
      title: "Adversarial fixtures",
      note: "Scenario 11 is the first fixture. A2 to a6 follow.",
      entries: fixtures,
    },
    {
      title: "Approvals",
      note: "Make these requests last, all of them, then work through the Approval Inbox. Approving changes the ledger, and later requests stop replaying until it is seeded again.",
      entries: scenario("approval"),
    },
  ];
}

export function Console({ onChange }: { onChange: () => void }) {
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [input, setInput] = useState("");
  const [docName, setDocName] = useState("");
  const [docText, setDocText] = useState("");
  const [showDoc, setShowDoc] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const count = useRef(0);

  useEffect(() => {
    api.scenarios().then(setCatalogue, () => undefined);
  }, []);

  async function turn(request: string, attachments: Attachment[], conversation: string) {
    const index = count.current++;
    setExchanges((xs) => [...xs, { request, attachments, steps: [], turn: null, error: null }]);
    setSelected(index);
    const update = (fn: (x: Exchange) => Exchange) =>
      setExchanges((xs) => xs.map((x, i) => (i === index ? fn(x) : x)));
    try {
      const result = await chat(request, attachments, conversation, (step) =>
        update((x) => ({ ...x, steps: [...x.steps, step] })),
      );
      update((x) => ({ ...x, turn: result }));
    } catch (e) {
      update((x) => ({ ...x, error: (e as Error).message }));
    }
  }

  async function play(turns: { request: string; attachments: Attachment[] }[]) {
    if (busy || turns.length === 0) return;
    setBusy(true);
    const conversation = `conv_${Date.now()}`;
    try {
      for (const t of turns) await turn(t.request, t.attachments, conversation);
    } finally {
      setBusy(false);
      onChange();
    }
  }

  function submit() {
    if (!input.trim()) return;
    const attachments = showDoc && docText.trim() ? [{ name: docName.trim() || "document", text: docText }] : [];
    setInput("");
    setDocText("");
    setDocName("");
    setShowDoc(false);
    play([{ request: input, attachments }]);
  }

  const current = selected === null ? null : exchanges[selected];
  const latest = useRef<HTMLDivElement>(null);
  useEffect(() => {
    latest.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [exchanges.length]);

  const sections = catalogue ? groups(catalogue) : [];
  const all = sections.flatMap((g) => g.entries);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="flex min-w-0 flex-col gap-4">
        {exchanges.length === 0 && (
          <Card title="Recorded scenarios">
            <p className="mb-3 text-sm text-slate-600">
              Each request below has a recorded model response, so it runs with no model server.
              The verifier, the rules and the ledger run live.
            </p>
            {!catalogue && <Spinner />}
            {sections.map((g) => (
              <div key={g.title} className="mb-3">
                <div className="flex items-baseline gap-2">
                  <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{g.title}</h3>
                  <span className="text-xs text-slate-400">{g.note}</span>
                </div>
                <div className="mt-1 flex flex-col">
                  {g.entries.map((e) => (
                    <button
                      key={e.key}
                      type="button"
                      onClick={() => play(e.turns)}
                      className="flex items-baseline gap-3 rounded px-2 py-1.5 text-left text-sm hover:bg-slate-50"
                    >
                      <span className="w-6 shrink-0 font-mono text-xs text-slate-400">{e.label}</span>
                      <span className="w-56 shrink-0 text-xs text-slate-500">{e.title}</span>
                      <span>
                        {e.turns.map((t) => t.request).join(" → ")}
                        {e.turns.some((t) => t.attachments.length > 0) && (
                          <span className="ml-1 text-xs text-slate-400">with an attached document</span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </Card>
        )}
        {exchanges.map((x, i) => (
          <div key={i} ref={i === exchanges.length - 1 ? latest : undefined} className="scroll-mt-4">
            <ExchangeView exchange={x} selected={i === selected} onSelect={() => setSelected(i)} />
          </div>
        ))}
        <form
          className="sticky bottom-0 flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-2 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {showDoc && (
            <div className="flex flex-col gap-1">
              <input
                value={docName}
                onChange={(e) => setDocName(e.target.value)}
                placeholder="Document name, for example memo.pdf"
                className="rounded border border-slate-200 px-2 py-1 text-xs"
              />
              <textarea
                value={docText}
                onChange={(e) => setDocText(e.target.value)}
                placeholder="Text extracted from the document. It is treated as outside content, never as your instructions."
                className="h-24 rounded border border-slate-200 px-2 py-1 text-xs"
              />
            </div>
          )}
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about cash, financing, invoices or the audit trail"
              className="min-w-0 flex-1 rounded px-2 py-1.5 text-sm outline-none"
              disabled={busy}
            />
            <button
              type="button"
              onClick={() => setShowDoc(!showDoc)}
              className={`rounded border px-2 text-xs ${showDoc ? "border-accent text-accent" : "border-slate-200 text-slate-600"}`}
            >
              Attach text
            </button>
            <select
              className="w-40 rounded border border-slate-200 px-1 text-xs text-slate-600"
              value=""
              onChange={(e) => {
                const entry = all.find((x) => x.key === e.target.value);
                if (entry) play(entry.turns);
              }}
              disabled={busy}
            >
              <option value="">Recorded requests</option>
              {all.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label}. {e.turns[0].request}
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
          </div>
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
  const { request, attachments, steps, turn, error } = exchange;
  return (
    <article
      onClick={onSelect}
      className={`rounded-lg border bg-white ${selected ? "border-accent/50 ring-1 ring-accent/20" : "border-slate-200"}`}
    >
      <div className="border-b border-slate-100 px-4 py-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Owner</div>
        <div className="text-sm">{request}</div>
        {attachments.map((a) => (
          <div key={a.name} className="mt-1 text-xs text-slate-500">
            Attached: <span className="font-mono">{a.name}</span>, read as outside content
          </div>
        ))}
      </div>
      <Steps steps={steps} running={!turn && !error} />
      <Containment steps={steps} />
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

/** Shown whenever instruction-like text was removed from what the model reads. */
function Containment({ steps }: { steps: Step[] }) {
  const spans = steps
    .filter((s) => s.step === "neutralised")
    .flatMap((s) => (s.spans as { source: string; removed: string[] }[]) ?? []);
  if (spans.length === 0) return null;
  return (
    <div className="mx-4 mt-3 rounded border border-block/40 bg-block-soft p-3 text-sm">
      <div className="text-xs font-semibold uppercase tracking-wide text-block">Contained</div>
      <p className="mt-1 text-slate-800">
        Instruction-like text was found in outside content and removed before the model read it.
        The attempt is in the audit log. The turn's allowed actions come only from your words.
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {spans.map((s, i) => (
          <li key={i}>
            <span className="text-slate-500">{s.source}: </span>
            {s.removed.map((r, j) => (
              <q key={j} className="mr-2 font-mono text-block">
                {r}
              </q>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------ steps

// Which plane a step belongs to. The model only ever proposes. Everything else is code.
function isModel(step: string): boolean {
  return step === "model";
}

function describe(s: Step): { title: string; detail?: string; tone?: "block" | "warn" | "ok" } {
  const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : String(v ?? ""));
  switch (s.step) {
    case "plan":
      return {
        title: "Decided what this request may do, from your words alone",
        detail: `Allowed: ${list(s.kinds)}${s.required ? `, must be ${s.required}` : ""}. Tools: ${list(s.tools) || "none"}.`,
      };
    case "tools":
      return { title: `Read the ledger, read-only, and computed ${s.facts} facts` };
    case "neutralised":
      return {
        title: "Removed instructions hidden in outside text before the model read it",
        tone: "block",
      };
    case "attachments":
      return { title: `Treated the attached document as outside content: ${list(s.documents)}` };
    case "model": {
      const when = Number(s.elapsed_ms) === 0 ? "replayed from the recording" : `${(Number(s.elapsed_ms) / 1000).toFixed(1)} s`;
      return {
        title: `${s.phase === "final" ? "Wrote its final answer" : "Drafted an action"}${Number(s.attempt) > 1 ? `, attempt ${s.attempt}` : ""}`,
        detail: when,
        tone: s.valid ? undefined : "warn",
      };
    }
    case "rejected":
      return { title: "Rejected the model's output and sent the errors back", detail: String(s.errors), tone: "warn" };
    case "dry_run":
      return Array.isArray(s.blocks) && s.blocks.length
        ? {
            title: `Ran every rule on the draft first. ${list(s.blocks).replaceAll("block.", "")} blocked it`,
            detail: "Nothing changed. The model was shown the block.",
            tone: "block",
          }
        : { title: "Ran every rule on the draft first. Nothing blocked", detail: "Nothing changed." };
    case "scenario":
      return { title: "Ran the scenario on a copy of the projection", detail: "The ledger did not change." };
    case "final_refused":
      return { title: "The model would not answer about its own draft. The draft is what was checked", tone: "warn" };
    case "verified":
      return {
        title: `Verified the answer: ${s.outcome}`,
        detail: list(s.flags) || "no flags",
        tone: s.outcome === "verified" ? "ok" : "block",
      };
    default:
      return { title: s.step };
  }
}

const TONE_TEXT = { block: "text-block", warn: "text-warn", ok: "text-ok" } as const;

/** The turn as two lanes: what the model proposed on the left, what code did on the right. */
function Steps({ steps, running }: { steps: Step[]; running: boolean }) {
  return (
    <div className="border-b border-slate-100 px-4 py-3">
      <div className="mb-1.5 grid grid-cols-2 gap-3 text-[10px] font-semibold uppercase tracking-wider">
        <div className="flex items-center gap-1.5 text-accent">
          <span className="h-2 w-2 rounded-full bg-accent" /> Model · proposes
        </div>
        <div className="flex items-center gap-1.5 text-ok">
          <span className="h-2 w-2 rounded-sm bg-ok" /> Code · checks and decides
        </div>
      </div>
      <ol className="space-y-1">
        {steps.map((s, i) => {
          const d = describe(s);
          const model = isModel(s.step);
          const box = (
            <div
              className={`rounded-md border px-2.5 py-1.5 text-xs leading-snug ${
                model
                  ? "border-blue-200 bg-blue-50/70"
                  : d.tone === "block"
                    ? "border-red-200 bg-red-50"
                    : d.tone === "warn"
                      ? "border-amber-200 bg-amber-50"
                      : "border-slate-200 bg-white"
              }`}
            >
              <div className={`font-medium ${d.tone ? TONE_TEXT[d.tone] : model ? "text-blue-900" : "text-slate-800"}`}>
                {d.title}
              </div>
              {d.detail && <div className="mt-0.5 break-words text-[11px] text-slate-500">{d.detail}</div>}
            </div>
          );
          return (
            <li key={i} className="grid grid-cols-2 gap-3">
              {model ? box : <div className="border-r border-dashed border-slate-200" />}
              {model ? <div /> : box}
            </li>
          );
        })}
        {running && (
          <li className="grid grid-cols-2 gap-3">
            <div />
            <div className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-slate-500">
              <Spinner /> working
            </div>
          </li>
        )}
      </ol>
    </div>
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
          {why
            ? `Added by the verifier. The model's answer left this out: ${why}.`
            : "Added by the verifier, computed for exactly this action."}
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
