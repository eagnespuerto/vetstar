import { useState } from "react";
import type { DetectParams } from "../api";

export function SensitivityPanel({
  params,
  setParams,
}: {
  params: DetectParams;
  setParams: (p: DetectParams) => void;
}) {
  const [open, setOpen] = useState(false);
  const isDefault =
    params.threshold === 0.997 &&
    params.minSnr === 4.0 &&
    params.secondarySigma === 3.0 &&
    params.oddEvenSigma === 3.0;

  // Helpers for sliders that store a value on a log scale but expose a linear
  // 0..1 position. We pick (min, max) so that sqrt(min*max) === default — that
  // puts the default exactly at the slider midpoint. Because the mapping is
  // logarithmic, the lower half covers a narrow band of small values (fine
  // control near the default) while the upper half stretches out to much
  // larger values (the "highest options can be rather big" behaviour).
  const logPos = (value: number, lo: number, hi: number) => {
    const v = Math.min(Math.max(value, lo), hi);
    return (Math.log(v) - Math.log(lo)) / (Math.log(hi) - Math.log(lo));
  };
  const logVal = (pos: number, lo: number, hi: number) =>
    Math.exp(Math.log(lo) + pos * (Math.log(hi) - Math.log(lo)));

  return (
    <section className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2 text-sm hover:bg-slate-50 transition"
      >
        <span className="font-medium text-slate-700">
          ⚙️ Detection sensitivity{" "}
          <span className="text-xs text-slate-500 ml-1">
            {isDefault ? "(defaults)" : `(threshold=${params.threshold}, SNR=${params.minSnr})`}
          </span>
        </span>
        <span className="text-slate-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 py-4 border-t border-slate-200 space-y-4 text-sm">
          <p className="text-xs text-slate-600">
            Tune how aggressively the pipeline flags dips and follow-on EB
            indicators (secondary eclipse, odd/even depth mismatch). All four
            sliders are <strong>logarithmic</strong>: the default sits at the
            midpoint, the lower half gives fine control near the default, and
            the upper half stretches out to much stricter values so you can
            push the pipeline well past its usual operating point. Defaults
            work well for typical 2-min cadence stars — loosen (slide left) for
            shallow transits on quiet stars, tighten (slide right) for noisy
            targets or to suppress false positives.
          </p>

          <div>
            {(() => {
              // Depth threshold slider: log scale in depth space with the
              // default (0.3%) at the midpoint. Goes from 0.01% (very strict)
              // up to 9% (very loose). Stored value is the raw threshold
              // (1 - depth); API unchanged.
              const MIN_DEPTH = 0.0001; // 0.01% — strictest end
              const MAX_DEPTH = 0.09;   //   9%  — loosest end
              const depth = Math.min(Math.max(1 - params.threshold, MIN_DEPTH), MAX_DEPTH);
              // Slider position: 0 = loose (left, high depth), 1 = strict (right, low depth).
              const pos = 1 - logPos(depth, MIN_DEPTH, MAX_DEPTH);
              const setFromPos = (p: number) => {
                const d = logVal(1 - p, MIN_DEPTH, MAX_DEPTH);
                setParams({ ...params, threshold: 1 - d });
              };
              return (
                <>
                  <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
                    <span>
                      Depth threshold:{" "}
                      <span className="font-mono">{params.threshold.toFixed(4)}</span>
                      <span className="text-slate-400 ml-1">
                        (flag dips deeper than {((1 - params.threshold) * 100).toFixed(3)}%)
                      </span>
                    </span>
                    <button
                      onClick={() => setParams({ ...params, threshold: 0.997 })}
                      className="text-blue-600 hover:underline"
                    >
                      reset
                    </button>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.001}
                    value={pos}
                    onChange={(e) => setFromPos(parseFloat(e.target.value))}
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
                    <span>9% (very loose)</span>
                    <span>0.3% default</span>
                    <span>0.01% (very strict)</span>
                  </div>
                </>
              );
            })()}
          </div>

          <div>
            {(() => {
              // Log scale: default 4σ at midpoint, 1σ..16σ.
              const LO = 1.0, HI = 16.0;
              const pos = logPos(params.minSnr, LO, HI);
              return (
                <>
                  <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
                    <span>
                      Minimum SNR:{" "}
                      <span className="font-mono">{params.minSnr.toFixed(1)}σ</span>
                      <span className="text-slate-400 ml-1">
                        (dips must exceed this × local scatter)
                      </span>
                    </span>
                    <button
                      onClick={() => setParams({ ...params, minSnr: 4.0 })}
                      className="text-blue-600 hover:underline"
                    >
                      reset
                    </button>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.001}
                    value={pos}
                    onChange={(e) =>
                      setParams({ ...params, minSnr: logVal(parseFloat(e.target.value), LO, HI) })
                    }
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
                    <span>1σ (max sensitivity)</span>
                    <span>4σ default</span>
                    <span>16σ (very strict)</span>
                  </div>
                </>
              );
            })()}
          </div>

          <div>
            {(() => {
              // Log scale: default 3σ at midpoint, 0.5σ..18σ.
              const LO = 0.5, HI = 18.0;
              const pos = logPos(params.secondarySigma, LO, HI);
              return (
                <>
                  <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
                    <span>
                      Secondary eclipse σ:{" "}
                      <span className="font-mono">{params.secondarySigma.toFixed(1)}σ</span>
                      <span className="text-slate-400 ml-1">
                        (depth at phase 0.5 must exceed this × local scatter)
                      </span>
                    </span>
                    <button
                      onClick={() => setParams({ ...params, secondarySigma: 3.0 })}
                      className="text-blue-600 hover:underline"
                    >
                      reset
                    </button>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.001}
                    value={pos}
                    onChange={(e) =>
                      setParams({ ...params, secondarySigma: logVal(parseFloat(e.target.value), LO, HI) })
                    }
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
                    <span>0.5σ (very loose, more EB flags)</span>
                    <span>3σ default</span>
                    <span>18σ (very strict)</span>
                  </div>
                </>
              );
            })()}
          </div>

          <div>
            {(() => {
              // Log scale: default 3σ at midpoint, 0.5σ..18σ.
              const LO = 0.5, HI = 18.0;
              const pos = logPos(params.oddEvenSigma, LO, HI);
              return (
                <>
                  <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
                    <span>
                      Odd / even depth σ:{" "}
                      <span className="font-mono">{params.oddEvenSigma.toFixed(1)}σ</span>
                      <span className="text-slate-400 ml-1">
                        (odd vs even transit depth difference flagged as EB above this)
                      </span>
                    </span>
                    <button
                      onClick={() => setParams({ ...params, oddEvenSigma: 3.0 })}
                      className="text-blue-600 hover:underline"
                    >
                      reset
                    </button>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.001}
                    value={pos}
                    onChange={(e) =>
                      setParams({ ...params, oddEvenSigma: logVal(parseFloat(e.target.value), LO, HI) })
                    }
                    className="w-full"
                  />
                  <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
                    <span>0.5σ (very loose, more EB flags)</span>
                    <span>3σ default</span>
                    <span>18σ (very strict)</span>
                  </div>
                </>
              );
            })()}
          </div>

          <div className="pt-2 border-t border-slate-200">
            <label className="block text-xs font-medium text-slate-700 mb-1">
              Known period (days, optional)
            </label>
            <input
              type="number"
              step="any"
              min="0"
              placeholder="e.g. 3.14 — leave blank for blind BLS sweep"
              value={params.knownPeriod ?? ""}
              onChange={(e) => {
                const v = e.target.value.trim();
                setParams({
                  ...params,
                  knownPeriod: v === "" ? null : parseFloat(v),
                });
              }}
              className="w-full border rounded px-2 py-1 font-mono text-xs"
            />
            <p className="text-[11px] text-slate-500 mt-1">
              If set, BLS searches a ±2% window around this period and its
              P/2 and 2P harmonics instead of a blind sweep. Sharpens
              recovered period, t0, duration, and depth — which feeds
              cleaner inputs into the odd/even, secondary, and
              transit-shape checks. Leave blank for unconstrained search.
            </p>
          </div>

          <p className="text-xs text-slate-500 italic">
            Tip: if real shallow transits are being missed, lower SNR first
            (slide left). If noise spikes are being flagged as events, raise
            SNR. If genuine planets are being mislabelled as eclipsing
            binaries, raise the secondary-eclipse and odd/even thresholds —
            because the sliders are logarithmic, small drags on the right half
            move the threshold a lot.
          </p>
        </div>
      )}
    </section>
  );
}
