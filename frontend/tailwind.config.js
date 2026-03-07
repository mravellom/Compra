/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{html,ts}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          900: "#0f1117",
          800: "#161822",
          700: "#1e2030",
          600: "#272a3d",
        },
        accent: {
          green: "#00e88f",
          red: "#ff4d6a",
          blue: "#3b82f6",
          yellow: "#fbbf24",
        },
      },
    },
  },
  plugins: [],
};
