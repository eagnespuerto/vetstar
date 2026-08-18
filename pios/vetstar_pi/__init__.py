"""VetStar Pi — Raspberry Pi OS port of the Vetstar TESS vetting studio.

Package layout mirrors ``backend/app/`` upstream so the vendored modules
(pipeline, microlensing, habitability, observables, tlcm_geometry, hci_image,
mast_fetch, dvt_fetch, exofop, exominer, ffi_cutout, gaia_catalog,
gaia_photometry, tic_catalog, rv_fetch, microlensing_coverage,
microlensing_ffi, tess_sector_dates, report, microlensing_report, detrend,
progress, parsers, pdf_fonts) import each other with the same relative
paths.

Two features are intentionally missing versus the cloud studio:

* multi-sector orchestration — Pi RAM budget can't hold two BLS sweeps in
  parallel; run each sector separately.
* joint TESS + Gaia PSPL fit — needs the Gaia Alerts fetch pipeline
  running online; single-band single-lens fits still work.

Command-line entry: ``python -m vetstar_pi …`` (see :mod:`vetstar_pi.cli`).
GUI entry: :func:`vetstar_pi.gui.run_gui`.
"""
__version__ = "0.2.0"
