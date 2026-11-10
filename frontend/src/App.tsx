import { useCallback, useState } from "react";
import { fetchMultisector, mastSectors, reportUrl, vetMastAndReport, vetUploadAndReport, type ProgressEvent, type SectorInfo, type DetectParams, type VetCallbacks, type VetOutcome } from "./api";
import type { VettingResult } from "./types";
import ExoMinerPanel from "./ExoMinerPanel";
import FfiCutoutPanel from "./FfiCutoutPanel";
import MicrolensingPanel from "./MicrolensingPanel";
import { CyclingLoader } from "./Loaders";
import { MULTISECTOR_LOADING_MSGS } from "./transit/loadingMessages";
import { VetProgress } from "./transit/VetProgress";
import { SensitivityPanel } from "./transit/SensitivityPanel";
import { TabButton, SectionTabButton } from "./transit/TabButtons";
import { ResultsView } from "./transit/ResultsView";
import { MultisectorPanel } from "./transit/MultisectorPanel";

type Status = "idle" | "uploading" | "analyzing" | "done" | "error";

type ReportState =
  | { state: "building" }
  | { state: "ready"; url: string; filename: string }
  | { state: "error"; message: string };

type Mode = "upload" | "mast";

type Section = "transit" | "microlensing";

const REPO_URL = "https://github.com/eagnespuerto/vetstar";

const KOFI_URL = "https://ko-fi.com/eagnespuerto";

