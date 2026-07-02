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
  detail?: string;
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

export interface InvestigationState {
  current_step?: string;
  completed_agents?: string[];
  confidence?: number | null;
  evidence?: Evidence[];
  rca?: Rca | null;
  recommendations?: Recommendation[];
  timeline?: TimelineEntry[];
  memory_context?: MemoryContextItem[];
  [key: string]: unknown;
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
