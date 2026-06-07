/**
 * Minimal store-only ZIP writer.
 *
 * No compression — PNG/JPEG payloads are already compressed, so DEFLATE
 * adds bytes for no gain. Keeps the implementation small enough to avoid
 * a third-party dependency.
 *
 * Spec reference: APPNOTE.TXT v6.3.10 (PKWARE), sections 4.3 (local file
 * header), 4.4 (central directory), and 4.4.4 (End Of Central Directory).
 */

function makeCrc32Table(): Uint32Array {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[i] = c >>> 0;
  }
  return table;
}
const CRC_TABLE = makeCrc32Table();

function crc32(data: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

export interface ZipEntry {
  /** Path inside the archive (forward slashes only, UTF-8). */
  name: string;
  /** Raw bytes to store. */
  data: Uint8Array;
}

/**
 * Build a store-only (method = 0) ZIP archive from the given entries
 * and return it as a Blob (mime: application/zip).
 */
export function buildZip(entries: ZipEntry[]): Blob {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = enc.encode(entry.name);
    const crc = crc32(entry.data);
    const size = entry.data.length;

    // Local file header (signature 0x04034b50). 30 bytes + filename.
    const local = new Uint8Array(30 + nameBytes.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);   // version needed to extract
    lv.setUint16(6, 0x0800, true); // flags: bit 11 = UTF-8 filename
    lv.setUint16(8, 0, true);    // method: 0 = store
    lv.setUint16(10, 0, true);   // mod time (omitted)
    lv.setUint16(12, 0x0021, true); // mod date (1980-01-01)
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true); // compressed size
    lv.setUint32(22, size, true); // uncompressed size
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);   // extra field length
    local.set(nameBytes, 30);
    parts.push(local);
    parts.push(entry.data);

    // Central directory header (signature 0x02014b50). 46 bytes + filename.
    const cdr = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(cdr.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);   // version made by
    cv.setUint16(6, 20, true);   // version needed
    cv.setUint16(8, 0x0800, true); // flags
    cv.setUint16(10, 0, true);   // method
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0x0021, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, size, true);
    cv.setUint32(24, size, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);   // extra field length
    cv.setUint16(32, 0, true);   // comment length
    cv.setUint16(34, 0, true);   // disk number
    cv.setUint16(36, 0, true);   // internal attrs
    cv.setUint32(38, 0, true);   // external attrs
    cv.setUint32(42, offset, true); // local header offset
    cdr.set(nameBytes, 46);
    central.push(cdr);

    offset += local.length + size;
  }

  const centralSize = central.reduce((acc, p) => acc + p.length, 0);

  // End of Central Directory record (signature 0x06054b50). 22 bytes.
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);    // disk
  ev.setUint16(6, 0, true);    // disk with central dir start
  ev.setUint16(8, entries.length, true);
  ev.setUint16(10, entries.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);
  ev.setUint16(20, 0, true);   // comment length

  // Cast through unknown to satisfy stricter Uint8Array<ArrayBufferLike>
  // typings in newer @types/node — the runtime accepts Uint8Array fine.
  const blobParts = [...parts, ...central, eocd] as unknown as BlobPart[];
  return new Blob(blobParts, { type: "application/zip" });
}
