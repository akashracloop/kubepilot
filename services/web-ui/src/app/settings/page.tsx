"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAdminKey,
  getSettings,
  putSettings,
  setAdminKey,
  setKillSwitch,
  type SettingField,
  type SettingsResponse,
} from "@/lib/api";
import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Input,
  Meta,
  PageHeader,
  Select,
  Spinner,
  Toggle,
} from "@/components/ui";
import { Icon, type IconName } from "@/components/icons";

const GROUP_META: Record<string, { title: string; icon: IconName; hint: string }> = {
  features: { title: "Features", icon: "Sliders", hint: "Turn investigation capabilities on or off." },
  llm: { title: "LLM routing", icon: "Cpu", hint: "Which provider + model answers each role." },
  remediation: { title: "Remediation", icon: "Shield", hint: "The HITL-gated write path." },
  prompts: { title: "Prompts & thresholds", icon: "Book", hint: "Prompt version pins (A/B + rollback)." },
};
const GROUP_ORDER = ["features", "llm", "remediation", "prompts"];

type Draft = Record<string, unknown>;

export default function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adminKey, setAdminKeyState] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getSettings();
      setData(res);
      const base: Draft = {};
      for (const fields of Object.values(res.groups))
        for (const f of fields) base[f.key] = f.value;
      setDraft(base);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setAdminKeyState(getAdminKey());
    load();
  }, [load]);

  const dirtyKeys = useMemo(() => {
    if (!data) return [] as string[];
    const out: string[] = [];
    for (const fields of Object.values(data.groups))
      for (const f of fields)
        if (JSON.stringify(draft[f.key]) !== JSON.stringify(f.value)) out.push(f.key);
    return out;
  }, [data, draft]);

  function set(key: string, value: unknown) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setNotice(null);
    const overrides: Draft = {};
    for (const k of dirtyKeys) overrides[k] = draft[k];
    try {
      const res = await putSettings(overrides);
      setNotice(res.rebuilt ? "Saved — applied to new investigations." : "Saved.");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function toggleKill(enabled: boolean) {
    try {
      const res = await setKillSwitch(enabled);
      setData((d) => (d ? { ...d, kill_switch: res.kill_switch } : d));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function saveAdminKey(k: string) {
    setAdminKey(k);
    setAdminKeyState(k);
    load();
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-16 text-ink-subtle">
        <Spinner /> <span className="text-[13px]">Loading settings…</span>
      </div>
    );
  }

  return (
    <div className="pb-20">
      <PageHeader
        title="Settings"
        description="Live configuration — changes apply to new investigations. Secrets and infrastructure stay environment-managed."
      />

      {error && (
        <div className="mb-4">
          <Banner tone="red">{error}</Banner>
        </div>
      )}
      {notice && (
        <div className="mb-4">
          <Banner tone="green">
            <Icon.Check size={16} className="mt-0.5 shrink-0" /> {notice}
          </Banner>
        </div>
      )}

      {/* Admin access */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle>Admin access</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2">
          <p className="text-[13px] text-ink-muted">
            Changing settings requires an <span className="font-medium">admin</span> API key. It is
            stored in your browser only and sent with settings requests.
          </p>
          <div className="flex gap-2">
            <Input
              type="password"
              placeholder="admin API key"
              defaultValue={adminKey}
              onBlur={(e) => saveAdminKey(e.target.value.trim())}
              className="max-w-xs font-mono"
            />
            {adminKey ? (
              <Badge tone="green">
                <Icon.Check size={12} /> key set
              </Badge>
            ) : (
              <Badge tone="amber">not set</Badge>
            )}
          </div>
        </CardBody>
      </Card>

      <div className="space-y-4">
        {GROUP_ORDER.filter((g) => data?.groups[g]).map((group) => {
          const meta = GROUP_META[group];
          const fields = data!.groups[group];
          const GroupIcon = Icon[meta.icon];
          return (
            <Card key={group}>
              <CardHeader>
                <CardTitle>
                  <span className="inline-flex items-center gap-2">
                    <GroupIcon size={15} className="text-ink-subtle" /> {meta.title}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardBody className="divide-y divide-line-soft">
                {fields.map((f) => (
                  <FieldRow key={f.key} field={f} value={draft[f.key]} onChange={set} />
                ))}
              </CardBody>
            </Card>
          );
        })}

        {/* Kill switch */}
        <Card className="border-red-200">
          <CardHeader>
            <CardTitle>
              <span className="inline-flex items-center gap-2">
                <Icon.Alert size={15} className="text-red-500" /> Remediation kill switch
              </span>
            </CardTitle>
          </CardHeader>
          <CardBody>
            <div className="flex items-center justify-between gap-4">
              <p className="text-[13px] text-ink-muted">
                Halts <span className="font-medium">all</span> remediation execution immediately.
                Every executor run checks it first.
              </p>
              <div className="flex items-center gap-2">
                {data?.kill_switch && <Badge tone="red">engaged</Badge>}
                <Toggle checked={!!data?.kill_switch} onChange={toggleKill} />
              </div>
            </div>
          </CardBody>
        </Card>

        {/* Read-only facts */}
        {data?.readonly && data.readonly.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Environment (read-only)</CardTitle>
            </CardHeader>
            <CardBody className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
              {data.readonly.map((r) => (
                <Meta key={r.label} label={r.label} value={r.value} />
              ))}
            </CardBody>
          </Card>
        )}
      </div>

      {/* Sticky save bar */}
      {dirtyKeys.length > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-10 border-t border-line bg-surface/90 backdrop-blur">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-3">
            <span className="text-[13px] text-ink-muted">
              {dirtyKeys.length} unsaved change{dirtyKeys.length > 1 ? "s" : ""}
            </span>
            <div className="flex gap-2">
              <Button variant="subtle" onClick={() => load()} disabled={saving}>
                Discard
              </Button>
              <Button onClick={save} disabled={saving}>
                {saving ? (
                  <>
                    <Spinner /> Saving…
                  </>
                ) : (
                  "Save changes"
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FieldRow({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
}) {
  const control = (() => {
    switch (field.kind) {
      case "bool":
        return (
          <Toggle checked={Boolean(value)} onChange={(v) => onChange(field.key, v)} />
        );
      case "select":
        return (
          <Select
            value={String(value ?? "")}
            onChange={(e) => onChange(field.key, e.target.value)}
            className="max-w-[220px]"
          >
            {(field.options ?? []).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </Select>
        );
      case "json":
        return (
          <Input
            value={typeof value === "string" ? value : JSON.stringify(value ?? {})}
            onChange={(e) => {
              try {
                onChange(field.key, JSON.parse(e.target.value || "{}"));
              } catch {
                onChange(field.key, e.target.value);
              }
            }}
            className="max-w-[320px] font-mono text-xs"
            placeholder='{"rca_agent": "v1"}'
          />
        );
      default:
        return (
          <Input
            value={String(value ?? "")}
            onChange={(e) => onChange(field.key, e.target.value)}
            className="max-w-[320px]"
            placeholder={field.options ? field.options.join(", ") : undefined}
          />
        );
    }
  })();

  return (
    <div className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium text-ink">{field.label}</span>
          {field.overridden && <Badge tone="brand">overridden</Badge>}
          {field.restart_required && <Badge tone="amber">restart</Badge>}
        </div>
        {field.help && <p className="mt-0.5 max-w-xl text-xs text-ink-muted">{field.help}</p>}
      </div>
      <div className="shrink-0 pt-0.5">{control}</div>
    </div>
  );
}
