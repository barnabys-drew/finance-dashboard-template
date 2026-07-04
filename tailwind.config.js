/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html", "./static/**/*.js"],
  // Bootstrap is removed; Preflight now provides the base reset.
  corePlugins: { preflight: true },
  theme: {
    extend: {
      colors: {
        bg: "#0f1117",        // page background
        panel: "#1c1f26",     // card-panel surface
        panel2: "#161b22",    // insight-block / table head / inputs
        ink: "#e1e4e8",       // primary text
        muted: "#8b949e",     // secondary text
        faint: "#6e7681",     // tertiary text
        line: "#2d3139",      // borders
        line2: "#21262d",     // subtle dividers
        accent: "#58a6ff",    // primary accent / active
        pos: "#3fb950",       // income / positive
        neg: "#f8a5a5",       // expense / negative
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "sans-serif"],
      },
      borderRadius: {
        card: "12px",
        tile: "10px",
      },
    },
  },
};