export default function App() {
  const [section, setSection] = useState<Section>("transit");
  const [mode, setMode] = useState<Mode>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VettingResult | null>(null);
  const [drag, setDrag] = useState(false);

  // Detection sensitivity (shared across both modes).
  const [params, setParams] = useState<DetectParams>({
    threshold: 0.997,
    minSnr: 4.0,
    highVariability: false,
    rotationPeriod: null,
    secondarySigma: 3.0,
    oddEvenSigma: 3.0,
    knownPeriod: null,
  });

  // MAST mode state
  const [ticInput, setTicInput] = useState<string>("");
  const [sectorInput, setSectorInput] = useState<string>("");
  const [availableSectors, setAvailableSectors] = useState<SectorInfo[] | null>(null);
  const [sectorLookupLoading, setSectorLookupLoading] = useState(false);

  // Analysis scope chosen up front: single sector vs multi-sector (≤5).
  const [scope, setScope] = useState<"single" | "multi">("single");
  // Sectors selected for a multi-sector run (max 5). Empty = backend picks newest 5.
  const [multiSectors, setMultiSectors] = useState<number[]>([]);
  // Number of distinct objects to surface from the per-sector event clustering.
  // Default 2 matches the backend; bump higher when the deepest dips are
  // contaminants/EBs masking the real TOI (e.g. TOI 6568.01 on TIC 253126207).
  const [multiMaxObjects, setMultiMaxObjects] = useState<number>(2);
  const [msData, setMsData] = useState<any>(null);
  const [msTopLoading, setMsTopLoading] = useState(false);
  // PDF built by the same job as the on-page vetting; null before a run.
  const [report, setReport] = useState<ReportState | null>(null);
  // Live progress for the vet + report job (SSE-backed). Keeps updating
  // after the result renders, while the PDF builds; null when idle.
  const [analyzeProgress, setAnalyzeProgress] = useState<ProgressEvent | null>(null);
  const jobRunning = status === "analyzing" || report?.state === "building";

  const MAX_MULTI_SECTORS = 5;
  const MAX_MULTI_OBJECTS = 10; // MAX_SECTORS * EVENTS_PER_SECTOR on backend
  const toggleMultiSector = (s: number) => {
    setMultiSectors((prev) =>
      prev.includes(s)
        ? prev.filter((x) => x !== s)
        : prev.length >= MAX_MULTI_SECTORS
        ? prev
        : [...prev, s]
    );
  };

  const runMultisectorTop = async () => {
    const tic = parseInt(ticInput);
    if (!tic) {
      setError("Enter a TIC ID.");
      return;
    }
    setStatus("analyzing");
    setError(null);
    setMsData(null);
    setReport(null);
    setMsTopLoading(true);
    try {
      const data = await fetchMultisector(tic, params, multiSectors, multiMaxObjects);
      setMsData(data);
      setStatus("done");
    } catch (e: any) {
      setError(e.message || String(e));
      setStatus("error");
    } finally {
      setMsTopLoading(false);
    }
  };

  const onFile = (f: File) => {
    setFile(f);
    setError(null);
    setResult(null);
    setReport(null);
    setStatus("idle");
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files?.[0]) onFile(e.dataTransfer.files[0]);
  }, []);

  // One job vets, renders the result on the page, then builds the PDF from
  // that same result. `keepResult` leaves the current result on screen
  // while re-running (plot toggles) instead of blanking the page.
  const runVet = async (
    start: (cb: VetCallbacks) => Promise<VetOutcome>,
    startMessage: string,
    keepResult = false,
  ) => {
    setStatus("analyzing");
    setError(null);
    if (!keepResult) setResult(null);
    setReport({ state: "building" });
    setAnalyzeProgress({ stage: "starting", percent: 0, message: startMessage });
    let gotResult = false;
    try {
      const out = await start({
        onProgress: setAnalyzeProgress,
        onResult: (r) => {
          gotResult = true;
          setResult(r);
          setStatus("done");
        },
      });
      setReport(
        out.report
          ? { state: "ready", url: reportUrl(out.report.report_id), filename: out.report.filename }
          : { state: "error", message: out.reportError || "PDF build failed" },
      );
    } catch (e: any) {
      const message = e.message || String(e);
      if (gotResult) {
        // Vetting is on screen; only the PDF step was lost.
        setReport({ state: "error", message });
      } else {
        setReport(null);
        setError(message);
        setStatus("error");
      }
    } finally {
      setAnalyzeProgress(null);
    }
  };

  const runAnalyze = () => {
    if (!file) return;
    void runVet((cb) => vetUploadAndReport(file, params, cb), "Uploading light curve…");
  };

  // -- MAST mode handlers --
  const lookupSectors = async () => {
    const tic = parseInt(ticInput);
    if (!tic) return;
    setSectorLookupLoading(true);
    setError(null);
    try {
      const sectors = await mastSectors(tic);
      setAvailableSectors(sectors);
      if (sectors.length && !sectorInput) {
        setSectorInput(String(sectors[sectors.length - 1].sector));
      }
    } catch (e: any) {
      setError(e.message || String(e));
      setAvailableSectors(null);
    } finally {
      setSectorLookupLoading(false);
    }
  };

  const runMastAnalyze = async () => {
    const tic = parseInt(ticInput);
    const sec = parseInt(sectorInput);
    if (!tic || !sec) {
      setError("Enter both TIC and sector.");
      return;
    }
    await runVet((cb) => vetMastAndReport(tic, sec, params, cb), "Starting analysis…");
  };

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white py-4 px-6 shadow">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-6 flex-wrap">
            <div>
              <h1 className="text-xl font-bold">Vetstar v1.0.0</h1>
              <p className="text-sm text-slate-300">
                {section === "transit"
                  ? "Upload a SPOC light curve (FITS) or pull one from MAST by TIC + sector"
                  : "Fit PSPL / flare / null to a positive excursion, or check TESS coverage of known events"}
              </p>
            </div>
            <nav
              className="flex items-center gap-1 bg-slate-800 rounded-lg p-1 border border-slate-700"
              aria-label="Vetstar section"
            >
              <SectionTabButton active={section === "transit"} onClick={() => setSection("transit")}>
                Transit
              </SectionTabButton>
              <SectionTabButton active={section === "microlensing"} onClick={() => setSection("microlensing")}>
                Microlensing
              </SectionTabButton>
            </nav>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded text-sm font-medium transition"
              title="View on GitHub, report a bug, or contribute"
            >
              <svg
                viewBox="0 0 16 16"
                width="16"
                height="16"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
              </svg>
              Report an Issue or Contribute
            </a>
            <a
              href={KOFI_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-2 bg-[#ff5e5b] hover:bg-[#e54d4a] border border-[#ff7a78] rounded text-sm font-medium transition"
              title="Support Vetstar — buy me a Ko-fi"
            >
              <svg
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M3 4h14.5a4.5 4.5 0 0 1 0 9H17a6 6 0 0 1-6 5H8a5 5 0 0 1-5-5V4zm14 7h.5a2.5 2.5 0 0 0 0-5H17v5zM2 20h16v2H2v-2z" />
              </svg>
              Buy me a Ko-fi
            </a>
          </div>
        </div>
      </header>

      {section === "transit" && (
        <>
      <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {/* Mode tabs */}
        <div className="flex gap-2 border-b">
          <TabButton active={mode === "upload"} onClick={() => setMode("upload")}>
            Upload file
          </TabButton>
          <TabButton active={mode === "mast"} onClick={() => setMode("mast")}>
            Fetch from MAST
          </TabButton>
        </div>

        {/* Detection sensitivity (collapsed by default) */}
        <SensitivityPanel params={params} setParams={setParams} />

        {/* Upload mode */}
        {mode === "upload" && (
          <section
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={`rounded-lg border-2 border-dashed bg-white p-8 transition ${
              drag ? "border-blue-500 bg-blue-50" : "border-slate-300"
            }`}
          >
            <div className="text-center space-y-3">
              <p className="text-slate-700 font-medium">
                Drop a <code>.fits</code> / <code>.fits.gz</code> / <code>.json</code> /{" "}
                <code>.customization</code> file here
              </p>
              <p className="text-xs text-slate-500">or</p>
              <input
                type="file"
                accept=".fits,.gz,.json,.customization"
                onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
                className="block mx-auto text-sm"
              />
              {file && (
                <p className="text-sm text-slate-600">
                  Selected: <span className="font-mono">{file.name}</span> (
                  {(file.size / 1024 / 1024).toFixed(2)} MB)
                </p>
              )}
              <div className="flex justify-center gap-3 mt-2">
                <button
                  onClick={runAnalyze}
                  disabled={!file || jobRunning}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
                >
                  {jobRunning ? "Working…" : "Vet & build PDF report"}
                </button>
              </div>
            </div>
          </section>
        )}

        {/* MAST mode */}
        {mode === "mast" && (
          <section className="rounded-lg border-2 border-slate-200 bg-white p-6">
            <p className="text-sm text-slate-600 mb-4">
              Enter a TIC ID and sector; the backend will fetch the matching SPOC
              2-min light curve from <code>mast.stsci.edu</code> via{" "}
              <code>astroquery.mast.Observations</code>, then run full vetting.
            </p>

            {/* Analysis scope — choose up front */}
            <div className="mb-4">
              <label className="block text-xs text-slate-600 mb-1">Analysis scope</label>
              <div className="inline-flex rounded-lg border border-slate-200 overflow-hidden">
                <button
                  onClick={() => setScope("single")}
                  className={`px-4 py-1.5 text-sm font-medium ${
                    scope === "single" ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  Single sector
                </button>
                <button
                  onClick={() => setScope("multi")}
                  className={`px-4 py-1.5 text-sm font-medium border-l border-slate-200 ${
                    scope === "multi" ? "bg-blue-600 text-white" : "bg-white text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  Multi-sector (≤5)
                </button>
              </div>
              {scope === "multi" && (
                <>
                  <p className="text-xs text-slate-500 mt-1">
                    Pick up to {MAX_MULTI_SECTORS} sectors below (or leave blank to use the
                    newest 5). One representative event per sector is cross-checked for the
                    same duration and period.
                  </p>
                  <div className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                    <label htmlFor="multi-max-objects">
                      Look for up to
                    </label>
                    <input
                      id="multi-max-objects"
                      type="number"
                      min={1}
                      max={MAX_MULTI_OBJECTS}
                      step={1}
                      value={multiMaxObjects}
                      onChange={(e) => {
                        const v = parseInt(e.target.value, 10);
                        if (!Number.isFinite(v)) return;
                        setMultiMaxObjects(Math.max(1, Math.min(MAX_MULTI_OBJECTS, v)));
                      }}
                      className="border rounded px-2 py-0.5 font-mono w-16 text-xs"
                    />
                    <span>
                      distinct object{multiMaxObjects === 1 ? "" : "s"} (max {MAX_MULTI_OBJECTS}).
                      Raise this when the deepest dips look like a contaminant or EB and the
                      real TOI isn't showing up.
                    </span>
                  </div>
                </>
              )}
            </div>
            {/* High stellar variability — fit + subtract a sinusoid before BLS. */}
            <div className="mb-4 p-3 bg-slate-50 rounded border border-slate-200">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input
                  type="checkbox"
                  checked={params.highVariability}
                  onChange={(e) =>
                    setParams({ ...params, highVariability: e.target.checked })
                  }
                />
                High stellar variability (detrend before BLS)
              </label>
              {params.highVariability && (
                <div className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                  <label>Expected rotation period (days, optional):</label>
                  <input
                    type="number"
                    step="0.001"
                    min="0"
                    value={params.rotationPeriod ?? ""}
                    placeholder="auto (Lomb-Scargle peak)"
                    onChange={(e) => {
                      const v = e.target.value.trim();
                      setParams({
                        ...params,
                        rotationPeriod: v === "" ? null : parseFloat(v),
                      });
                    }}
                    className="border rounded px-2 py-0.5 font-mono w-40 text-xs"
                  />
                </div>
              )}
              <p className="text-[11px] text-slate-500 mt-1">
                Fits a sine + first harmonic and subtracts it before BLS. Helps
                detect shallow dips on spotted rotators and wave-like variables.
              </p>
            </div>
            <div className="grid sm:grid-cols-3 gap-3 items-end">
              <div>
                <label className="block text-xs text-slate-600 mb-1">TIC ID</label>
                <input
                  type="number"
                  placeholder="e.g. 451483379"
                  value={ticInput}
                  onChange={(e) => setTicInput(e.target.value)}
                  className="w-full border rounded px-3 py-2 font-mono text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-600 mb-1">Sector</label>
                <input
                  type="number"
                  placeholder="e.g. 100"
                  value={sectorInput}
                  onChange={(e) => setSectorInput(e.target.value)}
                  className="w-full border rounded px-3 py-2 font-mono text-sm"
                />
              </div>
              <button
                onClick={lookupSectors}
                disabled={!ticInput || sectorLookupLoading}
                className="px-4 py-2 bg-slate-700 text-white rounded font-medium hover:bg-slate-800 disabled:bg-slate-300"
              >
                {sectorLookupLoading ? "Looking up…" : "List sectors"}
              </button>
            </div>
            {availableSectors && (
              <div className="mt-3 text-sm">
                {availableSectors.length === 0 ? (
                  <p className="text-slate-500">No TESS sectors found for this TIC.</p>
                ) : (
                  <div className="text-slate-700 space-y-1">
                    <p className="text-xs text-slate-500">
                      Click a sector. Hover for providers (SPOC = 2-min, best;
                      TESS-SPOC / QLP = FFI fallback, no centroid).
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {availableSectors.map((si) => {
                        const selected =
                          scope === "multi"
                            ? multiSectors.includes(si.sector)
                            : String(si.sector) === sectorInput;
                        return (
                        <button
                          key={si.sector}
                          onClick={() =>
                            scope === "multi"
                              ? toggleMultiSector(si.sector)
                              : setSectorInput(String(si.sector))
                          }
                          title={
                            si.providers.length
                              ? `Providers: ${si.providers.join(", ")}`
                              : ""
                          }
                          className={`px-2 py-0.5 rounded font-mono text-xs ${
                            selected
                              ? "bg-blue-600 text-white"
                              : si.providers.includes("SPOC")
                              ? "bg-slate-100 hover:bg-slate-200"
                              : "bg-amber-50 hover:bg-amber-100 text-amber-900"
                          }`}
                        >
                          S{String(si.sector).padStart(3, "0")}
                        </button>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-center gap-3 mt-5">
              {scope === "single" ? (
                <button
                  onClick={runMastAnalyze}
                  disabled={jobRunning || !ticInput || !sectorInput}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
                >
                  {jobRunning ? "Working…" : "Fetch, vet & build PDF report"}
                </button>
              ) : (
                <button
                  onClick={runMultisectorTop}
                  disabled={jobRunning || msTopLoading || !ticInput}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:bg-slate-300"
                >
                  {msTopLoading
                    ? "Running multi-sector…"
                    : multiSectors.length
                    ? `Run multi-sector (${multiSectors.length} selected)`
                    : "Run multi-sector (newest 5)"}
                </button>
              )}
            </div>
          </section>
        )}

        {error && (
          <div className="rounded bg-red-50 border border-red-300 text-red-800 p-4">
            <strong>Error:</strong> {error}
          </div>
        )}

        {status === "analyzing" && msTopLoading && (
          <div className="rounded bg-blue-50 border border-blue-200 p-4 text-blue-900">
            <CyclingLoader messages={MULTISECTOR_LOADING_MSGS} />
            <span className="block mt-1 text-xs text-blue-700">
              Multi-sector runs can take a minute or two — we're crunching several sectors at once.
            </span>
          </div>
        )}

        {analyzeProgress && !msTopLoading && (
          <VetProgress progress={analyzeProgress} hasResult={!!result} />
        )}

        {report && report.state === "ready" && (
          <div className="rounded bg-emerald-50 border border-emerald-200 p-4 text-emerald-900 flex items-center justify-between gap-3 flex-wrap">
            <span className="text-sm">
              <strong>PDF report ready.</strong> Built from the vetting shown below.
            </span>
            <a
              href={report.url}
              download={report.filename}
              className="px-4 py-2 bg-emerald-600 text-white rounded font-medium hover:bg-emerald-700"
            >
              Download PDF report
            </a>
          </div>
        )}

        {report && report.state === "error" && (
          <div className="rounded bg-amber-50 border border-amber-300 text-amber-900 p-4 text-sm">
            <strong>Vetting finished, but the PDF couldn't be built:</strong> {report.message}
          </div>
        )}

        {result && (
          <ResultsView
            result={result}
            lcControls={
              // Only enable toggle re-runs in MAST mode (we need tic+sector to re-fetch).
              result.star?.tic_id != null && result.star?.sector != null
                ? {
                    detrend: params.plotDetrend ?? true,
                    binMinutes:
                      params.plotBinMinutes === undefined ? 30 : params.plotBinMinutes,
                    busy: jobRunning,
                    onChange: ({ detrend, binMinutes }) => {
                      const next: DetectParams = {
                        ...params,
                        plotDetrend: detrend,
                        plotBinMinutes: binMinutes,
                      };
                      setParams(next);
                      // Re-run the single-sector job with new plot params so
                      // the PDF is rebuilt with the same plots as the page.
                      const tic = result.star.tic_id!;
                      const sec = result.star.sector!;
                      void runVet(
                        (cb) => vetMastAndReport(tic, sec, next, cb),
                        "Re-rendering plot…",
                        true,
                      );
                    },
                  }
                : undefined
            }
          />
        )}

        {msData && (
          <div className="mt-2">
            <MultisectorPanel data={msData} />
          </div>
        )}
      </main>

      {result && <FfiCutoutPanel result={result} />}
      {result && <ExoMinerPanel result={result} />}
        </>
      )}

      {section === "microlensing" && <MicrolensingPanel />}

      <footer className="text-center text-xs text-slate-400 py-6">
        {section === "transit"
          ? <>Pipeline: astropy <code>BoxLeastSquares</code> + <code>LombScargle</code>, scipy median filtering, centroid + odd/even + secondary tests, physics-based companion sizing.</>
          : <>Microlensing pipeline: PSPL vs flare vs null model comparison via MulensModel + scipy; TESS sector coverage via tess-point.</>}
      </footer>
    </div>
  );
}
