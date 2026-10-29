"""Download OSM PBF from Geofabrik with checksum verification."""
import hashlib
import shutil
import urllib.request
from pathlib import Path
from geocoder.config import SETTINGS


def download_pbf(force: bool = False) -> Path:
    """
    Download Cuba PBF from Geofabrik.
    
    Args:
        force: Force re-download even if file exists.
        
    Returns:
        Path to downloaded PBF file.
    """
    SETTINGS.ensure_dirs()
    pbf_path = SETTINGS.PBF_PATH
    
    if pbf_path.exists() and not force:
        print(f"PBF already exists: {pbf_path}")
        return pbf_path
    
    print(f"Downloading {SETTINGS.PBF_URL}...")
    urllib.request.urlretrieve(SETTINGS.PBF_URL, pbf_path)
    print(f"Downloaded to {pbf_path} ({pbf_path.stat().st_size / 1e6:.1f} MB)")
    
    # Save version timestamp
    version_file = SETTINGS.DATA_DIR / "versions" / f"cuba-{pbf_path.stat().st_mtime:.0f}.osm.pbf"
    shutil.copy2(pbf_path, version_file)
    
    return pbf_path


def verify_md5(pbf_path: Path, expected_md5: str = None) -> bool:
    """
    Verify MD5 checksum of PBF file.
    
    Args:
        pbf_path: Path to PBF file.
        expected_md5: Expected MD5 hash (optional).
        
    Returns:
        True if checksum matches or no expected hash provided.
    """
    if expected_md5 is None:
        print("No expected MD5 provided, skipping verification")
        return True
    
    print("Verifying MD5 checksum...")
    hash_md5 = hashlib.md5()
    with open(pbf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_md5.update(chunk)
    
    actual_md5 = hash_md5.hexdigest()
    if actual_md5 == expected_md5:
        print("MD5 verified")
        return True
    else:
        print(f"MD5 mismatch: expected {expected_md5}, got {actual_md5}")
        return False


if __name__ == "__main__":
    download_pbf()