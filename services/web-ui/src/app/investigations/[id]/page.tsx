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
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Code,
  Meta,
  Spinner,
} from "@/components/ui";
import { SeverityBadge, StatusBadge } from "@/components/StatusBadge";
import { Icon } from "@/components/icons";

interface ProgressLine {
  key: string;
  label: string;
  done: boolean;
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

  function pushProgress(label: string, done = true) {
    seq.current += 1;
    setProgress((prev) => [...prev, { key: `${Date.now()}-${seq.current}`, label, done }]);
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
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
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
        case "node_completed":
          pushProgress(`${(data.node as string) || "node"} completed`);
          return;
        case "investigation_awaiting_approval":
          pushProgress("Awaiting approval");
          finalize();
          return;
        case "investigation_resumed":
          pushProgress("Resumed after approval");
          return;
        case "investigation_completed":
          pushProgress("Investigation completed");
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
      try {
        const snap = await getInvestigation(id);
        if (cancelled) return;
        setDetail(snap);
        if (TERMINAL.has(snap.status)) return;
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
      try {
        await streamInvestigation(id, onEvent, controller.signal);
      } catch (err) {
        if (!cancelled && !controller.signal.aborted) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
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
      setDetail(await getInvestigation(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "approval failed");
    }
  }

  return (
    <div>
      <Link
        href="/investigations"
        className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-ink-muted hover:text-ink"
      >
        <Icon.ArrowLeft size={15} /> All investigations
      </Link>

      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <h1 className="font-mono text-lg font-semibold text-ink">{id?.slice(0, 8)}</h1>
            {detail && <StatusBadge status={detail.status} />}
          </div>
          {detail && <p className="mt-1 text-[13px] text-ink-muted">{detail.query}</p>}
        </div>
      </div>

      {error && (
        <div className="mb-4">
          <Banner tone="red">{error}</Banner>
        </div>
      )}

      {detail && (
        <Card className="mb-4">
          <CardBody className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
            <Meta label="Namespace" value={detail.namespace} />
            <Meta label="Service" value={detail.service || "—"} />
            <Meta label="Created" value={fmt(detail.created_at)} />
            <Meta label="Updated" value={fmt(detail.updated_at)} />
          </CardBody>
        </Card>
      )}

      {/* Escalate-to-human banner */}
      {critique?.escalate_to_human && (
        <div className="mb-4">
          <Banner tone="amber">
            <Icon.Alert size={16} className="mt-0.5 shrink-0" />
            <span>
              <span className="font-semibold">Escalated to human review.</span> The critic&apos;s
              confidence in this finding is low — verify before acting.
            </span>
          </Banner>
        </div>
      )}

      <div className="space-y-4">
        {/* Live progress */}
        <Card>
          <CardHeader
            actions={
              !isTerminal && detail ? (
                <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-brand-600">
                  <Spinner className="h-3 w-3" /> live
                </span>
              ) : null
            }
          >
            <CardTitle>Agent progress</CardTitle>
          </CardHeader>
          <CardBody>
            {progress.length === 0 ? (
              <p className="text-[13px] text-ink-subtle">
                {isTerminal
                  ? "No streamed progress (investigation already finished)."
                  : "Waiting for events…"}
              </p>
            ) : (
              <ol className="space-y-2">
                {progress.map((p) => (
                  <li key={p.key} className="flex animate-fade-in items-center gap-2.5 text-[13px]">
                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-50 text-emerald-600">
                      <Icon.Check size={11} />
                    </span>
                    <span className="text-ink">{p.label}</span>
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
              <p className="text-[13px] text-red-700">{detail.error}</p>
            </CardBody>
          </Card>
        )}

        {/* RCA */}
        {rca && (
          <Card>
            <CardHeader>
              <CardTitle>
                <span className="inline-flex items-center gap-2">
                  <Icon.Brain size={15} className="text-brand-600" /> Root cause analysis
                </span>
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="violet">{rca.root_cause_category}</Badge>
                <ConfidenceBar
                  label="Confidence"
                  value={rca.confidence ?? 0}
                />
                {calibratedConfidence != null && (
                  <ConfidenceBar label="Calibrated" value={calibratedConfidence} />
                )}
              </div>
              <Section title="Root cause">
                <p className="text-[13px] leading-relaxed text-ink">{rca.root_cause}</p>
              </Section>
              <Section title="Reasoning">
                <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">
                  {rca.reasoning}
                </p>
              </Section>
            </CardBody>
          </Card>
        )}

        {/* Critic */}
        {critique && (
          <Card>
            <CardHeader>
              <CardTitle>Critic review</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <ConfidenceBar label="Agreement" value={critique.agreement ?? 0} />
                {critique.escalate_to_human && <Badge tone="amber">escalate to human</Badge>}
              </div>
              {critique.concerns.length > 0 && (
                <Section title="Concerns">
                  <ul className="space-y-1.5 text-[13px] text-ink-muted">
                    {critique.concerns.map((c, i) => (
                      <li key={i} className="flex gap-2">
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-subtle" />
                        {c}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </CardBody>
          </Card>
        )}

        {/* Remediation approval (Phase 4) */}
        {remediationPlan && remediationPlan.actions.length > 0 && (
          <Card className="border-amber-200 ring-1 ring-amber-100">
            <CardHeader
              actions={<StatusBadge status={remediationOutcome ?? "pending_approval"} />}
            >
              <CardTitle>
                <span className="inline-flex items-center gap-2">
                  <Icon.Shield size={15} className="text-amber-600" /> Remediation approval
                </span>
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <p className="text-[13px] text-ink-muted">
                These actions require explicit approval before KubePilot executes them. Nothing
                runs until approved.
              </p>
              {remediationPlan.actions.map((a, i) => (
                <div key={i} className="rounded-lg border border-line bg-canvas/40 px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[13px] font-medium text-ink">{a.tool}</span>
                    <Icon.ChevronRight size={13} className="text-ink-subtle" />
                    <span className="font-mono text-[13px] text-ink-muted">{a.target}</span>
                    <Badge tone={a.reversibility === "reversible" ? "green" : "amber"}>
                      {a.reversibility}
                    </Badge>
                    <Badge tone="neutral">approve: {a.approval_tier}</Badge>
                  </div>
                  {a.rationale && (
                    <p className="mt-1.5 text-[13px] text-ink-muted">{a.rationale}</p>
                  )}
                  {a.estimated_blast_radius && (
                    <p className="mt-1.5 text-xs text-ink-subtle">
                      blast radius: ~{a.estimated_blast_radius.pods_affected ?? "?"} pod(s), ~
                      {a.estimated_blast_radius.traffic_percent ?? "?"}% traffic
                      {a.estimated_blast_radius.dependents &&
                      a.estimated_blast_radius.dependents.length > 0
                        ? ` · dependents: ${a.estimated_blast_radius.dependents.join(", ")}`
                        : ""}
                    </p>
                  )}
                  {remediationOutcome === "pending_approval" && (
                    <div className="mt-2.5 flex gap-2">
                      <Button size="sm" variant="success" onClick={() => decide("approve", i)}>
                        <Icon.Check size={13} /> Approve
                      </Button>
                      <Button size="sm" variant="danger" onClick={() => decide("reject", i)}>
                        <Icon.X size={13} /> Reject
                      </Button>
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
            <CardBody className="space-y-3">
              {recommendations.map((r, i) => (
                <div key={i} className="rounded-lg border border-line px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    {typeof r.priority === "number" && (
                      <span className="font-mono text-[11px] text-ink-subtle">#{r.priority}</span>
                    )}
                    <h4 className="text-[13px] font-semibold text-ink">{r.title}</h4>
                    <Badge tone="neutral">risk: {r.risk}</Badge>
                    {r.requires_approval && <Badge tone="amber">requires approval</Badge>}
                  </div>
                  <p className="mt-1.5 text-[13px] text-ink-muted">{r.rationale}</p>
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

        {/* Cluster knowledge */}
        {knowledgeContext.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Cluster knowledge ({knowledgeContext.length})</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2">
              {knowledgeContext.map((k, i) => (
                <div key={i} className="rounded-lg border border-line px-3.5 py-2.5">
                  <div className="font-mono text-[13px] font-medium text-ink">{k.service}</div>
                  <div className="mt-0.5 text-xs text-ink-muted">
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

        {/* Timeline */}
        {isCompleted && timeline.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>
                <span className="inline-flex items-center gap-2">
                  <Icon.Clock size={15} className="text-ink-subtle" /> Timeline ({timeline.length})
                </span>
              </CardTitle>
            </CardHeader>
            <CardBody>
              <ol className="relative space-y-4 border-l border-line pl-4">
                {timeline.map((t, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full border-2 border-surface bg-brand-500" />
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-ink-subtle">{fmtTime(t.at)}</span>
                      <Badge tone="neutral">{t.label}</Badge>
                      <SeverityBadge severity={t.severity} />
                      {t.source && <span className="text-[11px] text-ink-subtle">{t.source}</span>}
                    </div>
                    <p className="mt-1 text-[13px] text-ink-muted">{t.description}</p>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
        )}

        {/* Similar past incidents */}
        {isCompleted && memoryContext.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Similar past incidents ({memoryContext.length})</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <p className="text-xs text-ink-subtle">
                Corroborating context from long-term memory — not part of this investigation&apos;s
                evidence.
              </p>
              {memoryContext.map((m, i) => (
                <div key={m.incident_id || i} className="rounded-lg border border-line px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="brand">{Math.round((m.similarity ?? 0) * 100)}% match</Badge>
                    {m.root_cause_category && <Badge tone="neutral">{m.root_cause_category}</Badge>}
                    {(m.namespace || m.service) && (
                      <span className="text-[11px] text-ink-subtle">
                        {[m.namespace, m.service].filter(Boolean).join(" / ")}
                      </span>
                    )}
                    {m.occurred_at && (
                      <span className="text-[11px] text-ink-subtle">{fmt(m.occurred_at)}</span>
                    )}
                  </div>
                  <p className="mt-1.5 text-[13px] text-ink">{m.summary}</p>
                  {m.outcome && (
                    <p className="mt-1 text-xs text-ink-subtle">Outcome: {m.outcome}</p>
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
            <CardBody className="space-y-2.5">
              {evidence.map((e, i) => (
                <div key={i} className="rounded-lg border border-line px-3.5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[11px] text-ink-subtle">#{i}</span>
                    <span className="text-xs font-medium text-ink-muted">{e.source_agent}</span>
                    <Badge tone="neutral">{e.kind}</Badge>
                    <SeverityBadge severity={e.severity} />
                  </div>
                  <p className="mt-1.5 text-[13px] text-ink">{e.summary}</p>
                  {e.detail && Object.keys(e.detail).length > 0 && (
                    <p className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-ink-subtle">
                      {Object.entries(e.detail)
                        .map(
                          ([k, v]) =>
                            `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`
                        )
                        .join("  ·  ")}
                    </p>
                  )}
                </div>
              ))}
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ink-subtle">
        {title}
      </h4>
      {children}
    </div>
  );
}

function ConfidenceBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round((value ?? 0) * 100);
  const tone = pct >= 70 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";
  return (
    <span className="inline-flex items-center gap-2 text-xs text-ink-muted">
      {label}
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-line">
        <span className={`block h-full ${tone}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="font-semibold text-ink">{pct}%</span>
    </span>
  );
}
