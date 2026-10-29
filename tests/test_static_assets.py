from pathlib import Path

STATIC = Path(__file__).parent.parent / "src" / "geocoder" / "api" / "static"
FONTSTACK = "Inter Medium"  # MapLibre `text-font` name == the glyph directory name


def test_maplibre_and_pmtiles_are_vendored():
    assert (STATIC / "vendor" / "maplibre" / "maplibre-gl.js").exists()
    assert (STATIC / "vendor" / "maplibre" / "maplibre-gl.css").exists()
    assert (STATIC / "vendor" / "pmtiles" / "pmtiles.js").exists()
    html = (STATIC / "index.html").read_text()
    assert "/static/vendor/maplibre/maplibre-gl.js" in html
    assert "/static/vendor/pmtiles/pmtiles.js" in html
    appjs = (STATIC / "app.js").read_text()
    assert "protomapsL" not in appjs  # engine actually swapped
    assert "new maplibregl.Map" in appjs


def test_fonts_are_vendored_and_referenced():
    fonts = STATIC / "vendor" / "fonts"
    assert (fonts / "fonts.css").exists()
    assert list(fonts.glob("*.woff2")), "no woff2 files downloaded"
    css = (fonts / "fonts.css").read_text()
    assert "https://" not in css, "fonts.css still points at remote URLs"
    html = (STATIC / "index.html").read_text()
    assert "/static/vendor/fonts/fonts.css" in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_old_map_engines_removed():
    assert not (STATIC / "vendor" / "leaflet").exists()
    assert not (STATIC / "vendor" / "protomaps").exists()
    css = (STATIC / "style.css").read_text()
    assert ".leaflet-" not in css, "style.css still targets Leaflet class names"


def test_glyphs_are_vendored_and_wired():
    glyphs = STATIC / "vendor" / "glyphs" / FONTSTACK
    # Latin-1 covers the accents Cuban place names use (ñ, á, é, í, ó, ú, ü).
    assert (glyphs / "0-255.pbf").exists()
    assert (glyphs / "256-511.pbf").exists()
    appjs = (STATIC / "app.js").read_text()
    assert "/static/vendor/glyphs/{fontstack}/{range}.pbf" in appjs
    assert f'"text-font": ["{FONTSTACK}"]' in appjs
    assert '"source-layer": "places"' in appjs


def test_ui_has_no_external_resources():
    # No CDN/remote resource hosts in the served HTML/JS (attribution text is
    # fine; we only flag the resource hosts we vendored away).
    html = (STATIC / "index.html").read_text()
    appjs = (STATIC / "app.js").read_text()
    for needle in ("unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com",
                   "basemaps.cartocdn.com"):
        assert needle not in html, f"{needle} still in index.html"
        assert needle not in appjs, f"{needle} still in app.js"


def test_ui_supports_permalinks():
    appjs = (STATIC / "app.js").read_text()
    assert "history.replaceState" in appjs
    assert 'searchParams.set("q"' in appjs
    assert 'searchParams.set("at"' in appjs


def test_lote_mode_is_wired_and_offline():
    """Lote reads the spreadsheet in the browser; it must ship with the UI and
    pull in no remote library (the UI runs air-gapped)."""
    html = (STATIC / "index.html").read_text()
    lote = (STATIC / "lote.js").read_text()
    assert "/static/lote.js" in html and 'data-mode="lote"' in html
    assert "DecompressionStream" in lote and "/api/v1/resolve/batch" in lote
    assert "https://" not in lote.replace("http://schemas.openxmlformats.org", "")
    views = (STATIC / "lote-views.js").read_text()
    assert "/static/lote-views.js" in html and 'data-view="zone"' in html
    assert "/api/v1/admin/geometries" in views and "https://" not in views


def test_isochrone_ui_is_wired_and_offline():
    html = (STATIC / "index.html").read_text()
    iso = (STATIC / "iso.js").read_text()
    assert "/static/iso.js" in html and 'id="iso-go"' in html
    assert "/api/v1/isochrone" in iso and "https://" not in iso
    assert "clearIso()" in (STATIC / "app.js").read_text()   # a new result clears the bands


def test_coverage_ui_is_wired_and_offline():
    html = (STATIC / "index.html").read_text()
    cov = (STATIC / "lote-coverage.js").read_text()
    assert "/static/lote-coverage.js" in html and 'data-view="coverage"' in html
    assert "/api/v1/coverage" in cov and "https://" not in cov
    assert "loteMapClick" in (STATIC / "app.js").read_text()   # map clicks reach Cobertura


def test_dbscan_ui_is_wired_and_offline():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "lote-dbscan.js").read_text()
    assert "/static/lote-dbscan.js" in html and 'data-mode="analysis"' in html
    assert "/api/v1/cluster" in js and "https://" not in js
