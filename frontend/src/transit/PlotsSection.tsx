import { useState } from "react";
import { ShareToImgbbButton, ShareIcon } from "../ShareButton";
import { triggerDownload, CopyField } from "./ui";

export function SharePlot({
  b64,
  label,
  title,
}: {
  b64: string | null | undefined;
  label: string;
  title: string;
}) {
  if (!b64) return null;
  return (
    <figure className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-xs text-slate-500">{label}</figcaption>
        <ShareToImgbbButton base64={b64} title={title} label={label} />
      </div>
      <img
        src={`data:image/png;base64,${b64}`}
        alt={label}
        className="w-full rounded border"
        loading="lazy"
      />
    </figure>
  );
}

export function PlotsSection({
  plots,
  ticId,
  sector,
  extras,
}: {
  plots: Record<string, string>;
  ticId?: number | null;
  sector?: number | null;
  extras?: Record<string, string>;
}) {
  const order = ["lightcurve", "event_zoom", "centroid", "bls", "lomb_scargle", "hci_summary"];
  const labels: Record<string, string> = {
    lightcurve: "Full detrended light curve",
    event_zoom: "Event zoom",
    centroid: "Centroid behaviour",
    bls: "BLS periodogram",
    lomb_scargle: "Lomb-Scargle top peaks",
    spoc_dv_phase_fold: "SPOC DV phase-fold",
    hci_summary: "Habitability Chance Index summary",
  };
  // ExoFOP file-type single-letter code per plot. L = Light Curve,
  // O = Other (used for periodograms + centroid diagnostics that aren't
  // raw photometry). See the bulk-upload spec, section 1.
  const exofopTypeCode: Record<string, string> = {
    lightcurve: "L",
    event_zoom: "L",
    centroid: "O",
    bls: "O",
    lomb_scargle: "O",
    spoc_dv_phase_fold: "L",
    hci_summary: "O",
  };

  const [albumResult, setAlbumResult] = useState<any>(null);
  const [albumLoading, setAlbumLoading] = useState(false);
  const [albumError, setAlbumError] = useState<string | null>(null);
  const [forumText, setForumText] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);
  // ExoFOP upload metadata — initials + data tag persist across runs so
  // a frequent uploader sets them once. Counter resets to 001 each day.
  const [exoInitials, setExoInitials] = useState<string>(
    () => localStorage.getItem("vetstar.exofop.initials") || "",
  );
  const [exoCounter, setExoCounter] = useState<string>("001");
  const [exoTag, setExoTag] = useState<string>(
    () => localStorage.getItem("vetstar.exofop.tag") || "",
  );

  const bulkDownload = async () => {
    setBulkError(null);

    if (!ticId) {
      setBulkError(
        "Needs a TIC ID — run via the MAST tab or upload a SPOC FITS with a TICID header.",
      );
      return;
    }
    const initials = exoInitials.trim().toLowerCase();
    if (!/^[a-z]{2,}$/.test(initials)) {
      setBulkError("Initials must be 2+ letters (e.g. mc) — used in zip + file names.");
      return;
    }
    const counter = exoCounter.trim().padStart(3, "0");
    if (!/^\d{3}$/.test(counter) || counter === "000") {
      setBulkError("Counter must be 001–999.");
      return;
    }
    const tag = exoTag.trim();
    if (!tag) {
      setBulkError(
        "Data tag is required (column 2 of the descriptor). Must be valid for your ExoFOP account.",
      );
      return;
    }

    // Persist the long-lived fields so the next run is one-click.
    localStorage.setItem("vetstar.exofop.initials", initials);
    localStorage.setItem("vetstar.exofop.tag", tag);

    // YYYYMMDD in the user's local timezone (the upload date the user sees).
    const now = new Date();
    const yyyymmdd =
      `${now.getFullYear()}` +
      String(now.getMonth() + 1).padStart(2, "0") +
      String(now.getDate()).padStart(2, "0");
    const archiveBase = `${initials}${yyyymmdd}-${counter}`; // mc20180301-001

    setBulkBusy(true);
    try {
      const { buildZip } = await import("../zip");

      const b64ToBytes = (b64: string) => {
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return bytes;
      };

      // Individual filename: targetnameC-xxDATESTAMP[optional_info][y].ext
      // — targetname includes the "TIC" prefix (no dashes/spaces).
      // — C is a single-letter type code (L for light-curve plots,
      //   O for periodograms / centroid diagnostics).
      // — DATESTAMP uses the UT-date form YYYYMMDD.
      // — [optional_info] keeps the file unique inside the archive when
      //   several plots ship together (only dots and dashes are legal,
      //   no spaces or slashes).
      // Spec: ExoFOP-TESS Bulk File Upload Instructions §1, Sept 2024.
      const seen = new Set<string>();
      const all: {
        key: string;
        label: string;
        filename: string;
        bytes: Uint8Array;
      }[] = [];
      const collect = (k: string, b64?: string) => {
        if (!b64) return;
        // If this key was already collected (e.g. plots[hci_summary] from
        // /api/analyze followed by a user-recomputed extras[hci_summary]),
        // overwrite the bytes but keep the existing filename to avoid the
        // a/b/c suffix logic kicking in for what is logically one slot.
        const existingIdx = all.findIndex((x) => x.key === k);
        if (existingIdx >= 0) {
          all[existingIdx] = { ...all[existingIdx], bytes: b64ToBytes(b64) };
          return;
        }
        const code = exofopTypeCode[k] ?? "O";
        const safeKey = k.replace(/_/g, "-"); // underscores allowed by spec
        let filename = `TIC${ticId}${code}-${initials}${yyyymmdd}.${safeKey}.png`;
        if (filename.length > 100) {
          // 100-char cap (spec §1). Truncate the descriptor segment.
          const maxKey =
            100 - `TIC${ticId}${code}-${initials}${yyyymmdd}..png`.length;
          filename = `TIC${ticId}${code}-${initials}${yyyymmdd}.${safeKey.slice(0, Math.max(maxKey, 1))}.png`;
        }
        // Suffix a/b/c if a sibling already grabbed this name.
        if (seen.has(filename)) {
          let suffix = "a";
          while (seen.has(filename.replace(/\.png$/, `${suffix}.png`))) {
            suffix = String.fromCharCode(suffix.charCodeAt(0) + 1);
          }
          filename = filename.replace(/\.png$/, `${suffix}.png`);
        }
        seen.add(filename);
        all.push({ key: k, label: labels[k] ?? k, filename, bytes: b64ToBytes(b64) });
      };
      for (const k of order) collect(k, plots[k]);
      if (extras) for (const k of Object.keys(extras)) collect(k, extras[k]);

      if (all.length === 0) {
        setBulkError("No plots available to bundle yet.");
        return;
      }

      // Descriptor file: xxYYYYMMDD-nnn.txt with 5 pipe-delimited columns:
      //   filename | data tag | group | proprietary period | description
      // Group blank → proprietary period must be 0 (spec §2).
      const descriptorLines = all.map(
        (x) =>
          `${x.filename}|${tag}||0|Vetstar — ${x.label}${sector ? ` (Sector ${sector})` : ""}`,
      );
      const descriptorBytes = new TextEncoder().encode(
        descriptorLines.join("\n") + "\n",
      );

      const zipBlob = buildZip([
        { name: `${archiveBase}.txt`, data: descriptorBytes },
        ...all.map((x) => ({ name: x.filename, data: x.bytes })),
      ]);
      triggerDownload(zipBlob, `${archiveBase}.zip`);

      // Auto-bump the counter so a second run on the same day gets -002.
      const nextN = Math.min(parseInt(counter, 10) + 1, 999);
      setExoCounter(String(nextN).padStart(3, "0"));
    } catch (e: any) {
      setBulkError(e?.message || String(e));
    } finally {
      setBulkBusy(false);
    }
  };

  const uploadAll = async () => {
    setAlbumLoading(true);
    setAlbumError(null);
    try {
      const { uploadAllPlots, forumPost } = await import("../imgbb");
      const title = ticId
        ? `TIC_${ticId}${sector ? `_S${sector}` : ""}`
        : "Vetstar";
      const result = await uploadAllPlots(plots, labels, title);
      setAlbumResult(result);
      setForumText(forumPost(result.images, ticId, sector));
    } catch (e: any) {
      setAlbumError(e.message || String(e));
    } finally {
      setAlbumLoading(false);
    }
  };

  return (
    <section className="bg-white rounded-lg shadow p-5 space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-bold">Diagnostic plots</h3>
        <button
          onClick={uploadAll}
          disabled={albumLoading}
          className="text-xs px-3 py-1.5 bg-purple-600 text-white rounded hover:bg-purple-700 disabled:bg-slate-300 flex items-center gap-1"
        >
          {albumLoading ? (
            "Uploading…"
          ) : (
            <>
              <ShareIcon />
              Upload all to ImgBB
            </>
          )}
        </button>
      </div>

      {/* ExoFOP-TESS bulk-upload ZIP builder. Spec:
          https://exofop.ipac.caltech.edu/tess/script_upload_help.php */}
      <details className="rounded border border-slate-200 bg-slate-50 p-3 text-xs">
        <summary className="font-semibold text-slate-700 cursor-pointer">
          ⬇ Build ExoFOP-TESS bulk-upload ZIP
        </summary>
        <p className="text-slate-500 mt-2">
          Packages every diagnostic plot (PNG) into a single{" "}
          <code>xxYYYYMMDD-nnn.zip</code> with a matching{" "}
          <code>.txt</code> descriptor, ready for the{" "}
          <a
            className="text-blue-600 hover:underline"
            href="https://exofop.ipac.caltech.edu/tess/script_upload_help.php"
            target="_blank"
            rel="noopener noreferrer"
          >
            ExoFOP-TESS bulk file upload form
          </a>
          . Initials + data tag are remembered between runs; counter
          auto-bumps after each build.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-[auto_auto_1fr_auto] gap-2 mt-3 items-end">
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-600">Initials (xx)</span>
            <input
              type="text"
              value={exoInitials}
              onChange={(e) => setExoInitials(e.target.value)}
              placeholder="mc"
              maxLength={6}
              className="w-20 px-2 py-1 border border-slate-300 rounded font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-600">Counter (nnn)</span>
            <input
              type="text"
              value={exoCounter}
              onChange={(e) => setExoCounter(e.target.value)}
              maxLength={3}
              className="w-16 px-2 py-1 border border-slate-300 rounded font-mono"
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-slate-600">
              Data tag{" "}
              <a
                className="text-blue-600 hover:underline"
                href="https://exofop.ipac.caltech.edu/tess/help.php#upload"
                target="_blank"
                rel="noopener noreferrer"
              >
                (must be valid on your ExoFOP account)
              </a>
            </span>
            <input
              type="text"
              value={exoTag}
              onChange={(e) => setExoTag(e.target.value)}
              placeholder="20180301_yourname_vetstar_00000000000001"
              className="px-2 py-1 border border-slate-300 rounded font-mono"
            />
          </label>
          <button
            onClick={bulkDownload}
            disabled={bulkBusy}
            className="text-xs px-3 py-1.5 bg-slate-700 text-white rounded hover:bg-slate-800 disabled:bg-slate-300 self-end"
          >
            {bulkBusy ? "Building…" : "Build ZIP"}
          </button>
        </div>
        {bulkError && (
          <p className="mt-2 text-red-700 bg-red-50 border border-red-200 rounded p-2">
            {bulkError}
          </p>
        )}
      </details>

      {albumError && (
        <p className="text-xs text-red-700 bg-red-50 rounded p-2">
          ImgBB error: {albumError}
        </p>
      )}

      {albumResult && (
        <div className="bg-purple-50 border border-purple-200 rounded p-3 space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-purple-900">
              Plots uploaded to ImgBB
            </span>
            <span className="text-xs text-purple-600">
              {albumResult.images.length} image{albumResult.images.length !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="grid gap-1">
            {albumResult.images.map((img: any) => (
              <CopyField
                key={img.name}
                label={img.label}
                value={img.link}
              />
            ))}
          </div>
          {/* Forum BBCode ready to paste */}
          {forumText && (
            <details className="mt-2">
              <summary className="text-xs text-purple-700 cursor-pointer hover:underline">
                Copy BBCode for Planet Hunters / forum post
              </summary>
              <div className="mt-1 relative">
                <textarea
                  readOnly
                  value={forumText}
                  className="w-full h-32 text-xs font-mono bg-white border rounded p-2 text-slate-700"
                />
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(forumText);
                  }}
                  className="absolute top-1 right-1 text-[10px] px-2 py-0.5 bg-purple-600 text-white rounded hover:bg-purple-700"
                >
                  Copy
                </button>
              </div>
            </details>
          )}
        </div>
      )}

      {order.map((k) =>
        plots[k] ? (
          <figure key={k}>
            <div className="flex items-center justify-between mb-1">
              <figcaption className="text-sm text-slate-600">
                {labels[k]}
              </figcaption>
              <ShareToImgbbButton
                base64={plots[k]}
                title={
                  ticId
                    ? `TIC ${ticId}${sector ? ` S${sector}` : ""} — ${labels[k]}`
                    : labels[k]
                }
                label={labels[k]}
              />
            </div>
            <img
              src={`data:image/png;base64,${plots[k]}`}
              alt={labels[k]}
              className="w-full rounded border"
            />
          </figure>
        ) : null
      )}
    </section>
  );
}
