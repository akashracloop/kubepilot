"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createInvestigation } from "@/lib/api";
import {
  Banner,
  Button,
  Card,
  CardBody,
  Field,
  Input,
  PageHeader,
  Spinner,
  Textarea,
} from "@/components/ui";
import { Icon } from "@/components/icons";

const EXAMPLES = [
  "Why is payment-service returning 5xx?",
  "checkout pods are restarting in prod",
  "latency spiked after the last deploy",
];

export default function NewInvestigationPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [namespace, setNamespace] = useState("");
  const [service, setService] = useState("");
  const [timeWindow, setTimeWindow] = useState(30);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await createInvestigation({
        query: query.trim(),
        namespace: namespace.trim(),
        service: service.trim() || undefined,
        time_window_minutes: Number(timeWindow) || 30,
      });
      router.push(`/investigations/${res.incident_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="New investigation"
        description="Describe the symptom. The multi-agent RCA loop correlates signals across the cluster API, Prometheus, Loki, Tempo and CI to find the root cause."
      />

      <Card>
        <CardBody className="p-5">
          <form onSubmit={onSubmit} className="space-y-5">
            <Field
              label="What's going wrong?"
              htmlFor="query"
              hint="natural language"
            >
              <Textarea
                id="query"
                required
                rows={3}
                placeholder="Why is payment-service failing?"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setQuery(ex)}
                    className="rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-muted transition-colors hover:border-brand-500 hover:text-brand-700"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Namespace" htmlFor="namespace">
                <Input
                  id="namespace"
                  required
                  placeholder="default"
                  value={namespace}
                  onChange={(e) => setNamespace(e.target.value)}
                />
              </Field>
              <Field label="Service" htmlFor="service" hint="optional">
                <Input
                  id="service"
                  placeholder="payment-service"
                  value={service}
                  onChange={(e) => setService(e.target.value)}
                />
              </Field>
            </div>

            <Field label="Time window" htmlFor="time_window" hint="minutes">
              <Input
                id="time_window"
                type="number"
                min={1}
                max={1440}
                value={timeWindow}
                onChange={(e) => setTimeWindow(Number(e.target.value))}
              />
            </Field>

            {error && <Banner tone="red">{error}</Banner>}

            <div className="flex items-center justify-end border-t border-line-soft pt-4">
              <Button type="submit" disabled={submitting || !query.trim()}>
                {submitting ? (
                  <>
                    <Spinner /> Starting…
                  </>
                ) : (
                  <>
                    <Icon.Search size={15} /> Start investigation
                  </>
                )}
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
