import { Fragment, useCallback, useEffect, useState } from "react";
import { api, type AuditEvent } from "./api";
import { Badge, Card, Spinner, download } from "./ui";

const PAGE = 50;

export function Audit({ version }: { version: number }) {
  const [check, setCheck] = useState<{ intact: boolean; events: number; head_hash: string } | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setCheck(null);
    Promise.all([api.verify(), api.audit(1, 1000)]).then(
      ([v, a]) => {
        setCheck(v);
        setEvents(a.events);
      },
      (e: Error) => setError(e.message),
    );
  }, []);

  useEffect(load, [load, version]);

  if (error) return <p className="text-sm text-block">{error}</p>;

  const shown = events ? [...events].reverse().slice(0, PAGE) : [];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-4">
          {check === null ? (
            <Spinner />
          ) : check.intact ? (
            <span className="text-lg font-semibold text-ok">Chain intact ✓</span>
          ) : (
            <span className="text-lg font-semibold text-block">Chain broken ✗</span>
          )}
          {check && (
            <span className="text-sm text-slate-600">
              {check.events} events, each hashed with the one before it.
            </span>
          )}
          <button type="button" onClick={load} className="rounded border border-slate-300 px-2 py-1 text-xs">
            Verify again
          </button>
          {events && (
            <button
              type="button"
              onClick={() => download("audit_chain.json", events)}
              className="rounded border border-slate-300 px-2 py-1 text-xs"
            >
              Export chain
            </button>
          )}
        </div>
        {check && (
          <p className="mt-2 break-all font-mono text-[11px] text-slate-500">head {check.head_hash}</p>
        )}
        <p className="mt-2 text-xs text-slate-500">
          Tamper-evident with a published head hash, not tamper-proof. Changing any stored event breaks
          every hash after it, and the check above walks the whole chain.
        </p>
      </Card>

      <Card title={`Latest ${Math.min(PAGE, shown.length)} events`}>
        {!events && <Spinner />}
        <table className="w-full text-sm">
          <tbody>
            {shown.map((e) => (
              <Fragment key={e.seq}>
                <tr
                  onClick={() => setOpen(open === e.seq ? null : e.seq)}
                  className="cursor-pointer border-b border-slate-50 align-top hover:bg-slate-50"
                >
                  <td className="w-12 py-1.5 pr-2 font-mono text-xs text-slate-400">{e.seq}</td>
                  <td className="w-44 py-1.5 pr-2 text-xs text-slate-500">
                    {new Date(e.occurred_at).toLocaleString()}
                  </td>
                  <td className="w-20 py-1.5 pr-2">
                    <Badge tone={e.actor === "system" ? "slate" : e.actor === "llm" ? "accent" : "ok"}>{e.actor}</Badge>
                  </td>
                  <td className="py-1.5 pr-2 font-mono text-xs">
                    <Badge
                      tone={
                        e.event_type.includes("blocked") || e.event_type.includes("refused") || e.event_type.includes("neutralised")
                          ? "block"
                          : e.event_type.includes("executed")
                            ? "ok"
                            : "slate"
                      }
                    >
                      {e.event_type}
                    </Badge>
                  </td>
                  <td className="py-1.5 font-mono text-[11px] text-slate-400">{e.hash.slice(0, 12)}</td>
                </tr>
                {open === e.seq && (
                  <tr className="bg-slate-50">
                    <td colSpan={5} className="p-3">
                      <div className="mb-2 break-all font-mono text-[11px] text-slate-500">
                        prev {e.prev_hash}
                        <br />
                        hash {e.hash}
                      </div>
                      <pre className="max-h-80 overflow-auto rounded bg-white p-2 text-[11px]">
                        {JSON.stringify(e.payload, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
