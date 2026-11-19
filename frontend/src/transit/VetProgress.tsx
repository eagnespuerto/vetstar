import type { ProgressEvent } from "../api";
import { CyclingLoader } from "../Loaders";
import { VETTING_LOADING_MSGS, PDF_LOADING_MSGS } from "./loadingMessages";

// Backend progress stages (progress.py STAGE_WEIGHTS) grouped into the
// four phases of the vet + report job, in order.
export const VET_PHASES: { label: string; stages: string[] }[] = [
  { label: "Load light curve", stages: ["starting", "queued", "mast_fetch", "parse"] },
  {
    label: "Vet",
    stages: [
      "clean", "lomb_scargle", "detrend", "bls", "events", "shape", "centroid",
      "odd_even", "secondary", "physics", "verdict", "crossmatch", "plots",
    ],
  },
  { label: "SPOC DV + habitability", stages: ["dvt_fetch", "habitability"] },
  { label: "PDF report", stages: ["report", "done"] },
];

export function vetPhaseIndex(stage: string): number {
  const i = VET_PHASES.findIndex((p) => p.stages.includes(stage));
  return i < 0 ? 0 : i;
}

export function VetProgress({
  progress,
  hasResult,
}: {
  progress: ProgressEvent;
  hasResult: boolean;
}) {
  const phase = vetPhaseIndex(progress.stage);
  const building = phase === VET_PHASES.length - 1;
  const pct = Math.max(2, Math.min(100, progress.percent));
  const tone = building
    ? { box: "bg-emerald-50 border-emerald-200 text-emerald-900", bar: "bg-emerald-600", track: "bg-emerald-100", sub: "text-emerald-700" }
    : { box: "bg-blue-50 border-blue-200 text-blue-900", bar: "bg-blue-600", track: "bg-blue-100", sub: "text-blue-700" };

  return (
    <div className={`rounded border p-4 ${tone.box}`}>
      <CyclingLoader messages={building ? PDF_LOADING_MSGS : VETTING_LOADING_MSGS} />

      <ol className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs" aria-label="Vetting phases">
        {VET_PHASES.map((p, i) => {
          const state = i < phase ? "done" : i === phase ? "active" : "pending";
          return (
            <li
              key={p.label}
              aria-current={state === "active" ? "step" : undefined}
              className={`flex items-center gap-1.5 rounded px-2 py-1 border ${
                state === "done"
                  ? "bg-white/70 border-slate-200 text-slate-600"
                  : state === "active"
                  ? `bg-white border-current font-semibold`
                  : "border-transparent text-slate-400"
              }`}
            >
              <span
                className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] ${
                  state === "done"
                    ? "bg-slate-400 text-white"
                    : state === "active"
                    ? `${tone.bar} text-white animate-pulse`
                    : "bg-slate-200 text-slate-500"
                }`}
                aria-hidden="true"
              >
                {state === "done" ? "✓" : i + 1}
              </span>
              {p.label}
            </li>
          );
        })}
      </ol>

      <div className="mt-3">
        <div className="flex items-center justify-between text-xs font-mono mb-1">
          <span>{progress.message}</span>
          <span>{progress.percent.toFixed(0)}%</span>
        </div>
        <div className={`h-2 w-full rounded overflow-hidden ${tone.track}`}>
          <div
            className={`h-full transition-all duration-300 ease-out ${tone.bar}`}
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={Math.round(progress.percent)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Vetting progress: ${progress.message}`}
          />
        </div>
      </div>

      <p className={`mt-2 text-xs ${tone.sub}`}>
        {progress.stage === "queued"
          ? "Another vetting is running on the server — yours starts as soon as it finishes."
          : building && hasResult
          ? "Results are below. The PDF is being built from the same run — no second download."
          : "The TESScut cutout, SPOC DV file, catalogue cross-match and ExoFOP load in the background while the light curve is vetted. Results appear first, then the PDF."}
      </p>
    </div>
  );
}
