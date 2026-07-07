/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-bg))",
          border: "hsl(var(--sidebar-border))",
          hover: "hsl(var(--sidebar-item-hover))",
          active: "hsl(var(--sidebar-item-active))",
          "active-text": "hsl(var(--sidebar-item-active-text))",
          "group-text": "hsl(var(--sidebar-group-text))",
        },
      },
      fontSize: {
        'page-title': ['1.5rem', { lineHeight: '1.3', fontWeight: '600' }],
        'section-title': ['1.5rem', { lineHeight: '1.2', fontWeight: '600' }],
        'subsection-title': ['1.25rem', { lineHeight: '1.3', fontWeight: '600' }],
      },
      spacing: {
        'header-gap': '0.5rem',
        'section-gap': '1.5rem',
        'page-padding': '1.5rem',
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "new-card-highlight": {
          "0%": {
            boxShadow: "0 0 12px rgba(109, 76, 196, 0.35), 0 0 24px rgba(109, 76, 196, 0.15)",
            backgroundColor: "rgba(109, 76, 196, 0.06)",
          },
          "100%": {
            boxShadow: "none",
            backgroundColor: "transparent",
          },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "new-card": "new-card-highlight 2s ease-out forwards",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}