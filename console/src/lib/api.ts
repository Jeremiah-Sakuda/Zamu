/**
 * The console's view of Zamu.
 *
 * Shapes mirror `zamu/api/views.py` exactly. They are written out by hand rather than
 * generated because they are the contract between two languages, and a contract you
 * cannot read is not one you can rely on.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_ZAMU_API?.replace(/\/$/, "") ?? "http://localhost:8000";

export const DEMO_ORG = process.env.NEXT_PUBLIC_ZAMU_ORG ?? "org_demo_riverside";

export type CoverageState = "covered" | "at_risk" | "uncovered" | "unknown";

export interface Org {
  id: string;
  name: string;
  timezone: string;
  demo: boolean;
  now: string;
  local_time: string;
  coverage: Record<CoverageState, number>;
  fairness_window_weeks: number;
  max_asks_per_person_per_week: number;
}

export interface Summary {
  covered: number;
  at_risk: number;
  uncovered: number;
  unknown: number;
  needs_attention: number;
  open_asks: number;
}

export interface PendingAsk {
  id: string;
  person: string;
  expires_at: string;
  rationale: string;
  drafted_only: boolean;
}

export interface Duty {
  id: string;
  title: string;
  role: string;
  required_qualification: string | null;
  starts_at: string;
  ends_at: string;
  when: string;
  notice: string;
  hours: number;
  state: CoverageState;
  reason: string;
  needs_filling: boolean;
  is_past: boolean;
  assigned: { id: string; name: string } | null;
  confirmed_at: string | null;
  pending_ask: PendingAsk | null;
}

export interface Person {
  id: string;
  name: string;
  email: string;
  active: boolean;
  qualifications: string[];
  opted_in: boolean;
  quiet_hours: string | null;
  shifts_carried: number;
  hours_carried: number;
  unsociable_hours: number;
  weighted_load: number;
  asks_received: number;
  declines: number;
  accepts: number;
  last_asked_at: string | null;
  summary: string;
}

export interface Grant {
  level: number;
  key: string;
  label: string;
  granted: boolean;
  changeable: boolean;
  forbidden: boolean;
  default_on: boolean;
  granted_by: string | null;
  granted_at: string | null;
  note: string;
  description: string;
}

export interface BriefItem {
  headline: string;
  detail: string;
  duty_id: string | null;
  person_id: string | null;
  action_id: string | null;
  policy_rule: string;
}

export interface Brief {
  org_id: string;
  org_name: string;
  generated_at: string;
  since: string;
  filled: BriefItem[];
  waiting: BriefItem[];
  needs_decision: BriefItem[];
  not_allowed: BriefItem[];
  fairness_note: string;
  needs_human: boolean;
  text?: string;
}

export interface Components {
  fairness: number;
  fit: number;
  responsiveness: number;
  notice: number;
  rest: number;
}

export interface Candidate {
  rank: number;
  person_id: string;
  name: string;
  score: number;
  rationale: string;
  load_summary: string;
  fairness_debt_hours: number;
  components: Components;
  asks_remaining: number;
}

export interface CandidateOrder {
  duty_id: string;
  duty_title: string;
  when: string;
  team_average_load_hours: number;
  computed_at: string;
  candidates: Candidate[];
  excluded: { person_id: string; name: string; reason: string }[];
}

export type Outcome =
  | "asked"
  | "drafted"
  | "already_covered"
  | "waiting"
  | "deferred"
  | "no_candidates"
  | "blocked"
  | "failed"
  | "replayed"
  | "withdrawn";

export interface AskResult {
  outcome: Outcome;
  duty_id: string;
  detail: string;
  person_id: string | null;
  person_name: string | null;
  ask_id: string | null;
  action_id: string | null;
  rationale: string;
  policy_rule: string;
  expires_at: string | null;
  needs_coordinator: boolean;
  excluded: { person: string; reason: string }[];
  draft_subject: string;
  draft_text: string;
}

export interface Receipt {
  id: string;
  at: string;
  action: string;
  action_level: number;
  summary: string;
  policy_rule: string;
  result: "verified" | "failed" | "conflicted" | "blocked" | "reversed" | "in_progress";
  detail: string;
  intended: Record<string, unknown>;
  observed: Record<string, unknown> | null;
  duty_id: string | null;
  person_id: string | null;
  executed_at: string | null;
  verified_at: string | null;
}

export interface OutboxMessage {
  to_name: string;
  to_email: string;
  subject: string;
  text: string;
  html: string;
  delivered: boolean;
  provider: string;
  detail: string;
  ask_id: string | null;
  state: string | null;
  accept_url: string | null;
  decline_url: string | null;
}

export interface Console {
  org: Org;
  summary: Summary;
  duties: Duty[];
  people: Person[];
  grants: Grant[];
  brief: Brief;
}

export interface AgentRun {
  model: string;
  message: string;
  reply: string;
  tools_called: string[];
  refusals: { tool: string; rule: string; reason: string }[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* the body was not JSON; the status line is all we have */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  console: (org = DEMO_ORG) => request<Console>(`/api/orgs/${org}`),
  candidates: (dutyId: string, org = DEMO_ORG) =>
    request<CandidateOrder>(`/api/orgs/${org}/duties/${dutyId}/candidates`),
  ask: (dutyId: string, org = DEMO_ORG) =>
    request<AskResult>(`/api/orgs/${org}/duties/${dutyId}/ask`, { method: "POST" }),
  withdraw: (dutyId: string, personId: string, evidence: string, org = DEMO_ORG) =>
    request<AskResult>(`/api/orgs/${org}/duties/${dutyId}/withdraw`, {
      method: "POST",
      body: JSON.stringify({ person_id: personId, evidence }),
    }),
  sweep: (org = DEMO_ORG) => request<unknown>(`/api/orgs/${org}/sweep`, { method: "POST" }),
  receipts: (org = DEMO_ORG) => request<Receipt[]>(`/api/orgs/${org}/receipts?limit=60`),
  outbox: (org = DEMO_ORG) => request<OutboxMessage[]>(`/api/orgs/${org}/outbox`),
  setGrant: (key: string, granted: boolean, org = DEMO_ORG) =>
    request<{ grants: Grant[] }>(`/api/orgs/${org}/grants/${key}`, {
      method: "POST",
      body: JSON.stringify({ granted, granted_by: "coordinator" }),
    }),
  runAgent: (message: string, org = DEMO_ORG) =>
    request<AgentRun>(`/api/orgs/${org}/agent`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  resetDemo: () => request<{ reset: boolean }>(`/api/demo/reset`, { method: "POST" }),
};
