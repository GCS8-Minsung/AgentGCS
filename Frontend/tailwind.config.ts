import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          50: "#f7fbfb",
          100: "#eaf5f3",
          800: "#0f2d34",
          900: "#08181c"
        },
        accent: {
          orange: "#f97316",
          teal: "#10b981",
          blue: "#0ea5e9"
        }
      },
      boxShadow: {
        panel: "0 20px 45px -24px rgba(7, 42, 51, 0.35)"
      }
    }
  },
  plugins: []
};

export default config;

