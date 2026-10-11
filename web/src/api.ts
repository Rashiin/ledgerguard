// Types mirror what the API returns. Money arrives as decimal strings and stays
// a string until it is formatted for display. The UI never does arithmetic on it.

export type ClaimStatus =
  | "verified"
  | "corrected"
  | "cited"
  | "figure_removed"
  | "removed"
  | "inserted_by_verifier";

export interface Claim {
  statement: string;
  figure: string | null;
  status: ClaimStatus;
  fact_id?: string;
  mandatory?: boolean;
  commentary?: string;
  model_figure?: string | null;
  sources: string[];
}

export interface RuleResult {
  rule: string;
  result: "PASS" | "WARN" | "BLOCK";
  reason: string;
  flags: string[];
  rows: string[];
}

export interface Recomputation {
  claim: string;
  claim_index: number;
  formula: string | null;
  match: boolean | null;
  model_figure: string | null;
  operands: string[];
  reason: string | null;
  recomputed: string | null;
  status: string;
}

export interface TextCorrection {
  field: string;
  original: string;
  replacement: string;
  reason: string;
}

export interface Approval {
  required: boolean;
  by: string | null;
  status: string;
  confirmation_phrase: string | null;
  not_before: string | null;
}

export interface Receipt {
  receipt_id: string;
  action_intent_id: string;
  outcome: string;
  kind: string | null;
  tier: string | null;
  rows_read: { table: string; id: string }[];
  schema_errors: { location: string; message: string }[];
  recomputations: Recomputation[];
  provenance: { claim_index: number; status: string; reason: string | null }[];
  rules_evaluated: RuleResult[];
  policy: { check: string; result: string; detail: string }[];
  risk_flags: string[];
  text_corrections: TextCorrection[];
  material_omissions: { fact_id: string; inserted: string; why: string }[];
  payload_figures: Record<string, string> | null;
  tools_called: string[];
  disclosures: string[];
  approval: Approval;
  verifier_version: string;
  created_at: string;
  audit_seq: number | null;
}

export interface Turn {
  intent_id: string;
  kind: string | null;
  outcome: string;
  tier: string | null;
  claims: Claim[];
  rationale: string;
  blocked_by: { rule: string; reason: string }[];
  warnings: { rule: string; reason: string }[];
  disclosures: string[];
  approval: Approval;
  receipt_id: string;
  status: string;
  audit_seq: number | null;
  receipt: Receipt;
  message?: string;
  errors?: { location: string; message: string }[];
}

export type Step = { step: string } & Record<string, unknown>;

export interface Pending {
  intent_id: string;
  kind: string | null;
  tier: string | null;
  payload: Record<string, unknown>;
  rationale: string;
  risk_flags: string[];
  rules_evaluated: RuleResult[];
  approval: Approval;
  payload_figures: Record<string, string> | null;
  receipt_id: string;
}

export interface Change {
  table: string;
  id: string;
  field: string;
  before: unknown;
  after: unknown;
}

export interface AuditEvent {
  id: string;
  seq: number;
  occurred_at: string;
  actor: string;
  event_type: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface ProjectedWeek {
  week: number;
  starts_on: string;
  ends_on: string;
  opening: string;
  inflow: Record<string, string>;
  outflow: Record<string, string>;
  closing_contracted: string;
  closing_with_expected: string;
  closing: string;
}

export interface Alert {
  id: string;
  kind: string;
  severity: string;
  headline: string;
  lead_days: number;
  rows: string[];
  classes: string[];
}

export interface ScenarioResult {
  description: string;
  before: { week: string; closing: string; closing_contracted: string }[];
  after: { week: string; closing: string; closing_contracted: string }[];
  new_alerts: Alert[];
  cleared_alerts: Alert[];
}

export interface FinancingOption {
  product_id: string;
  provider: string;
  name: string;
  kind: string;
  eligible: boolean;
  reasons: string[];
  amount: string;
  total_cost: string;
  score: string | null;
  days: number;
  full_term_months: number | null;
  funded_in_days: number;
  in_time: boolean;
  breakdown: { item: string; amount: string }[];
}

export interface FinancingMatch {
  synthetic_catalogue: boolean;
  gap: { amount: string; need_by: string; until: string; days: number; lead_days: number } | null;
  options: FinancingOption[];
  weights: Record<string, string>;
  do_nothing: { total_cost: string; consequence: string };
}

export interface Attachment {
  name: string;
  text: string;
}

export interface CatalogueTurn {
  request: string;
  attachments: Attachment[];
}

export interface Catalogue {
  scenarios: { id: string; n: number; phase: string; title: string; turns: CatalogueTurn[] }[];
  adversarial: { id: string; where: string; attack: string; turns?: CatalogueTurn[] }[];
}

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail ?? body));
  }
  return body as T;
}

export const api = {
  health: () => call<Record<string, unknown>>("/api/health"),
  scenarios: () => call<Catalogue>("/api/demo/scenarios"),
  summary: () => call<Record<string, unknown>>("/api/ledger/summary"),
  row: (table: string, id: string) =>
    call<{ table: string; row: Record<string, unknown> }>(`/api/ledger/rows/${table}/${id}`),
  receipt: (id: string) => call<Receipt>(`/api/receipts/${id}`),
  approvals: () => call<{ pending: Pending[] }>("/api/approvals"),
  diff: (id: string) => call<{ intent_id: string; changes: Change[] }>(`/api/approvals/${id}/diff`),
  approve: (id: string, approver: string, confirmation: string | null) =>
    call<{ changes: Change[]; audit_seq: number }>(`/api/approvals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approver, confirmation }),
    }),
  decline: (id: string, by: string, reason: string) =>
    call<{ audit_seq: number }>(`/api/approvals/${id}/decline`, {
      method: "POST",
      body: JSON.stringify({ by, reason }),
    }),
  audit: (fromSeq: number, limit: number) =>
    call<{ events: AuditEvent[] }>(`/api/audit?from_seq=${fromSeq}&limit=${limit}`),
  verify: () => call<{ intact: boolean; events: number; head_hash: string }>("/api/audit/verify"),
  projection: () =>
    call<{ as_of: string; cash_on_hand: string; weeks: ProjectedWeek[] }>(
      "/api/cashflow/projection?weeks=13",
    ),
  alerts: () => call<{ as_of: string; alerts: Alert[] }>("/api/alerts"),
  scenario: (scenario: Record<string, unknown>) =>
    call<ScenarioResult>("/api/cashflow/scenario", {
      method: "POST",
      body: JSON.stringify({ scenario }),
    }),
  financing: (weights: Record<string, string>) =>
    call<FinancingMatch>("/api/financing/match", {
      method: "POST",
      body: JSON.stringify({ weights }),
    }),
};

/** POST a chat message and hand each server-sent event to the callbacks. */
export async function chat(
  message: string,
  attachments: Attachment[],
  conversation: string,
  onStep: (step: Step) => void,
): Promise<Turn> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, attachments, conversation_id: conversation }),
  });
  if (!response.ok || !response.body) {
    throw new ApiError(`chat failed with HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turn: Turn | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const fields = Object.fromEntries(
        block.split("\n").map((line) => {
          const at = line.indexOf(": ");
          return [line.slice(0, at), line.slice(at + 2)];
        }),
      );
      const data = JSON.parse(fields.data ?? "null");
      if (fields.event === "step") onStep(data as Step);
      else if (fields.event === "turn") turn = data as Turn;
      else if (fields.event === "error") throw new ApiError(String(data?.message ?? "turn failed"));
    }
  }
  if (turn === null) throw new ApiError("the stream ended without a verified turn");
  return turn;
}
