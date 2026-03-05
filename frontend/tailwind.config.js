/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        sentinel: {
          50: "#edfdf0",
          100: "#d3fada",
          200: "#aaf4b8",
          300: "#74e990",
          400: "#3dd66a",
          500: "#18be4b",
          600: "#0f9a3a",
          700: "#0f7a30",
          800: "#116129",
          900: "#0f5024",
        },
      },
    },
  },
  plugins: [],
};
