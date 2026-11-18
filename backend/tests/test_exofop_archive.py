"""ExoFOP-TESS bulk-upload compliance of Vetstar's ZIP builder.

Rules from the ExoFOP-TESS Bulk File Upload Instructions
(https://exofop.ipac.caltech.edu/tess/ExoFOP-TESS_Bulk_File_Upload_Instructions.pdf).

The archive is built by the app's own `buildExofopArchive` through
frontend/scripts/exofop-check.ts (bundled with esbuild, run with Node).
Skipped when Node or the frontend's node_modules are missing.
"""
import base64
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
ARCHIVE_RE = re.compile(r"^(?P<xx>[a-z]{2,})(?P<date>\d{8})-(?P<nnn>\d{3})\.zip$")
FILE_RE = re.compile(r"^(?P<target>TIC\d+|TOI\d+(\.\d{2})?)(?P<code>[FILPSO])-"
                     r"(?P<xx>[a-z]{2,})(?P<stamp>\d{8}|\d{5}\.\d{5})(?P<info>[A-Za-z0-9.\-]*)\.(?P<ext>[a-z0-9]+)$")
PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
    b"\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
).decode()


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    node, npx = shutil.which("node"), shutil.which("npx")
    if not node or not npx or not (FRONTEND / "node_modules" / ".bin").exists():
        pytest.skip("needs Node and frontend/node_modules")
    d = tmp_path_factory.mktemp("exofop")
    (d / "result.json").write_text(json.dumps(
        {"figures": {k: PNG for k in ("light_curve", "fold", "odd_even", "periodogram")}}))
    subprocess.run([npx, "esbuild", "scripts/exofop-check.ts", "--bundle", "--platform=node",
                    "--format=esm", "--log-level=warning", f"--outfile={d / 'check.mjs'}"],
                   cwd=FRONTEND, check=True, shell=False)
    out = subprocess.run([node, str(d / "check.mjs"), str(d / "result.json"), str(d / "a.zip")],
                         check=True, capture_output=True, text=True).stdout.split()
    return out[0], (d / "a.zip").read_bytes()


def test_archive_and_descriptor_names(archive):
    name, data = archive
    m = ARCHIVE_RE.match(name)
    assert m, name
    # 23:30 UTC on 18 Nov: UT date, not the UTC+8 local date.
    assert m["date"] == "20261118"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
    assert f"{name[:-4]}.txt" in names, "descriptor must share the archive's base name"


def test_flat_and_every_file_listed_once(archive):
    name, data = archive
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        desc = z.read(f"{name[:-4]}.txt").decode("ascii")  # also proves pure ASCII
    assert all("/" not in n and "\\" not in n for n in names), "single flat directory"
    files = [n for n in names if not n.endswith(".txt")]
    rows = [r for r in desc.splitlines() if r]
    listed = [r.split("|")[0] for r in rows]
    assert sorted(listed) == sorted(files), "files and descriptor rows must match one-to-one"
    assert len(set(files)) == len(files)


def test_file_names_follow_convention(archive):
    name, data = archive
    xx, date = ARCHIVE_RE.match(name)["xx"], ARCHIVE_RE.match(name)["date"]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = [n for n in z.namelist() if not n.endswith(".txt")]
    for f in files:
        m = FILE_RE.match(f)
        assert m, f
        assert m["target"] == "TIC103633434" and m["xx"] == xx and m["stamp"] == date
        assert " " not in f
    codes = {FILE_RE.match(f)["code"] for f in files}
    assert codes == {"L", "O"}
    assert any(f.endswith(".pdf") for f in files) and any(f.endswith(".png") for f in files)
    # The duplicated key got a distinguishing [y] letter.
    assert any(re.search(r"nemesis-vetting-reporta\.pdf$", f) for f in files)


def test_descriptor_rows(archive):
    name, data = archive
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        rows = [r for r in z.read(f"{name[:-4]}.txt").decode("ascii").splitlines() if r]
    for r in rows:
        cols = r.split("|")
        assert len(cols) == 5, r                 # five pipe-delimited columns
        assert cols[1] == "20261118_puerto_vetstar_001"   # data tag (required)
        assert cols[2] == ""                     # no group
        assert cols[3] == "0"                    # proprietary period must be 0 without a group
        assert cols[4]                           # description
    assert any("with residuals" in r for r in rows)   # "|" in a label was removed, not a 6th column
    assert any("periodogram - 1.9 R_Earth" in r for r in rows)
    # Author credit: ASCII-folded, pipe removed, with the initials, on every row.
    assert all(r.endswith(", by Ma. Liza Penaflor test (ea)") for r in rows), rows[0]   # em dash and R-circled-plus made ASCII
