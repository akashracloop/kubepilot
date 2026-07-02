"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  decideRemediation,
  getInvestigation,
  streamInvestigation,
  type InvestigationDetail,
  type StreamEvent,
} from "@/lib/api";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Code,
} from "@/components/ui";
import { SeverityBadge, StatusBadge } from "@/components/StatusBadge";

interface ProgressLine {
  key: string;
  label: string;
  detail?: string;
}

const TERMINAL = new Set(["completed", "failed"]);

function fmt(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function fmtTime(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

export default function InvestigationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [detail, setDetail] = useState<InvestigationDetail | null>(null);
  const [progress, setProgress] = useState<ProgressLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  function pushProgress(label: string, detailText?: string) {
    seq.current += 1;
    setProgress((prev) => [
      ...prev,
      { key: `${Date.now()}-${seq.current}`, label, detail: detailText },
    ]);
  }

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let cancelled = false;

    async function finalize() {
      try {
        const full = await getInvestigation(id);
        if (!cancelled) setDetail(full);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err));
      }
    }

    function onEvent(evt: StreamEvent) {
      if (cancelled) return;
      const data = (evt.data ?? {}) as Record<string, unknown>;
      switch (evt.event) {
        case "ping":
          return;
        case "investigation_started":
          pushProgress("Investigation started");
          return;
        case "node_started": {
          const node = (data.node as string) || "node";
          pushProgress(`${node} started`);
          return;
        }
        case "node_completed": {
          const node = (data.node as string) || "node";
          pushProgress(`${node} completed`);
          return;
        }
        case "investigation_completed":
          pushProgress("Investigation completed");
          // The terminal frame carries the full detail; still GET for canonical state.
          finalize();
          return;
        case "investigation_failed":
          pushProgress("Investigation failed");
          finalize();
          return;
        default:
          return;
      }
    }

    async function run() {
      // Load current snapshot first so late-joiners see prior state.
      try {
        const snap = await getInvestigation(id);
        if (cancelled) return;
        setDetail(snap);
        if (TERMINAL.has(snap.status)) {
          return; // already done — no need to stream
        }
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : String(err));
      }

      try {
        await streamInvestigation(id, onEvent, controller.signal);
      } catch (err) {
        if (!cancelled && !controller.signal.aborted) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
      // Stream closed — make sure we have the final record.
      if (!cancelled) await finalize();
    }

    run();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const state = detail?.state ?? {};
  const rca = state.rca ?? null;
  const evidence = state.evidence ?? [];
  const recommendations = state.recommendations ?? [];
  const timeline = state.timeline ?? [];
  const memoryContext = state.memory_context ?? [];
  const critique = state.critique ?? null;
  const calibratedConfidence = state.calibrated_confidence ?? null;
  const knowledgeContext = state.knowledge_context ?? [];
  const remediationPlan = state.remediation_plan ?? null;
  const remediationOutcome = state.remediation_outcome ?? null;
  const isCompleted = detail?.status === "completed";
  const isTerminal = detail ? TERMINAL.has(detail.status) : false;

  async function decide(decision: "approve" | "reject", index: number) {
    if (!id) return;
    try {
      await decideRemediation(id, decision, index);
      setDetail(await getInvestigation(id)); // refresh to show the new status
    } catch (e) {
      setError(e instanceof Error ? e.message : "approval failed");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-semibold">
            {id?.slice(0, 8)}
          </h1>
          {detail && (
            <p className="text-sm text-neutral-500">{detail.query}</p>
          )}
        </div>
        {detail && <StatusBadge status={detail.status} />}
      </div>

      <Link
        href="/investigations"
        className="text-sm text-blue-700 hover:underline"
      >
        ← Back to all investigations
      </Link>

      {error && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {detail && (
        <Card>
          <CardBody className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <Meta label="Namespace" value={detail.namespace} />
            <Meta label="Service" value={detail.service || "—"} />
            <Meta label="Created" value={fmt(detail.created_at)} />
            <Meta label="Updated" value={fmt(detail.updated_at)} />
          </CardBody>
        </Card>
      )}

      {/* Live progress */}
      <Card>
        <CardHeader>
          <CardTitle>
            Agent Progress
            {!isTerminal && detail && (
              <span className="ml-2 text-xs font-normal text-blue-600">
                (live)
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardBody>
          {progress.length === 0 ? (
            <p className="text-sm text-neutral-500">
              {isTerminal
                ? "No streamed progress (investigation already finished)."
                : "Waiting for events…"}
            </p>
          ) : (
            <ol className="space-y-1 text-sm">
              {progress.map((p) => (
                <li key={p.key} className="flex gap-2">
                  <span className="text-neutral-400">•</span>
                  <span>{p.label}</span>
                  {p.detail && (
                    <span className="text-neutral-500">— {p.detail}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </CardBody>
      </Card>

      {detail?.error && (
        <Card className="border-red-200">
          <CardHeader>
            <CardTitle>Error</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-red-700">{detail.error}</p>
          </CardBody>
        </Card>
      )}

      {/* Escalate-to-human banner (Phase 3 critic) */}
      {critique?.escalate_to_human && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          ⚠️ <span className="font-semibold">Escalated to human review.</span> The
          critic&apos;s confidence in this finding is low — verify before acting.
        </div>
      )}

      {/* RCA report */}
      {rca && (
        <Card>
          <CardHeader>
            <CardTitle>Root Cause Analysis</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-700">
                {rca.root_cause_category}
              </span>
              <span className="text-sm text-neutral-600">
                Confidence:{" "}
                <span className="font-semibold text-neutral-900">
                  {Math.round((rca.confidence ?? 0) * 100)}%
                </span>
              </span>
              {calibratedConfidence != null && (
                <span className="text-sm text-neutral-600">
                  Calibrated:{" "}
                  <span className="font-semibold text-neutral-900">
                    {Math.round(calibratedConfidence * 100)}%
                  </span>
                </span>
              )}
            </div>
            <div>
              <h4 className="text-sm font-semibold text-neutral-800">
                Root cause
              </h4>
              <p className="text-sm text-neutral-700">{rca.root_cause}</p>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-neutral-800">
                Reasoning
              </h4>
              <p className="whitespace-pre-wrap text-sm text-neutral-700">
                {rca.reasoning}
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      {/* Critic review (Phase 3) */}
      {critique && (
        <Card>
          <CardHeader>
            <CardTitle>Critic Review</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <div className="flex flex-wrap items-center gap-3 text-sm text-neutral-600">
              <span>
                Agreement:{" "}
                <span className="font-semibold text-neutral-900">
                  {Math.round((critique.agreement ?? 0) * 100)}%
                </span>
              </span>
              {critique.escalate_to_human && (
                <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  escalate to human
                </span>
              )}
            </div>
            {critique.concerns.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-neutral-800">
                  Concerns
                </h4>
                <ul className="list-disc space-y-1 pl-5 text-sm text-neutral-700">
                  {critique.concerns.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {/* Cluster knowledge (Phase 3) */}
      {knowledgeContext.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Cluster Knowledge ({knowledgeContext.length})</CardTitle>
          </CardHeader>
          <CardBody className="space-y-2 text-sm">
            {knowledgeContext.map((k, i) => (
              <div key={i} className="rounded border border-neutral-200 px-3 py-2">
                <div className="font-mono font-medium text-neutral-900">
                  {k.service}
                </div>
                <div className="text-neutral-600">
                  {k.owner && <span>owner: {k.owner} · </span>}
                  {k.dependencies.length > 0 && (
                    <span>depends on: {k.dependencies.join(", ")} · </span>
                  )}
                  {k.dependents.length > 0 && (
                    <span>depended on by: {k.dependents.join(", ")}</span>
                  )}
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* Incident timeline */}
      {isCompleted && timeline.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Timeline ({timeline.length})</CardTitle>
          </CardHeader>
          <CardBody>
            <ol className="space-y-3">
              {timeline.map((t, i) => (
                <li key={i} className="flex gap-3">
                  <span className="mt-0.5 shrink-0 font-mono text-xs text-neutral-400">
                    {fmtTime(t.at)}
                  </span>
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-700">
                        {t.label}
                      </span>
                      <SeverityBadge severity={t.severity} />
                      {t.source && (
                        <span className="text-[11px] text-neutral-400">
                          {t.source}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-neutral-700">{t.description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </CardBody>
        </Card>
      )}

      {/* Similar past incidents (long-term memory) */}
      {isCompleted && memoryContext.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Similar past incidents ({memoryContext.length})</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <p className="text-xs text-neutral-500">
              Corroborating context retrieved from long-term memory — not part of
              this investigation&apos;s evidence.
            </p>
            {memoryContext.map((m, i) => (
              <div
                key={m.incident_id || i}
                className="rounded-md border border-neutral-200 p-3"
              >
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-700">
                    {Math.round((m.similarity ?? 0) * 100)}% match
                  </span>
                  {m.root_cause_category && (
                    <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600">
                      {m.root_cause_category}
                    </span>
                  )}
                  {(m.namespace || m.service) && (
                    <span className="text-[11px] text-neutral-400">
                      {[m.namespace, m.service].filter(Boolean).join(" / ")}
                    </span>
                  )}
                  {m.occurred_at && (
                    <span className="text-[11px] text-neutral-400">
                      {fmt(m.occurred_at)}
                    </span>
                  )}
                </div>
                <p className="text-sm text-neutral-800">{m.summary}</p>
                {m.outcome && (
                  <p className="mt-1 text-xs text-neutral-500">
                    Outcome: {m.outcome}
                  </p>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* Evidence */}
      {evidence.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Evidence ({evidence.length})</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            {evidence.map((e, i) => (
              <div
                key={i}
                className="rounded-md border border-neutral-200 p-3"
              >
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-mono text-neutral-400">
                    #{i}
                  </span>
                  <span className="text-xs font-medium text-neutral-600">
                    {e.source_agent}
                  </span>
                  <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600">
                    {e.kind}
                  </span>
                  <SeverityBadge severity={e.severity} />
                </div>
                <p className="text-sm text-neutral-800">{e.summary}</p>
                {e.detail && Object.keys(e.detail).length > 0 && (
                  <p className="mt-1 whitespace-pre-wrap font-mono text-xs text-neutral-500">
                    {Object.entries(e.detail)
                      .map(
                        ([k, v]) =>
                          `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`,
                      )
                      .join("  ·  ")}
                  </p>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* Remediation approval (Phase 4 — HITL) */}
      {remediationPlan && remediationPlan.actions.length > 0 && (
        <Card className="border-amber-200">
          <CardHeader>
            <CardTitle>
              Remediation Approval{" "}
              <span className="ml-1 rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                {remediationOutcome ?? "pending_approval"}
              </span>
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-3 text-sm">
            <p className="text-neutral-600">
              These actions require explicit approval before KubePilot executes
              them. Reversible actions are preferred; nothing runs until approved.
            </p>
            {remediationPlan.actions.map((a, i) => (
              <div
                key={i}
                className="rounded border border-neutral-200 px-3 py-2"
              >
                <div className="font-mono font-medium text-neutral-900">
                  {a.tool} → {a.target}{" "}
                  <span className="text-xs font-normal text-neutral-500">
                    ({a.reversibility}, approve: {a.approval_tier})
                  </span>
                </div>
                {a.rationale && (
                  <div className="text-neutral-700">{a.rationale}</div>
                )}
                {a.estimated_blast_radius && (
                  <div className="text-xs text-neutral-500">
                    blast radius: ~{a.estimated_blast_radius.pods_affected ?? "?"}{" "}
                    pod(s), ~{a.estimated_blast_radius.traffic_percent ?? "?"}%
                    traffic
                    {a.estimated_blast_radius.dependents &&
                    a.estimated_blast_radius.dependents.length > 0
                      ? ` · dependents: ${a.estimated_blast_radius.dependents.join(", ")}`
                      : ""}
                  </div>
                )}
                {remediationOutcome === "pending_approval" && (
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() => decide("approve", i)}
                      className="rounded bg-green-600 px-3 py-1 text-xs font-medium text-white hover:bg-green-700"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => decide("reject", i)}
                      className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Recommendations ({recommendations.length})</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            {recommendations.map((r, i) => (
              <div
                key={i}
                className="rounded-md border border-neutral-200 p-3"
              >
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  {typeof r.priority === "number" && (
                    <span className="text-xs font-mono text-neutral-400">
                      #{r.priority}
                    </span>
                  )}
                  <h4 className="text-sm font-semibold text-neutral-900">
                    {r.title}
                  </h4>
                  <span className="rounded border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-[11px] text-neutral-600">
                    risk: {r.risk}
                  </span>
                  {r.requires_approval && (
                    <span className="rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-800">
                      requires approval
                    </span>
                  )}
                </div>
                <p className="text-sm text-neutral-700">{r.rationale}</p>
                {r.commands && r.commands.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {r.commands.map((cmd, j) => (
                      <Code key={j}>{cmd}</Code>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-neutral-400">{label}</div>
      <div className="text-neutral-800">{value}</div>
    </div>
  );
}
