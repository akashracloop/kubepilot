"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createInvestigation } from "@/lib/api";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Textarea,
} from "@/components/ui";

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
      <Card>
        <CardHeader>
          <CardTitle>Trigger an Investigation</CardTitle>
        </CardHeader>
        <CardBody>
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <Label htmlFor="query">Query</Label>
              <Textarea
                id="query"
                required
                rows={3}
                placeholder="Why is payment-service failing?"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="namespace">Namespace</Label>
              <Input
                id="namespace"
                required
                placeholder="default"
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="service">Service (optional)</Label>
              <Input
                id="service"
                placeholder="payment-service"
                value={service}
                onChange={(e) => setService(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="time_window">Time window (minutes)</Label>
              <Input
                id="time_window"
                type="number"
                min={1}
                max={1440}
                value={timeWindow}
                onChange={(e) => setTimeWindow(Number(e.target.value))}
              />
            </div>

            {error && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
                {error}
              </p>
            )}

            <Button type="submit" disabled={submitting}>
              {submitting ? "Starting…" : "Start Investigation"}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
