"""Every served asset must survive `pip install`.

`api/static/*` matches only the top level, so the whole vendored tree
(maplibre, pmtiles.js, fonts, SDF glyphs) was silently absent from installed
packages while every test passed against the source tree. The airgap image
rendered a blank map. Assert the declared patterns actually cover the files.
"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
PKG = ROOT / "src" / "geocoder"


def _patterns():
    with open(ROOT / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    return cfg["tool"]["setuptools"]["package-data"]["geocoder"]


def _covered_set(patterns) -> set:
    """Exactly what setuptools resolves: glob(pattern, recursive=True) rooted at
    the package dir. Path.match() is NOT equivalent — it does not expand `**` —
    so matching by hand here would test a different thing than the build does.
    """
    import glob as _glob
    out = set()
    for p in patterns:
        for hit in _glob.glob(str(PKG / p), recursive=True):
            out.add(Path(hit).relative_to(PKG))
    return out


def _covered(rel: Path, patterns) -> bool:
    return rel in _covered_set(patterns)


def test_every_static_asset_is_declared_package_data():
    patterns = _patterns()
    missing = [
        str(p.relative_to(PKG))
        for p in (PKG / "api" / "static").rglob("*")
        if p.is_file() and not _covered(p.relative_to(PKG), patterns)
    ]
    assert not missing, (
        f"{len(missing)} static asset(s) would not be installed, e.g. {missing[:5]}"
    )


def test_the_vendored_map_engine_is_covered():
    patterns = _patterns()
    for rel in (
        "api/static/vendor/maplibre/maplibre-gl.js",
        "api/static/vendor/pmtiles/pmtiles.js",
        "api/static/vendor/glyphs/Inter Medium/0-255.pbf",
        "api/static/vendor/fonts/fonts.css",
    ):
        assert (PKG / rel).exists(), f"{rel} missing from the source tree"
        assert _covered(Path(rel), patterns), f"{rel} not covered by package-data"
