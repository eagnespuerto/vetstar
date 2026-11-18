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

/** One file to bundle into the ExoFOP archive (a PNG plot by default). */
export interface ExofopImage {
  /** Short slug used inside the filename (no spaces, no dots, no slashes). */
  key: string;
  /** Human-readable label used in the descriptor "description" column. */
  label: string;
  /** File bytes, base64-encoded (no data-URI prefix). Empty when `url` is set. */
  b64: string;
  /** ExoFOP single-letter file-type code: F finding chart, I image,
   *  L light curve, P photometry, S spectrum, O other. Default O. */
  code?: string;
  /** File extension without the dot. Default "png". */
  ext?: string;
  /** Produce the bytes at build time instead of `b64` (e.g. a PDF report). */
  fetchBytes?: () => Promise<Uint8Array>;
}

/** ExoFOP file-type codes (Bulk File Upload Instructions, section 3). */
const EXOFOP_CODES = new Set(["F", "I", "L", "P", "S", "O"]);

/** UT date as YYYYMMDD. The spec's DATESTAMP is the *UT* date of the file. */
export function utDateStamp(d = new Date()): string {
  return `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, "0")}${String(d.getUTCDate()).padStart(2, "0")}`;
}

/** Descriptor column text: plain ASCII, no pipes (pipes delimit columns). */
export function descriptorText(s: string): string {
  return s
    .replace(/[–—]/g, "-")
    .replace(/R⊕/g, "R_Earth")
    .replace(/⊕/g, "Earth")
    .replace(/[|\r\n]+/g, " ")
    .normalize("NFKD")
    .replace(/[^\x20-\x7E]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Optional-info part of a filename: letters, digits, dots and dashes only. */
export function filenameInfo(s: string): string {
  return s.replace(/[^A-Za-z0-9.-]+/g, "-").replace(/-+/g, "-").replace(/^[-.]+|[-.]+$/g, "");
}

export interface ExofopArchive {
  zipName: string;
  zip: Blob;
  /** Descriptor + files, in archive order (for checks and tests). */
  entries: { name: string; data: Uint8Array }[];
}

/**
 * Build an ExoFOP-TESS bulk-upload archive (Bulk File Upload Instructions):
 * `xxYYYYMMDD-nnn.zip` holding a same-named `.txt` descriptor and the files,
 * flat, each named `TIC<id><C>-xx<UT date>.<info>[y].<ext>`. Returns null
 * when there is nothing to bundle. Inputs are assumed already validated.
 */
export async function buildExofopArchive(opts: {
  ticId: number; sector?: number | null; initials: string; counter: string; tag: string;
  images: ExofopImage[]; now?: Date;
  /** Optional person credited in each description, with the initials. */
  author?: string;
}): Promise<ExofopArchive | null> {
  const { ticId, sector, initials: ini, counter: cnt, tag: tg, images } = opts;
  const credit = opts.author?.trim() ? `, by ${creditLine(opts.author, ini)}` : "";
  // Spec: archive date = creation date, file DATESTAMP = UT date. Use UT for both.
  const yyyymmdd = utDateStamp(opts.now);
  const archiveBase = `${ini}${yyyymmdd}-${cnt}`;
  const seen = new Set<string>();
  const planned: { label: string; filename: string; bytes: Uint8Array }[] = [];
  for (const img of images) {
    if (!img.b64 && !img.fetchBytes) continue;
    const code = (img.code || "O").toUpperCase();
    if (!EXOFOP_CODES.has(code)) throw new Error(`Unknown ExoFOP file-type code "${code}".`);
    const ext = (img.ext || "png").replace(/^\./, "").toLowerCase();
    const safeKey = filenameInfo(img.key.replace(/_/g, "-"));
    const stem = `TIC${ticId}${code}-${ini}${yyyymmdd}`;
    let filename = `${stem}.${safeKey}.${ext}`;
    // Keep names short (ExoFOP has no stated cap; 100 keeps them readable).
    if (filename.length > 100) {
      const maxKey = 100 - `${stem}..${ext}`.length;
      filename = `${stem}.${safeKey.slice(0, Math.max(maxKey, 1))}.${ext}`;
    }
    // Spec [y]: a letter after the optional info to tell duplicates apart.
    if (seen.has(filename)) {
      const base = filename.slice(0, -(ext.length + 1));
      let suffix = "a";
      while (seen.has(`${base}${suffix}.${ext}`)) suffix = String.fromCharCode(suffix.charCodeAt(0) + 1);
      filename = `${base}${suffix}.${ext}`;
    }
    let bytes: Uint8Array;
    if (img.fetchBytes) {
      try {
        bytes = await img.fetchBytes();
      } catch (e: any) {
        throw new Error(`Could not get ${img.label}: ${e?.message || e}`);
      }
    } else {
      bytes = b64ToBytes(img.b64);
    }
    seen.add(filename);
    planned.push({ label: img.label, filename, bytes });
  }
  if (planned.length === 0) return null;

  // Five pipe-delimited columns: file | data tag | group | proprietary months | description.
  // No group, so the proprietary period must be 0.
  const descriptor = planned.map((x) =>
    `${x.filename}|${descriptorText(tg)}||0|${descriptorText(`Vetstar - ${x.label}${sector ? ` (Sector ${sector})` : ""}${credit}`)}`,
  ).join("\n") + "\n";
  const entries = [
    { name: `${archiveBase}.txt`, data: new TextEncoder().encode(descriptor) },
    ...planned.map((x) => ({ name: x.filename, data: x.bytes })),
  ];
  return { zipName: `${archiveBase}.zip`, zip: buildZip(entries), entries };
}

const LS_NAME = "vetstar.exofop.name";
const LS_INITIALS = "vetstar.exofop.initials";

function lsGet(k: string): string {
  try { return localStorage.getItem(k) || ""; } catch { return ""; }
}
function lsSet(k: string, v: string) {
  // An empty value removes the entry, so clearing a box really forgets it.
  try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* private mode: not remembered */ }
}

/** Suggested ExoFOP initials from a name: "Ella Agnes Puerto" -> "eap". */
export function initialsFromName(name: string): string {
  return name
    .normalize("NFKD").replace(/[^\p{L}\s.-]/gu, "")
    .split(/[\s.-]+/).filter(Boolean)
    .map((w) => w[0]).join("").toLowerCase()
    .replace(/[^a-z]/g, "");
}

/** "E. A. Puerto (ea)", "E. A. Puerto", "(ea)" or "" for credits. */
export function creditLine(name: string, initials: string): string {
  const n = name.trim(), i = initials.trim().toLowerCase();
  return n && i ? `${n} (${i})` : n || (i ? `(${i})` : "");
}

export interface ExofopIdentity {
  name: string;
  setName: (v: string) => void;
  initials: string;
  setInitials: (v: string) => void;
}

/**
 * Your name (optional) and ExoFOP initials, remembered in this browser and
 * shared by every export (ZIP descriptions, file names, PDF cover). Typing a
 * name fills in suggested initials while the initials box is empty.
 */
export function useExofopIdentity(): ExofopIdentity {
  const [name, setNameState] = useState(() => lsGet(LS_NAME));
  const [initials, setInitialsState] = useState(() => lsGet(LS_INITIALS));
  const [autoInitials, setAutoInitials] = useState(() => !lsGet(LS_INITIALS));
  const setName = (v: string) => {
    setNameState(v); lsSet(LS_NAME, v.trim());
    if (autoInitials) { const s = initialsFromName(v); setInitialsState(s); lsSet(LS_INITIALS, s); }
  };
  const setInitials = (v: string) => {
    setInitialsState(v); lsSet(LS_INITIALS, v.trim().toLowerCase());
    setAutoInitials(v.trim() === "");
  };
  return { name, setName, initials, setInitials };
}

/** Name + initials inputs, used by the ZIP builder and the NEMESIS export card. */
export function IdentityFields({ identity, compact = false }: { identity: ExofopIdentity; compact?: boolean }) {
  return (
    <>
      <label className="flex flex-col gap-0.5">
        <span className="text-slate-600">Your name <span className="text-slate-400">(optional)</span></span>
        <input
          type="text"
          value={identity.name}
          onChange={(e) => identity.setName(e.target.value)}
          placeholder="E. A. Puerto"
          maxLength={60}
          autoComplete="name"
          className={`${compact ? "w-44" : "w-56"} px-2 py-1 border border-slate-300 rounded`}
        />
      </label>
      <label className="flex flex-col gap-0.5">
        <span className="text-slate-600">Initials (xx)</span>
        <input
          type="text"
          value={identity.initials}
          onChange={(e) => identity.setInitials(e.target.value)}
          placeholder="mc"
          maxLength={6}
          className="w-20 px-2 py-1 border border-slate-300 rounded font-mono"
        />
      </label>
      <p className="basis-full text-slate-500 text-[11px] leading-snug" role="note">
        Vetstar does not record your name or initials. They are used only to build your files: the
        "Prepared by" line on the PDF and the ExoFOP file names and descriptions in the ZIP. The ZIP is
        made in your browser; for the PDF they are sent with that one request and not stored. Your browser
        remembers them on this device so you don't retype them; clear the boxes to forget them.
      </p>
    </>
  );
}

interface Props {
  ticId?: number | null;
  /** Shared name + initials; when given, the panel does not show its own fields. */
  identity?: ExofopIdentity;
  /** Single-sector context, used in the description column. Optional. */
  sector?: number | null;
  /** Source list of bundleable images. May be empty until plots have loaded. */
  images: ExofopImage[];
  /** Wording on the summary/details title. */
  title?: string;
  /** Optional caption shown above the form. */
  caption?: React.ReactNode;
  /** Starting archive counter (nnn). Give a second ZIP on the same page "002"
   *  so two uploads on the same UT day don't share a name. */
  defaultCounter?: string;
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
  identity: sharedIdentity,
  defaultCounter = "001",
}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ownIdentity = useExofopIdentity();
  const identity = sharedIdentity ?? ownIdentity;
  const initials = identity.initials;
  const [counter, setCounter] = useState<string>(defaultCounter);
  const [tag, setTag] = useState<string>(() => lsGet("vetstar.exofop.tag"));

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

    lsSet(LS_INITIALS, ini);
    lsSet("vetstar.exofop.tag", tg);

    setBusy(true);
    try {
      const archive = await buildExofopArchive({
        ticId, sector, initials: ini, counter: cnt, tag: tg, images, author: identity.name,
      });
      if (!archive) {
        setError("No plots available to bundle yet.");
        return;
      }
      triggerDownload(archive.zip, archive.zipName);

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
          Packages every diagnostic plot and report (PNG, PDF) into a single{" "}
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
      <div className="flex flex-wrap gap-2 mt-3 items-end">
        {!sharedIdentity && <IdentityFields identity={identity} compact />}
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
            className="min-w-[14rem] px-2 py-1 border border-slate-300 rounded font-mono"
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
