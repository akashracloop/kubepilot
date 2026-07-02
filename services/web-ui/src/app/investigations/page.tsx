"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { listInvestigations, type InvestigationDetail } from "@/lib/api";
import { Button, Card, EmptyState, PageHeader, Spinner, Banner } from "@/components/ui";
import { StatusBadge } from "@/components/StatusBadge";
import { Icon } from "@/components/icons";

const PAGE_SIZE = 20;

function shortId(id: string): string {
  return id.slice(0, 8);
}

function fmt(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function InvestigationsPage() {
  const [items, setItems] = useState<InvestigationDetail[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listInvestigations(PAGE_SIZE, nextOffset);
      setItems(res.items);
      setOffset(nextOffset);
      setHasMore(res.items.length === PAGE_SIZE);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(0);
  }, [load]);

  return (
    <div>
      <PageHeader
        title="Investigations"
        description="Every incident KubePilot has investigated."
        actions={
          <Link href="/">
            <Button>
              <Icon.Plus size={15} /> New
            </Button>
          </Link>
        }
      />

      {error && (
        <div className="mb-4">
          <Banner tone="red">{error}</Banner>
        </div>
      )}

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead>
              <tr className="border-b border-line-soft text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
                <th className="px-4 py-2.5">Incident</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5">Query</th>
                <th className="px-4 py-2.5">Namespace</th>
                <th className="px-4 py-2.5">Created</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.incident_id}
                  className="group border-b border-line-soft last:border-0 hover:bg-canvas"
                >
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/investigations/${it.incident_id}`}
                      className="font-mono text-[12px] text-brand-700 hover:underline"
                    >
                      {shortId(it.incident_id)}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={it.status} />
                  </td>
                  <td
                    className="max-w-xs truncate px-4 py-2.5 text-ink"
                    title={it.query}
                  >
                    {it.query}
                  </td>
                  <td className="px-4 py-2.5 text-ink-muted">{it.namespace}</td>
                  <td className="px-4 py-2.5 text-ink-subtle">{fmt(it.created_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <Link
                      href={`/investigations/${it.incident_id}`}
                      className="inline-flex text-ink-subtle opacity-0 transition-opacity group-hover:opacity-100"
                    >
                      <Icon.ChevronRight size={16} />
                    </Link>
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={6}>
                    <EmptyState
                      icon={<Icon.List size={22} />}
                      title="No investigations yet"
                      hint="Start one from New Investigation to see it here."
                    />
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-ink-subtle">
                    <Spinner /> <span className="ml-1 text-[13px]">Loading…</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="mt-4 flex items-center justify-between">
        <Button
          variant="subtle"
          size="sm"
          onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
          disabled={loading || offset === 0}
        >
          Previous
        </Button>
        <span className="text-xs text-ink-subtle">
          Showing {items.length ? offset + 1 : 0}–{offset + items.length}
        </span>
        <Button
          variant="subtle"
          size="sm"
          onClick={() => load(offset + PAGE_SIZE)}
          disabled={loading || !hasMore}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
