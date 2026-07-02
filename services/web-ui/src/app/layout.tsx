import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "KubePilot AI",
  description: "Agentic SRE — read-only incident investigator for Kubernetes.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-neutral-200 bg-white">
            <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
              <Link href="/" className="text-sm font-semibold text-neutral-900">
                KubePilot AI
              </Link>
              <nav className="flex gap-4 text-sm">
                <Link
                  href="/"
                  className="text-neutral-600 hover:text-neutral-900"
                >
                  New Investigation
                </Link>
                <Link
                  href="/investigations"
                  className="text-neutral-600 hover:text-neutral-900"
                >
                  Investigations
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
