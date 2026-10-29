import shutil
from pathlib import Path
from fastapi.testclient import TestClient
from geocoder.config import SETTINGS

FIX = Path(__file__).parent / "fixtures" / "tiles" / "mini.pmtiles"


def test_tiles_mount_serves_byte_ranges():
    tiles_dir = SETTINGS.DATA_DIR / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    dst = tiles_dir / "mini.pmtiles"
    shutil.copy(FIX, dst)
    try:
        from geocoder.api import main as appmod
        appmod.app.state._test = True
        with TestClient(appmod.app) as c:
            r = c.get("/tiles/mini.pmtiles", headers={"Range": "bytes=0-99"})
            assert r.status_code == 206
            assert len(r.content) == 100
            assert r.headers["content-range"].startswith("bytes 0-99/")
    finally:
        dst.unlink(missing_ok=True)
