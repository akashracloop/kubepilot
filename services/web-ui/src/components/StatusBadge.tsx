import { Badge } from "./ui";

type Tone = "neutral" | "brand" | "green" | "red" | "amber" | "blue" | "violet";

const STATUS_TONE: Record<string, Tone> = {
  pending: "neutral",
  running: "blue",
  pending_approval: "amber",
  completed: "green",
  failed: "red",
};

const STATUS_LABEL: Record<string, string> = {
  pending_approval: "pending approval",
};

const DOT: Record<Tone, string> = {
  neutral: "bg-ink-subtle",
  brand: "bg-brand-500",
  green: "bg-emerald-500",
  red: "bg-red-500",
  amber: "bg-amber-500",
  blue: "bg-blue-500",
  violet: "bg-violet-500",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONE[status] ?? "neutral";
  const label = STATUS_LABEL[status] ?? status;
  return (
    <Badge tone={tone} className="capitalize">
      <span
        className={`h-1.5 w-1.5 rounded-full ${DOT[tone]} ${
          status === "running" ? "animate-pulse" : ""
        }`}
      />
      {label}
    </Badge>
  );
}

const SEVERITY_TONE: Record<string, Tone> = {
  critical: "red",
  high: "amber",
  warning: "amber",
  medium: "amber",
  info: "blue",
  low: "neutral",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const key = (severity || "").toLowerCase();
  return (
    <Badge tone={SEVERITY_TONE[key] ?? "neutral"} className="capitalize">
      {severity || "unknown"}
    </Badge>
  );
}
