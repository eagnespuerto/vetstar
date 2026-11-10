import { useState } from "react";
import { mastSectors, type SectorInfo } from "../api";
import { CyclingLoader, TIC_LC_MSGS } from "../Loaders";

export function DataLoader({
  onFile, onDemo, onFetchTic, loadErr, lcLabel, lcPoints,
}: {
  onFile: (f: File) => void;
  onDemo: (which: "pspl" | "flare") => void;
  onFetchTic: (ticId: number, sector: number | null) => Promise<void>;
  loadErr: string | null;
  lcLabel?: string;
  lcPoints: number;
}) {
  const [ticInput, setTicInput] = useState("");
  const [sectorInput, setSectorInput] = useState("");
  const [sectors, setSectors] = useState<SectorInfo[] | null>(null);
  const [sectorsBusy, setSectorsBusy] = useState(false);
  const [sectorErr, setSectorErr] = useState<string | null>(null);
  const [fetchBusy, setFetchBusy] = useState(false);

  const lookupSectors = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    setSectorsBusy(true); setSectorErr(null);
    try {
      const s = await mastSectors(tic);
      setSectors(s);
      if (s.length && !sectorInput) setSectorInput(String(s[s.length - 1].sector));
    } catch (e: any) {
      setSectorErr(e.message || String(e));
      setSectors(null);
    } finally {
      setSectorsBusy(false);
    }
  };

  const runFetch = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    const sec = sectorInput.trim() ? parseInt(sectorInput) : null;
    setFetchBusy(true);
    try {
      await onFetchTic(tic, sec);
    } finally {
      setFetchBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 space-y-4">
      <div>
        <h3 className="font-semibold text-slate-800 mb-2">1. Load a light curve</h3>
        <p className="text-xs text-slate-600 mb-3">
          Three ways to get data into the classifier: <strong>upload</strong> a
          FITS-equivalent JSON/CSV file, <strong>fetch from MAST</strong> by
          TIC + sector (same UX as the Transit tab's MAST mode), or start with
          a <strong>synthetic demo</strong>.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept=".json,.csv,.txt"
            onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
            className="text-sm"
          />
          <span className="text-slate-300">|</span>
          <button
            onClick={() => onDemo("pspl")}
            className="px-3 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 rounded border border-slate-200"
          >
            Load PSPL demo
          </button>
          <button
            onClick={() => onDemo("flare")}
            className="px-3 py-1 text-xs font-medium bg-slate-100 hover:bg-slate-200 rounded border border-slate-200"
          >
            Load flare demo
          </button>
        </div>
      </div>

      {/* MAST fetch — TIC + sector */}
      <div className="border-t border-slate-100 pt-3">
        <p className="text-xs font-semibold text-slate-700 mb-2">
          or fetch from MAST by TIC + sector
        </p>
        <div className="grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] gap-2 items-end">
          <div>
            <label className="block text-[10px] text-slate-600 mb-0.5">TIC ID</label>
            <input
              type="number"
              placeholder="e.g. 261136679"
              value={ticInput}
              onChange={(e) => setTicInput(e.target.value)}
              className="w-full border rounded px-2 py-1 font-mono text-xs"
            />
          </div>
          <div>
            <label className="block text-[10px] text-slate-600 mb-0.5">
              Sector <span className="text-slate-400">(blank = newest available)</span>
            </label>
            <input
              type="number"
              placeholder="auto"
              value={sectorInput}
              onChange={(e) => setSectorInput(e.target.value)}
              className="w-full border rounded px-2 py-1 font-mono text-xs"
            />
          </div>
          <button
            onClick={lookupSectors}
            disabled={!ticInput || sectorsBusy}
            className="px-3 py-1 text-xs font-medium bg-slate-700 text-white rounded hover:bg-slate-800 disabled:bg-slate-300"
          >
            {sectorsBusy ? "…" : "List sectors"}
          </button>
          <button
            onClick={runFetch}
            disabled={!ticInput || fetchBusy}
            className="px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-slate-300 min-w-[9rem]"
          >
            {fetchBusy
              ? <CyclingLoader messages={TIC_LC_MSGS} className="text-[10px]" />
              : "Fetch light curve"}
          </button>
        </div>
        {sectorErr && (
          <p className="text-xs text-red-700 mt-2">{sectorErr}</p>
        )}
        {sectors && sectors.length === 0 && (
          <p className="text-xs text-slate-500 mt-2">No TESS sectors found for this TIC.</p>
        )}
        {sectors && sectors.length > 0 && (
          <div className="mt-2">
            <p className="text-[10px] text-slate-500 mb-1">
              Click a sector to pick it. SPOC (2-min) = grey; FFI-only (TESS-SPOC/QLP) = amber.
            </p>
            <div className="flex flex-wrap gap-1">
              {sectors.map((si) => {
                const selected = String(si.sector) === sectorInput;
                const hasSpoc = si.providers.includes("SPOC");
                return (
                  <button
                    key={si.sector}
                    onClick={() => setSectorInput(String(si.sector))}
                    title={si.providers.length ? `Providers: ${si.providers.join(", ")}` : ""}
                    className={
                      "px-2 py-0.5 rounded font-mono text-[10px] " +
                      (selected
                        ? "bg-blue-600 text-white"
                        : hasSpoc
                        ? "bg-slate-100 hover:bg-slate-200"
                        : "bg-amber-50 hover:bg-amber-100 text-amber-900")
                    }
                  >
                    S{String(si.sector).padStart(3, "0")}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {loadErr && (
        <p className="text-sm text-red-700 mt-2">{loadErr}</p>
      )}
      {lcLabel && (
        <p className="text-xs text-slate-500 mt-2 font-mono">
          Loaded: {lcLabel} ({lcPoints} points)
        </p>
      )}
    </section>
  );
}

// ============================================================================
// Plot primitives
// ============================================================================
