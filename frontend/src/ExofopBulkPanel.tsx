import { useState } from "react";
import { buildZip } from "./zip";

function triggerDownload(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** One image to bundle into the ExoFOP archive. */
export interface ExofopImage {
  /** Short slug used inside the filename (no spaces, no dots, no slashes). */
  key: string;
  /** Human-readable label used in the descriptor "description" column. */
  label: string;
  /** PNG bytes, base64-encoded (no data-URI prefix). */
  b64: string;
  /** ExoFOP single-letter file-type code. L=Light curve, O=Other. Default O. */
  code?: string;
}

interface Props {
  ticId?: number | null;
  /** Single-sector context, used in the description column. Optional. */
  sector?: number | null;
  /** Source list of bundleable images. May be empty until plots have loaded. */
  images: ExofopImage[];
  /** Wording on the summary/details title. */
  title?: string;
  /** Optional caption shown above the form. */
  caption?: React.ReactNode;
}

/**
 * Reusable ExoFOP-TESS bulk-upload ZIP builder.
 *
 * Packages PNG diagnostic plots into a single ``xxYYYYMMDD-nnn.zip`` with a
 * matching ``.txt`` descriptor file, per the ExoFOP-TESS bulk file upload
 * spec (https://exofop.ipac.caltech.edu/tess/script_upload_help.php).
 *
 * Initials + data tag are persisted in localStorage so a frequent uploader
 * sets them once; the counter auto-bumps after each build within the day.
 */
export default function ExofopBulkPanel({
  ticId,
  sector,
  images,
  title = "⬇ Build ExoFOP-TESS bulk-upload ZIP",
  caption,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [initials, setInitials] = useState<string>(
    () => localStorage.getItem("vetstar.exofop.initials") || "",
  );
  const [counter, setCounter] = useState<string>("001");
  const [tag, setTag] = useState<string>(
    () => localStorage.getItem("vetstar.exofop.tag") || "",
  );

  const run = async () => {
    setError(null);
    if (!ticId) {
      setError(
        "Needs a TIC ID — run via the MAST tab or upload a SPOC FITS with a TICID header.",
      );
      return;
    }
    const ini = initials.trim().toLowerCase();
    if (!/^[a-z]{2,}$/.test(ini)) {
      setError("Initials must be 2+ letters (e.g. mc) — used in zip + file names.");
      return;
    }
    const cnt = counter.trim().padStart(3, "0");
    if (!/^\d{3}$/.test(cnt) || cnt === "000") {
      setError("Counter must be 001–999.");
      return;
    }
    const tg = tag.trim();
    if (!tg) {
      setError(
        "Data tag is required (column 2 of the descriptor). Must be valid for your ExoFOP account.",
      );
      return;
    }
    if (images.length === 0) {
      setError("No plots available to bundle yet.");
      return;
    }

    localStorage.setItem("vetstar.exofop.initials", ini);
    localStorage.setItem("vetstar.exofop.tag", tg);

    const now = new Date();
    const yyyymmdd =
      `${now.getFullYear()}` +
      String(now.getMonth() + 1).padStart(2, "0") +
      String(now.getDate()).padStart(2, "0");
    const archiveBase = `${ini}${yyyymmdd}-${cnt}`;

    setBusy(true);
    try {
      const seen = new Set<string>();
      const planned: { key: string; label: string; filename: string; bytes: Uint8Array }[] = [];
      for (const img of images) {
        if (!img.b64) continue;
        const code = img.code || "O";
        const safeKey = img.key.replace(/_/g, "-");
        let filename = `TIC${ticId}${code}-${ini}${yyyymmdd}.${safeKey}.png`;
        // 100-char cap per spec §1.
        if (filename.length > 100) {
          const maxKey =
            100 - `TIC${ticId}${code}-${ini}${yyyymmdd}..png`.length;
          filename = `TIC${ticId}${code}-${ini}${yyyymmdd}.${safeKey.slice(0, Math.max(maxKey, 1))}.png`;
        }
        // Disambiguate collisions with a/b/c suffixes.
        if (seen.has(filename)) {
          let suffix = "a";
          while (seen.has(filename.replace(/\.png$/, `${suffix}.png`))) {
            suffix = String.fromCharCode(suffix.charCodeAt(0) + 1);
          }
          filename = filename.replace(/\.png$/, `${suffix}.png`);
        }
        seen.add(filename);
        planned.push({
          key: img.key,
          label: img.label,
          filename,
          bytes: b64ToBytes(img.b64),
        });
      }

      if (planned.length === 0) {
        setError("No plots available to bundle yet.");
        return;
      }

      const descriptorLines = planned.map(
        (x) =>
          `${x.filename}|${tg}||0|Vetstar — ${x.label}${sector ? ` (Sector ${sector})` : ""}`,
      );
      const descriptorBytes = new TextEncoder().encode(
        descriptorLines.join("\n") + "\n",
      );

      const zipBlob = buildZip([
        { name: `${archiveBase}.txt`, data: descriptorBytes },
        ...planned.map((x) => ({ name: x.filename, data: x.bytes })),
      ]);
      triggerDownload(zipBlob, `${archiveBase}.zip`);

      const nextN = Math.min(parseInt(cnt, 10) + 1, 999);
      setCounter(String(nextN).padStart(3, "0"));
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="rounded border border-slate-200 bg-slate-50 p-3 text-xs">
      <summary className="font-semibold text-slate-700 cursor-pointer">
        {title}
      </summary>
      {caption ?? (
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
      )}
      <div className="grid grid-cols-1 sm:grid-cols-[auto_auto_1fr_auto] gap-2 mt-3 items-end">
        <label className="flex flex-col gap-0.5">
          <span className="text-slate-600">Initials (xx)</span>
          <input
            type="text"
            value={initials}
            onChange={(e) => setInitials(e.target.value)}
            placeholder="mc"
            maxLength={6}
            className="w-20 px-2 py-1 border border-slate-300 rounded font-mono"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-slate-600">Counter (nnn)</span>
          <input
            type="text"
            value={counter}
            onChange={(e) => setCounter(e.target.value)}
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
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="YYYYMMDD_initials_NNN"
            className="px-2 py-1 border border-slate-300 rounded font-mono"
          />
        </label>
        <button
          onClick={run}
          disabled={busy || !ticId || images.length === 0}
          className="px-3 py-1.5 bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:bg-slate-300"
        >
          {busy ? "Building…" : `Download ZIP (${images.length})`}
        </button>
      </div>
      {error && <p className="mt-2 text-red-700">{error}</p>}
      {images.length === 0 && !error && (
        <p className="mt-2 text-slate-500 italic">
          Plots will appear here once the analysis finishes.
        </p>
      )}
    </details>
  );
}
