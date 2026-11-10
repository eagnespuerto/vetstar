import { useState } from "react";
import { GLOSSARY } from "../glossary";

export function GlossaryPanel() {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");

  const entries = Object.entries(GLOSSARY).filter(
    ([k, v]) =>
      !filter ||
      k.toLowerCase().includes(filter.toLowerCase()) ||
      v.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <section className="bg-white rounded-lg shadow overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-slate-50 transition text-sm"
      >
        <span className="font-semibold text-slate-700">
          📖 Glossary of technical terms
          <span className="ml-2 text-xs font-normal text-slate-400">
            ({Object.keys(GLOSSARY).length} terms — hover any{" "}
            <span className="border-b border-dotted border-slate-400">
              dotted-underlined
            </span>{" "}
            term in the results for its definition)
          </span>
        </span>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 border-t border-slate-100 space-y-3">
          <input
            type="text"
            placeholder="Search glossary…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-full border rounded px-3 py-1.5 text-sm mt-3"
          />
          <dl className="space-y-2 max-h-96 overflow-y-auto">
            {entries.length === 0 && (
              <p className="text-sm text-slate-400 italic">No matching terms.</p>
            )}
            {entries.map(([term, def]) => (
              <div key={term} className="text-sm">
                <dt className="font-semibold text-slate-800 font-mono text-xs">
                  {term}
                </dt>
                <dd className="text-slate-600 ml-2">{def}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </section>
  );
}
