/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        accent: { DEFAULT: "#2563eb", soft: "#dbeafe" },
        warn: { DEFAULT: "#b45309", soft: "#fef3c7" },
        block: { DEFAULT: "#b91c1c", soft: "#fee2e2" },
        ok: { DEFAULT: "#047857", soft: "#d1fae5" },
      },
    },
  },
  plugins: [],
};
