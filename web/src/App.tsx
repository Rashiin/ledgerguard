import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Audit } from "./Audit";
import { Console } from "./Console";
import { Inbox } from "./Inbox";
import { Radar } from "./Radar";

const SCREENS = [
  ["console", "Console"],
  ["radar", "Cash-Flow Radar"],
  ["inbox", "Approval Inbox"],
  ["audit", "Audit"],
] as const;

type Screen = (typeof SCREENS)[number][0];

function fromHash(): Screen {
  const name = window.location.hash.slice(1);
  return (SCREENS.find(([k]) => k === name)?.[0] ?? "console") as Screen;
}

export function App() {
  const [screen, setScreen] = useState<Screen>(fromHash);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [pending, setPending] = useState(0);
  const [version, setVersion] = useState(0);

  const refresh = useCallback(() => {
    api.approvals().then((r) => setPending(r.pending.length), () => undefined);
    setVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    const onHash = () => setScreen(fromHash());
    window.addEventListener("hashchange", onHash);
    api.health().then(setHealth, () => undefined);
    refresh();
    return () => window.removeEventListener("hashchange", onHash);
  }, [refresh]);

  const ledger = health?.ledger as { business?: string; data_as_of?: string } | undefined;

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold tracking-tight">LedgerGuard</span>
            <span className="hidden text-xs text-slate-500 md:inline">
              The model proposes. Code decides. · {ledger?.business ?? "sandbox ledger"}
            </span>
          </div>
          <nav className="flex gap-1">
            {SCREENS.map(([key, label]) => (
              <a
                key={key}
                href={`#${key}`}
                className={`rounded px-3 py-1.5 text-sm ${screen === key ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
              >
                {label}
                {key === "inbox" && pending > 0 && (
                  <span className="ml-1.5 rounded-full bg-warn px-1.5 text-[11px] text-white">{pending}</span>
                )}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
            {ledger?.data_as_of && <span>ledger as of {ledger.data_as_of}</span>}
            {health && (
              <span title="Where model responses come from">
                model: {String(health.llm_client ?? "unknown")}
              </span>
            )}
            <span>synthetic data, sandbox only</span>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">
        {screen === "console" && <Console onChange={refresh} />}
        {screen === "radar" && <Radar key={version} />}
        {screen === "inbox" && <Inbox onChange={refresh} />}
        {screen === "audit" && <Audit version={version} />}
      </main>
    </div>
  );
}
