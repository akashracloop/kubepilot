import * as React from "react";

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ Card */

export function Card({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-xl border border-line bg-surface shadow-card",
        className
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  className,
  children,
  actions,
}: {
  className?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-line-soft px-4 py-3",
        className
      )}
    >
      <div className="min-w-0">{children}</div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  );
}

export function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[13px] font-semibold tracking-tight text-ink">
      {children}
    </h3>
  );
}

export function CardBody({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn("px-4 py-3.5", className)}>{children}</div>;
}

/* ---------------------------------------------------------------- Button */

type Variant = "solid" | "subtle" | "ghost" | "danger" | "success";
type Size = "sm" | "md";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 disabled:cursor-not-allowed disabled:opacity-50";

const BTN_VARIANT: Record<Variant, string> = {
  solid: "bg-ink text-white hover:bg-ink/90",
  subtle: "bg-line-soft text-ink hover:bg-line",
  ghost: "text-ink-muted hover:bg-line-soft hover:text-ink",
  danger: "bg-red-600 text-white hover:bg-red-700",
  success: "bg-emerald-600 text-white hover:bg-emerald-700",
};

const BTN_SIZE: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-9 px-3.5 text-[13px]",
};

export function Button({
  className,
  variant = "solid",
  size = "md",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
}) {
  return (
    <button
      className={cn(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size], className)}
      {...props}
    />
  );
}

/* ----------------------------------------------------------- Form fields */

const FIELD =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-[13px] text-ink placeholder:text-ink-subtle outline-none transition-shadow focus:border-brand-500 focus:ring-2 focus:ring-brand-500/25 disabled:bg-line-soft disabled:text-ink-subtle";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(FIELD, props.className)} {...props} />;
}

export function Textarea(
  props: React.TextareaHTMLAttributes<HTMLTextAreaElement>
) {
  return <textarea className={cn(FIELD, props.className)} {...props} />;
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(FIELD, "appearance-none pr-8", className)} {...props}>
      {children}
    </select>
  );
}

export function Label({
  htmlFor,
  children,
  hint,
}: {
  htmlFor?: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block">
      <span className="text-[13px] font-medium text-ink">{children}</span>
      {hint && <span className="ml-2 text-xs text-ink-subtle">{hint}</span>}
    </label>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label htmlFor={htmlFor} hint={hint}>
        {label}
      </Label>
      {children}
    </div>
  );
}

/* ---------------------------------------------------------------- Toggle */

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
  description,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
  description?: string;
}) {
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 disabled:opacity-50",
        checked ? "bg-brand-600" : "bg-line"
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
  if (!label) return control;
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        {description && (
          <div className="mt-0.5 text-xs text-ink-muted">{description}</div>
        )}
      </div>
      <div className="pt-0.5">{control}</div>
    </div>
  );
}

/* ----------------------------------------------------------------- Badge */

type Tone =
  | "neutral"
  | "brand"
  | "green"
  | "red"
  | "amber"
  | "blue"
  | "violet";

const TONE: Record<Tone, string> = {
  neutral: "bg-line-soft text-ink-muted ring-line",
  brand: "bg-brand-50 text-brand-700 ring-brand-100",
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  blue: "bg-blue-50 text-blue-700 ring-blue-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        TONE[tone],
        className
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ Code */

export function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="overflow-x-auto rounded-md bg-[#1f272e] px-3 py-2 text-xs leading-relaxed text-gray-100">
      <code className="font-mono">{children}</code>
    </pre>
  );
}

/* -------------------------------------------------------------- Feedback */

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent",
        className
      )}
    />
  );
}

export function Banner({
  tone = "amber",
  children,
}: {
  tone?: "amber" | "red" | "blue" | "green";
  children: React.ReactNode;
}) {
  const map = {
    amber: "border-amber-200 bg-amber-50 text-amber-800",
    red: "border-red-200 bg-red-50 text-red-700",
    blue: "border-blue-200 bg-blue-50 text-blue-800",
    green: "border-emerald-200 bg-emerald-50 text-emerald-800",
  } as const;
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-[13px]",
        map[tone]
      )}
    >
      {children}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      {icon && <div className="text-ink-subtle">{icon}</div>}
      <div className="text-[13px] font-medium text-ink">{title}</div>
      {hint && <div className="max-w-sm text-xs text-ink-muted">{hint}</div>}
    </div>
  );
}

/* --------------------------------------------------------- Page scaffold */

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight text-ink">
          {title}
        </h1>
        {description && (
          <p className="mt-0.5 text-[13px] text-ink-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </div>
  );
}

/** Two-column definition row used inside cards. */
export function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-subtle">
        {label}
      </div>
      <div className="mt-0.5 text-[13px] text-ink">{value}</div>
    </div>
  );
}
