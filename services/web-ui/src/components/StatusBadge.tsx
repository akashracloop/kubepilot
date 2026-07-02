import { cn } from "./ui";

const STYLES: Record<string, string> = {
  pending: "bg-neutral-100 text-neutral-700 border-neutral-200",
  running: "bg-blue-50 text-blue-700 border-blue-200",
  completed: "bg-green-50 text-green-700 border-green-200",
  failed: "bg-red-50 text-red-700 border-red-200",
};

export function StatusBadge({ status }: { status: string }) {
  const cls = STYLES[status] ?? "bg-neutral-100 text-neutral-700 border-neutral-200";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium capitalize",
        cls
      )}
    >
      {status}
    </span>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border-red-200",
  high: "bg-orange-100 text-orange-800 border-orange-200",
  warning: "bg-yellow-100 text-yellow-800 border-yellow-200",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
  info: "bg-blue-50 text-blue-700 border-blue-200",
  low: "bg-neutral-100 text-neutral-700 border-neutral-200",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const key = (severity || "").toLowerCase();
  const cls =
    SEVERITY_STYLES[key] ?? "bg-neutral-100 text-neutral-700 border-neutral-200";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium capitalize",
        cls
      )}
    >
      {severity || "unknown"}
    </span>
  );
}
