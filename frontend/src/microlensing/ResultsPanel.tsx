import type { MicrolensingFitResponse } from "../api";
import type { ModelKey } from "./types";
import { _MODEL_COLORS, ResidualPlot } from "./plot";
import { ObservablesPanel, PlanetPredictionsPanel, ExofopPlanetParamsPanel } from "./DerivedPanels";
import { ExportToolbar } from "./ExportToolbar";

export const VERDICT_STYLE: Record<string, string> = {
  microlensing: "bg-emerald-100 border-emerald-400 text-emerald-900",
  flare: "bg-amber-100 border-amber-400 text-amber-900",
  null: "bg-slate-100 border-slate-400 text-slate-800",
  ambiguous: "bg-sky-100 border-sky-400 text-sky-900",
};

export function ResultsPanel({
  result, visible, setVisible, residualModel, setResidualModel,
  svgRef, ffiPng, exportMetadata,
}: {
  result: MicrolensingFitResponse;
  visible: Record<ModelKey, boolean>;
  setVisible: (v: Record<ModelKey, boolean>) => void;
  residualModel: ModelKey;
  setResidualModel: (m: ModelKey) => void;
  svgRef: React.RefObject<SVGSVGElement | null>;
  ffiPng: string | null;
  exportMetadata: {
    event_id: string | null;
    tic_id: number | null;
    ra: number | null;
    dec: number | null;
    sector: number | null;
    provider: string | null;
  };
}) {
  const pspl = result.models.pspl;
  const flare = result.models.flare;
  const nul = result.models.null;
  const sym = result.symmetry_score;

  const bics: Array<{ key: ModelKey; label: string; bic: number | null }> = [
    { key: "pspl", label: "PSPL", bic: pspl.bic },
    { key: "flare", label: "Flare", bic: flare.bic },
    { key: "null", label: "Null", bic: nul.bic },
  ];
  const finiteBics = bics.filter((b) => b.bic != null && Number.isFinite(b.bic)) as Array<{ key: ModelKey; label: string; bic: number }>;
  const bestBic = finiteBics.length ? Math.min(...finiteBics.map((b) => b.bic)) : 0;
  const worstDelta = finiteBics.length ? Math.max(...finiteBics.map((b) => b.bic - bestBic)) : 1;

  return (
    <div className="space-y-4 border-t border-slate-200 pt-4">
      {/* Export / share toolbar */}
      <ExportToolbar
        result={result}
        svgRef={svgRef}
        ffiPng={ffiPng}
        exportMetadata={exportMetadata}
      />

      {/* Verdict badge */}
      <section
        className={`rounded-lg border-2 p-4 ${VERDICT_STYLE[result.verdict] || VERDICT_STYLE.ambiguous}`}
      >
        <p className="text-[10px] uppercase tracking-wide opacity-70">Verdict</p>
        <h4 className="text-xl font-bold mt-0.5">{result.verdict.toUpperCase()}</h4>
        <p className="text-sm mt-1">
          Confidence: <span className="font-mono">{(result.confidence * 100).toFixed(0)}%</span>{" "}
          &nbsp;·&nbsp; window: {result.window.n_points} points, baseline{" "}
          <span className="font-mono">{result.window.baseline_flux.toExponential(3)}</span>
        </p>
      </section>

      {/* ΔBIC bar */}
      <section>
        <h5 className="text-sm font-semibold text-slate-700 mb-1">
          Bayesian Information Criterion (lower is better)
        </h5>
        <div className="space-y-1.5">
          {bics.map(({ key, label, bic }) => {
            const delta = bic != null && Number.isFinite(bic) ? bic - bestBic : null;
            const barPct = delta != null && worstDelta > 0 ? (delta / worstDelta) * 100 : 0;
            const isBest = delta === 0;
            return (
              <div key={key} className="flex items-center gap-2 text-xs">
                <label className="w-14 flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={visible[key]}
                    onChange={(e) => setVisible({ ...visible, [key]: e.target.checked })}
                    className="h-3 w-3"
                  />
                  <span className="font-medium">{label}</span>
                </label>
                <span
                  className="inline-block w-3 h-3 rounded-sm"
                  style={{ background: _MODEL_COLORS[key] }}
                />
                <div className="flex-1 h-4 bg-slate-100 rounded overflow-hidden">
                  <div
                    className={`h-full ${isBest ? "bg-emerald-500" : "bg-slate-400"}`}
                    style={{ width: `${Math.min(100, Math.max(2, barPct))}%` }}
                  />
                </div>
                <span className="font-mono w-32 text-right">
                  {bic != null && Number.isFinite(bic)
                    ? `BIC=${bic.toFixed(1)} (Δ=${(bic - bestBic).toFixed(1)})`
                    : "fit failed"}
                </span>
              </div>
            );
          })}
        </div>
        <p className="text-[10px] text-slate-500 mt-1.5">
          Δnull−PSPL = {fmtDelta(result.delta_bic.null_minus_pspl)} ·
          Δflare−PSPL = {fmtDelta(result.delta_bic.flare_minus_pspl)}.
          Rule: PSPL strong if Δnull−PSPL &gt; 10; ambiguous if |Δflare−PSPL| &lt; 6.
        </p>
      </section>

      {/* PSPL params + errors */}
      <section>
        <h5 className="text-sm font-semibold text-slate-700 mb-1">
          PSPL best-fit parameters
        </h5>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
          {Object.entries(pspl.params).map(([k, v]) => (
            <ParamStat
              key={k}
              label={k}
              value={v}
              err={pspl.param_err[k] as number | null}
            />
          ))}
        </div>
        <p className="text-[10px] text-slate-500 mt-1">
          Errors from the least-squares Jacobian (linearized 1σ, may be optimistic).
        </p>
      </section>

      {/* Observable parameters — physical quantities derived from PSPL alone */}
      {result.observables && <ObservablesPanel obs={result.observables} />}

      {/* Predicted planet parameters — fiducial-lens scales + sensitivity floor */}
      {result.planet_predictions && (
        <PlanetPredictionsPanel pp={result.planet_predictions} />
      )}

      {/* ExoFOP planet parameters — microlensing-derivable subset, mirrors
          the transit tab's ExoFOP TOI parameters block in shape + naming. */}
      {result.exofop_rows && result.exofop_rows.length > 0 && (
        <ExofopPlanetParamsPanel rows={result.exofop_rows} />
      )}

      {/* Symmetry + notes */}
      <section className="grid md:grid-cols-2 gap-3 text-xs">
        <div className="rounded border border-slate-200 bg-slate-50 p-2">
          <span className="text-slate-500">Symmetry score (PSPL residuals, folded about t₀)</span>
          <div className="text-lg font-mono">
            {sym == null ? "—" : sym.toFixed(3)}
          </div>
          <p className="text-[10px] text-slate-500 mt-1">
            +1 = perfectly symmetric wings (PSPL-like). ~0 = uncorrelated (good fit
            or noise-dominated). &lt;0 = anti-symmetric.
          </p>
        </div>
        <div className="rounded border border-amber-200 bg-amber-50 p-2">
          <span className="text-slate-500 font-medium">Diagnostic notes</span>
          <ul className="mt-1 space-y-0.5">
            {result.notes.map((n, i) => (
              <li key={i} className="text-slate-700">• {n}</li>
            ))}
          </ul>
        </div>
      </section>

      {/* Residuals sub-plot */}
      <ResidualPlot
        result={result}
        model={residualModel}
        setModel={setResidualModel}
      />
    </div>
  );
}

export function fmtDelta(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(1);
}

export function ParamStat({ label, value, err }: { label: string; value: number; err: number | null }) {
  return (
    <div className="flex flex-col">
      <span className="text-slate-500 font-mono">{label}</span>
      <span className="font-mono text-sm">
        {value.toPrecision(5)}
        {err != null && Number.isFinite(err) && (
          <span className="text-slate-400"> ± {err.toPrecision(3)}</span>
        )}
      </span>
    </div>
  );
}
