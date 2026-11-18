import { useState } from "react";

/** Light/dark switch. The first paint is set by the inline script in index.html. */
export function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const flip = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("vetstar-theme", next ? "dark" : "light"); } catch { /* private mode */ }
    setDark(next);
  };
  return (
    <button onClick={flip} aria-pressed={dark} title={dark ? "Switch to light theme" : "Switch to dark theme"}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-dusk-800 hover:bg-dusk-700 border border-dusk-700 text-cream-100">
      <span aria-hidden="true">{dark ? "☀" : "☾"}</span>{dark ? "Light" : "Dark"}
    </button>
  );
}
