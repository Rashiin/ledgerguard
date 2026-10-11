import { useCallback, useEffect, useState } from "react";
import { api, type Change, type Pending } from "./api";
import { Badge, Card, RowChip, Rows, Spinner, TierBadge, eur, ruleTone } from "./ui";

export function Inbox({ onChange }: { onChange: () => void }) {
  const [pending, setPending] = useState<Pending[] | null>(null);
  const [done, setDone] = useState<{ id: string; text: string; tone: "ok" | "block" }[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Bumped after every approval or decline, so each remaining diff is computed again
  // against the ledger as it now stands.
  const [version, setVersion] = useState(0);

  const load = useCallback(() => {
    api.approvals().then(
      (r) => {
        setPending(r.pending);
        setVersion((v) => v + 1);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(load, [load]);

  function finished(id: string, text: string, tone: "ok" | "block") {
    setDone((d) => [{ id, text, tone }, ...d]);
    load();
    onChange();
  }

  if (error) return <p className="text-sm text-block">{error}</p>;
  if (!pending) return <Spinner />;

  return (
    <div className="space-y-4">
      {done.map((d) => (
        <div
          key={d.id + d.text}
          className={`rounded border p-3 text-sm ${d.tone === "ok" ? "border-ok/30 bg-ok-soft" : "border-block/30 bg-block-soft"}`}
        >
          <span className="font-mono text-xs">{d.id}</span> {d.text}
        </div>
      ))}
      {pending.length === 0 && (
        <Card>
          <p className="text-sm text-slate-500">
            Nothing is waiting. T2 actions, which change internal records, and T3 actions, which move
            money or accept financing, land here after the verifier passes them. Try scenario 7 or 8 in
            the Console.
          </p>
        </Card>
      )}
      {pending.map((p) => (
        <PendingItem key={p.intent_id} item={p} version={version} onFinished={finished} />
      ))}
    </div>
  );
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function shown(value: unknown): string {
  const text = String(value);
  return /^-?\d+\.\d{2}$/.test(text) ? eur(text) : text;
}

function PendingItem({
  item,
  version,
  onFinished,
}: {
  item: Pending;
  version: number;
  onFinished: (id: string, text: string, tone: "ok" | "block") => void;
}) {
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [phrase, setPhrase] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = useNow();

  useEffect(() => {
    api.diff(item.intent_id).then(
      (r) => setChanges(r.changes),
      (e: Error) => setDiffError(e.message),
    );
  }, [item.intent_id, version]);

  const t3 = item.tier === "T3";
  const needed = item.approval.confirmation_phrase;
  const readyAt = item.approval.not_before ? Date.parse(item.approval.not_before) : 0;
  const wait = Math.max(0, Math.ceil((readyAt - now) / 1000));
  const blocked = item.rules_evaluated.some((r) => r.result === "BLOCK");
  const canApprove = !busy && !blocked && (!t3 || (phrase === needed && wait === 0));

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.approve(item.intent_id, "operator", t3 ? phrase : null);
      onFinished(item.intent_id, `approved and executed, audit event ${r.audit_seq}, ${r.changes.length} field changes.`, "ok");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function decline() {
    setBusy(true);
    try {
      await api.decline(item.intent_id, "operator", reason || "declined by operator");
      onFinished(item.intent_id, "declined.", "block");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2 normal-case tracking-normal">
          <TierBadge tier={item.tier} />
          <span className="font-mono text-slate-700">{item.kind}</span>
          <span className="font-mono text-slate-400">{item.intent_id}</span>
        </span>
      }
      right={<span className="font-mono text-[11px] text-slate-400">{item.receipt_id}</span>}
    >
      <p className="text-sm">{item.rationale}</p>

      {item.payload_figures && (
        <div className="mt-2 flex flex-wrap gap-3 text-sm">
          {Object.entries(item.payload_figures).map(([k, v]) => (
            <span key={k}>
              <span className="text-slate-500">{k.replaceAll("_", " ")}: </span>
              <span className="font-medium">{eur(v)}</span>
            </span>
          ))}
        </div>
      )}

      {item.risk_flags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.risk_flags.map((f) => (
            <Badge key={f} tone="warn">{f}</Badge>
          ))}
        </div>
      )}

      <h3 className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        What approving changes in the ledger
      </h3>
      {diffError && <p className="text-sm text-block">{diffError}</p>}
      {!changes && !diffError && <Spinner />}
      {changes && changes.length === 0 && <p className="text-sm text-slate-500">No ledger rows change.</p>}
      {changes && changes.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
              <th className="py-1 pr-2">Row</th>
              <th className="py-1 pr-2">Field</th>
              <th className="py-1 pr-2">Before</th>
              <th className="py-1">After</th>
            </tr>
          </thead>
          <tbody>
            {changes.map((c, i) => (
              <tr key={i} className="border-b border-slate-50 align-top">
                <td className="py-1 pr-2">
                  {c.before === null && i === changes.findIndex((x) => x.id === c.id) ? (
                    <span className="flex items-center gap-1"><Badge tone="accent">new</Badge><RowChip id={c.id} /></span>
                  ) : (
                    <RowChip id={c.id} />
                  )}
                </td>
                <td className="py-1 pr-2 font-mono text-xs text-slate-600">{c.field === "*" ? "new row" : c.field}</td>
                <td className="py-1 pr-2 font-mono text-xs">
                  {c.before === null ? <span className="text-slate-300">none</span> : <s className="text-block">{shown(c.before)}</s>}
                </td>
                <td className="py-1 font-mono text-xs font-medium text-ok">{shown(c.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-1 text-xs text-slate-500">
        Computed by running the Executor's own handler inside a transaction that is rolled back. Approval
        re-checks every rule against the ledger as it is at that moment.
      </p>

      <h3 className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Rules</h3>
      <ul className="space-y-1">
        {item.rules_evaluated
          .filter((r) => r.result !== "PASS")
          .map((r) => (
            <li key={r.rule} className="text-sm">
              <Badge tone={ruleTone(r.result)}>{r.result}</Badge> <span className="font-medium">{r.rule}</span>{" "}
              <span className="text-slate-600">{r.reason}</span> <Rows ids={r.rows} />
            </li>
          ))}
        <li className="text-xs text-slate-500">
          {item.rules_evaluated.filter((r) => r.result === "PASS").length} other rules passed.
        </li>
      </ul>

      <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3">
        {t3 && needed && (
          <label className="flex flex-col text-xs text-slate-500">
            <span>
              T3: type <code className="rounded bg-slate-100 px-1 font-mono text-slate-800">{needed}</code> to confirm
            </span>
            <input
              value={phrase}
              onChange={(e) => setPhrase(e.target.value)}
              className="mt-1 w-64 rounded border border-slate-300 px-2 py-1 font-mono text-sm"
              autoComplete="off"
            />
          </label>
        )}
        <button
          type="button"
          onClick={approve}
          disabled={!canApprove}
          className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {t3 && wait > 0 ? `Cool-off ${wait}s` : "Approve as operator"}
        </button>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason for declining"
          className="w-56 rounded border border-slate-200 px-2 py-1 text-sm"
        />
        <button
          type="button"
          onClick={decline}
          disabled={busy}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
        >
          Decline
        </button>
      </div>
      {error && <p className="mt-2 rounded bg-block-soft p-2 text-sm text-block">{error}</p>}
    </Card>
  );
}
