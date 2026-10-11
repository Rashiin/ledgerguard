import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

/** Format a decimal string as euros without turning it into a float. */
export function eur(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = (fraction + "00").slice(0, 2);
  return `${negative ? "-" : ""}€${grouped}.${cents}`;
}

/** For charts only. Plotting needs numbers; nothing computed here is shown as a figure. */
export function plot(value: string): number {
  return Number(value);
}

const TABLES: Record<string, string> = {
  biz: "business",
  acc: "account",
  txn: "transaction",
  cp: "counterparty",
  inv: "invoice",
  pay: "payroll_run",
  obl: "obligation",
  fin: "financing_product",
};

export function tableOf(rowId: string): string | null {
  return TABLES[rowId.split("_", 1)[0]] ?? null;
}

type Tone = "slate" | "accent" | "warn" | "block" | "ok";

const TONES: Record<Tone, string> = {
  slate: "bg-slate-100 text-slate-700 ring-slate-200",
  accent: "bg-accent-soft text-accent ring-blue-200",
  warn: "bg-warn-soft text-warn ring-amber-200",
  block: "bg-block-soft text-block ring-red-200",
  ok: "bg-ok-soft text-ok ring-emerald-200",
};

export function Badge({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white ${className}`}>
      {title !== undefined && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function tierTone(tier: string | null | undefined): Tone {
  if (tier === "T3") return "block";
  if (tier === "T2") return "warn";
  if (tier === "T1") return "accent";
  return "slate";
}

export function TierBadge({ tier }: { tier: string | null | undefined }) {
  return <Badge tone={tierTone(tier)}>{tier ?? "no tier"}</Badge>;
}

export function ruleTone(result: string): Tone {
  if (result === "BLOCK") return "block";
  if (result === "WARN") return "warn";
  return "ok";
}

/** A row id that opens the ledger row it names. Every figure drills down through these. */
export function RowChip({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [row, setRow] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const table = tableOf(id);

  useEffect(() => {
    if (!open || row || error || !table) return;
    api.row(table, id).then(
      (r) => setRow(r.row),
      (e: Error) => setError(e.message),
    );
  }, [open, row, error, table, id]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <span ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-700 hover:bg-accent-soft hover:text-accent"
        title={table ? `Open ${table} ${id}` : id}
      >
        {id}
      </button>
      {open && (
        <span className="absolute left-0 top-6 z-30 block w-80 rounded-md border border-slate-200 bg-white p-3 text-left shadow-lg">
          <span className="mb-2 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            {table ?? "row"} {id}
          </span>
          {error && <span className="block text-xs text-block">{error}</span>}
          {!row && !error && <span className="block text-xs text-slate-500">Loading…</span>}
          {row && (
            <span className="block max-h-64 overflow-auto">
              {Object.entries(row).map(([k, v]) => (
                <span key={k} className="flex gap-2 border-b border-slate-50 py-0.5 text-xs">
                  <span className="w-32 shrink-0 font-mono text-slate-500">{k}</span>
                  <span className="break-all text-slate-800">{String(v)}</span>
                </span>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  );
}

export function Rows({ ids }: { ids: string[] }) {
  if (ids.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <RowChip key={id} id={id} />
      ))}
    </span>
  );
}

// Markers the verifier writes into text in place of what it removed.
const MARKERS = /(\[figure removed: not verified\]|\[removed: guaranteed-outcome language\]|\[unsupported claim removed by verifier: [^\]]*\])/g;

/** Text with every verifier marker highlighted, so a removal is never invisible. */
export function Marked({ text }: { text: string }) {
  const parts = text.split(MARKERS);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <mark
            key={i}
            className="rounded bg-block-soft px-1 font-medium text-block"
            title="Removed by the verifier"
          >
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

export function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-accent" />
  );
}

export function download(name: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
