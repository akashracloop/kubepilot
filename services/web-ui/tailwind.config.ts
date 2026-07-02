import type { Config } from "tailwindcss";

/**
 * Frappe-UI-inspired design tokens. A calm, gray-forward surface with a single
 * blue accent, soft radii, and subtle borders/shadows — applied on top of the
 * default Tailwind palette (we lean on `gray`/`blue` plus the semantic vars).
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        surface: "#ffffff",
        canvas: "#f4f5f6",
        ink: {
          DEFAULT: "#1f272e",
          muted: "#525b64",
          subtle: "#7c848c",
        },
        line: {
          DEFAULT: "#e2e4e9",
          soft: "#eef0f2",
        },
        brand: {
          50: "#eef4ff",
          100: "#dbe7ff",
          500: "#2b6cff",
          600: "#1a56db",
          700: "#1a44b8",
        },
      },
      borderRadius: {
        md: "6px",
        lg: "8px",
        xl: "10px",
        "2xl": "14px",
      },
      boxShadow: {
        card: "0 1px 2px 0 rgba(31, 39, 46, 0.04), 0 1px 3px 0 rgba(31, 39, 46, 0.06)",
        pop: "0 4px 12px -2px rgba(31, 39, 46, 0.10), 0 2px 6px -2px rgba(31, 39, 46, 0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
