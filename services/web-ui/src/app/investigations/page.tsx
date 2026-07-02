"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  listInvestigations,
  type InvestigationDetail,
} from "@/lib/api";
import { Button, Card } from "@/components/ui";
import { StatusBadge } from "@/components/StatusBadge";

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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Investigations</h1>
        <Link href="/">
          <Button>New Investigation</Button>
        </Link>
      </div>

      {error && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-neutral-200 text-xs uppercase text-neutral-500">
              <tr>
                <th className="px-4 py-2 font-medium">Incident</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Query</th>
                <th className="px-4 py-2 font-medium">Namespace</th>
                <th className="px-4 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.incident_id}
                  className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50"
                >
                  <td className="px-4 py-2">
                    <Link
                      href={`/investigations/${it.incident_id}`}
                      className="font-mono text-blue-700 hover:underline"
                    >
                      {shortId(it.incident_id)}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={it.status} />
                  </td>
                  <td className="max-w-xs truncate px-4 py-2" title={it.query}>
                    {it.query}
                  </td>
                  <td className="px-4 py-2">{it.namespace}</td>
                  <td className="px-4 py-2 text-neutral-500">
                    {fmt(it.created_at)}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-neutral-500"
                  >
                    No investigations yet.
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-neutral-500"
                  >
                    Loading…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="flex items-center justify-between">
        <Button
          onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
          disabled={loading || offset === 0}
        >
          Previous
        </Button>
        <span className="text-sm text-neutral-500">
          Showing {items.length ? offset + 1 : 0}–{offset + items.length}
        </span>
        <Button
          onClick={() => load(offset + PAGE_SIZE)}
          disabled={loading || !hasMore}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
