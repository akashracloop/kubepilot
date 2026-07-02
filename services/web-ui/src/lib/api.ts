/**
 * Typed client for the KubePilot api-gateway.
 *
 * Phase 1 auth: a single API key sent as the `X-API-Key` header on every
 * request. Because EventSource cannot set headers, the SSE stream is consumed
 * with fetch() + ReadableStream so the same header can be attached (see
 * `streamInvestigation`).
 */

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080"
).replace(/\/$/, "");

export const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

export type InvestigationStatus = "pending" | "running" | "completed" | "failed";

export interface Evidence {
  source_agent: string;
  kind: string;
  summary: string;
  detail?: Record<string, unknown>;
  severity: string;
  collected_at?: string;
}

export interface Rca {
  root_cause: string;
  root_cause_category: string;
  confidence: number;
  reasoning: string;
  recommendations: string[];
  evidence_refs: number[];
}

export interface Recommendation {
  title: string;
  rationale: string;
  commands: string[];
  risk: string;
  reversibility?: string;
  priority?: number;
  requires_approval: boolean;
}

export type TimelineSeverity = "info" | "warning" | "error" | "critical";

export interface TimelineEntry {
  at: string;
  label: string;
  description: string;
  source: string;
  severity: TimelineSeverity;
}

export interface MemoryContextItem {
  incident_id: string;
  summary: string;
  root_cause_category: string | null;
  namespace: string | null;
  service: string | null;
  similarity: number;
  outcome: string | null;
  occurred_at: string | null;
}

// Phase 3: the critic agent's assessment of the RCA.
export interface Critique {
  agreement: number;
  concerns: string[];
  adjusted_confidence: number | null;
  escalate_to_human: boolean;
}

// Phase 3: a fact from the cluster knowledge graph.
export interface ServiceKnowledge {
  service: string;
  owner: string | null;
  dependencies: string[];
  dependents: string[];
  slos: Record<string, unknown>;
  last_deploy: string | null;
  notes: string | null;
}

export interface InvestigationState {
  current_step?: string;
  completed_agents?: string[];
  confidence?: number | null;
  evidence?: Evidence[];
  rca?: Rca | null;
  recommendations?: Recommendation[];
  timeline?: TimelineEntry[];
  memory_context?: MemoryContextItem[];
  // Phase 3 additive fields.
  critique?: Critique | null;
  calibrated_confidence?: number | null;
  knowledge_context?: ServiceKnowledge[];
  prompt_versions?: Record<string, string>;
  // Phase 4 remediation.
  remediation_plan?: { actions: RemediationActionState[]; notes?: string | null } | null;
  remediation_outcome?: string | null;
  [key: string]: unknown;
}

export interface RemediationActionState {
  tool: string;
  target: string;
  namespace: string;
  reversibility: string;
  approval_tier: string;
  rationale?: string;
  estimated_blast_radius?: {
    pods_affected?: number | null;
    traffic_percent?: number | null;
    dependents?: string[];
  } | null;
}

export interface InvestigationDetail {
  incident_id: string;
  status: InvestigationStatus;
  query: string;
  namespace: string;
  service: string | null;
  created_at: string;
  updated_at: string;
  error: string | null;
  state: InvestigationState;
}

export interface InvestigationList {
  items: InvestigationDetail[];
  limit: number;
  offset: number;
}

export interface CreateInvestigationRequest {
  query: string;
  namespace: string;
  service?: string;
  time_window_minutes: number;
}

export interface CreateInvestigationResponse {
  incident_id: string;
  status: InvestigationStatus;
  created_at: string;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra || {}) };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return headers;
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ? JSON.stringify(body.detail) : detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

