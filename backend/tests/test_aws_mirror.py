from vetstar_core.aws_mirror import s3_key


def test_spoc_product_maps_to_tid_path():
    assert s3_key("mast:TESS/product/tess2019357164649-s0020-0000000103633434-0165-s_lc.fits") == (
        "tess/public/tid/s0020/0000/0001/0363/3434/tess2019357164649-s0020-0000000103633434-0165-s_lc.fits")


def test_dvt_maps_to_tid_path():
    # DVT names carry a sector range; single-sector ones sit under the first sector.
    assert s3_key("mast:TESS/product/tess2019358235523-s0020-s0020-0000000103633434-00285_dvt.fits") == (
        "tess/public/tid/s0020/0000/0001/0363/3434/tess2019358235523-s0020-s0020-0000000103633434-00285_dvt.fits")


def test_hlsp_qlp_and_tess_spoc():
    assert s3_key("mast:HLSP/qlp/s0020/0000/0001/0363/3434/hlsp_qlp_tess_ffi_s0020-0000000103633434_tess_v01_llc.fits") == (
        "mast/hlsp/qlp/s0020/0000/0001/0363/3434/hlsp_qlp_tess_ffi_s0020-0000000103633434_tess_v01_llc.fits")
    assert s3_key("mast:HLSP/tess-spoc/s0020/target/0000/0001/0363/3434/x_lc.fits") == (
        "mast/hlsp/tess-spoc/s0020/target/0000/0001/0363/3434/x_lc.fits")


def test_unknown_layout_falls_back():
    assert s3_key("mast:HLSP/cdips/s0020/cam2_ccd1/x.fits") is None
    assert s3_key("") is None
