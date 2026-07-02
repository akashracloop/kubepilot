"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "./icons";
import { cn } from "./ui";

type NavItem = { href: string; label: string; icon: IconName; match: (p: string) => boolean };

const NAV: NavItem[] = [
  { href: "/", label: "New Investigation", icon: "Plus", match: (p) => p === "/" },
  {
    href: "/investigations",
    label: "Investigations",
    icon: "List",
    match: (p) => p.startsWith("/investigations"),
  },
  {
    href: "/settings",
    label: "Settings",
    icon: "Settings",
    match: (p) => p.startsWith("/settings"),
  },
];

export function Sidebar() {
  const pathname = usePathname() || "/";
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
      <Link href="/" className="flex items-center gap-2.5 px-4 py-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-white">
          <Icon.Compass size={18} />
        </span>
        <span className="flex flex-col leading-tight">
          <span className="text-[13px] font-semibold text-ink">KubePilot AI</span>
          <span className="text-[11px] text-ink-subtle">Agentic SRE</span>
        </span>
      </Link>

      <nav className="flex-1 space-y-0.5 px-2 py-2">
        {NAV.map((item) => {
          const active = item.match(pathname);
          const IconCmp = Icon[item.icon];
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] font-medium transition-colors",
                active
                  ? "bg-brand-50 text-brand-700"
                  : "text-ink-muted hover:bg-line-soft hover:text-ink"
              )}
            >
              <IconCmp size={16} className={active ? "text-brand-600" : "text-ink-subtle"} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-line-soft px-4 py-3">
        <p className="text-[11px] leading-relaxed text-ink-subtle">
          Read-only investigator with HITL-gated remediation.
        </p>
      </div>
    </aside>
  );
}