export async function createInvestigation(
  body: CreateInvestigationRequest
): Promise<CreateInvestigationResponse> {
  const res = await fetch(`${API_BASE_URL}/investigations`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return handle<CreateInvestigationResponse>(res);
}

export async function listInvestigations(
  limit = 20,
  offset = 0
): Promise<InvestigationList> {
  const res = await fetch(
    `${API_BASE_URL}/investigations?limit=${limit}&offset=${offset}`,
    { headers: authHeaders(), cache: "no-store" }
  );
  return handle<InvestigationList>(res);
}

export async function getInvestigation(
  id: string
): Promise<InvestigationDetail> {
  const res = await fetch(`${API_BASE_URL}/investigations/${id}`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<InvestigationDetail>(res);
}

// Phase 4: HITL remediation approval.
export interface ApprovalAction {
  index: number;
  tool: string;
  target: string;
  namespace: string;
  reversibility: string;
  approval_tier: string;
  rationale: string;
  blast_radius: {
    pods_affected?: number | null;
    traffic_percent?: number | null;
    dependents?: string[];
    summary?: string;
  } | null;
  dry_run_preview?: string | null;
}

export interface ApprovalView {
  status: string; // pending_approval | approved | rejected | expired | no_plan
  actions: ApprovalAction[];
}

export async function getApproval(id: string): Promise<ApprovalView> {
  const res = await fetch(`${API_BASE_URL}/investigations/${id}/approval`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<ApprovalView>(res);
}

export async function decideRemediation(
  id: string,
  decision: "approve" | "reject",
  actionIndex: number
): Promise<{ status: string; action_index: number }> {
  const res = await fetch(`${API_BASE_URL}/investigations/${id}/${decision}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ action_index: actionIndex }),
  });
  return handle<{ status: string; action_index: number }>(res);
}

// ---------------------------------------------------------------------------
// Settings (UI-editable live config). Mutations require an admin key; because
// the baked NEXT_PUBLIC_API_KEY may not be admin, the Settings page lets the
// user paste an admin key that we store locally and send on settings requests.
// ---------------------------------------------------------------------------

const ADMIN_KEY_STORAGE = "kp_admin_key";

export function getAdminKey(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ADMIN_KEY_STORAGE) || "";
}

export function setAdminKey(key: string): void {
  if (typeof window === "undefined") return;
  if (key) window.localStorage.setItem(ADMIN_KEY_STORAGE, key);
  else window.localStorage.removeItem(ADMIN_KEY_STORAGE);
}

function settingsHeaders(extra?: Record<string, string>): Record<string, string> {
  const key = getAdminKey() || API_KEY;
  const headers: Record<string, string> = { ...(extra || {}) };
  if (key) headers["X-API-Key"] = key;
  return headers;
}

export interface SettingField {
  key: string;
  kind: "bool" | "string" | "select" | "csv" | "json";
  label: string;
  help: string;
  options: string[] | null;
  restart_required: boolean;
  value: unknown;
  overridden: boolean;
}

export interface SettingsResponse {
  groups: Record<string, SettingField[]>;
  readonly: { label: string; value: string }[];
  kill_switch: boolean;
  editable: boolean;
}

export async function getSettings(): Promise<SettingsResponse> {
  const res = await fetch(`${API_BASE_URL}/settings`, {
    headers: settingsHeaders(),
    cache: "no-store",
  });
  return handle<SettingsResponse>(res);
}

export async function putSettings(
  overrides: Record<string, unknown>
): Promise<{ ok: boolean; rebuilt: boolean }> {
  const res = await fetch(`${API_BASE_URL}/settings`, {
    method: "PUT",
    headers: settingsHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ overrides }),
  });
  return handle<{ ok: boolean; rebuilt: boolean }>(res);
}

export async function setKillSwitch(
  enabled: boolean
): Promise<{ kill_switch: boolean }> {
  const res = await fetch(`${API_BASE_URL}/settings/kill-switch`, {
    method: "POST",
    headers: settingsHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ enabled }),
  });
  return handle<{ kill_switch: boolean }>(res);
}

export interface StreamEvent {
  event: string;
  data: unknown;
}

/**
 * Open the SSE stream for an investigation using fetch + ReadableStream so the
 * `X-API-Key` header can be set (EventSource cannot set headers).
 *
 * Parses the `event: <type>\ndata: <json>\n\n` frame format. Yields one
 * StreamEvent per frame via the `onEvent` callback. Returns when the stream
 * closes or `signal` aborts. `ping` frames are surfaced too; callers ignore them.
 */
export async function streamInvestigation(
  id: string,
  onEvent: (evt: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/investigations/${id}/stream`, {
    headers: authHeaders({ Accept: "text/event-stream" }),
    signal,
    cache: "no-store",
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushFrame = (frame: string) => {
    const lines = frame.split("\n");
    let event = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
      // ignore id:, retry:, comments (":") etc.
    }
    if (dataLines.length === 0 && event === "message") return;
    const raw = dataLines.join("\n");
    let data: unknown = raw;
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch {
      /* leave as raw string */
    }
    onEvent({ event, data });
  };

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line. Handle \n\n and \r\n\r\n.
    let sep: number;
    while (
      (sep = indexOfFrameBoundary(buffer)) !== -1
    ) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep).replace(/^(\r?\n){2}/, "");
      if (frame.trim()) flushFrame(frame);
    }
  }
  if (buffer.trim()) flushFrame(buffer);
}

function indexOfFrameBoundary(buf: string): number {
  const a = buf.indexOf("\n\n");
  const b = buf.indexOf("\r\n\r\n");
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}
