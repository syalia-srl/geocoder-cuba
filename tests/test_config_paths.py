"""The package must be relocatable: both airgap producers install it outside a
checkout (pip into an image, uv into a relocatable venv), where DATA_DIR's
derivation from __file__ lands somewhere meaningless like
/usr/local/lib/python3.12/data and the /tiles mount silently 404s."""
import os
import subprocess
import sys

_PROBE = "from geocoder.config import SETTINGS; print(SETTINGS.DATA_DIR)"


def _data_dir(env=None):
    e = dict(os.environ)
    e.pop("GEOCODER_DATA_DIR", None)
    e.update(env or {})
    out = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True,
                         text=True, check=True, env=e)
    return out.stdout.strip()


def test_data_dir_defaults_to_the_repo_layout():
    assert _data_dir().endswith("/data")


def test_data_dir_is_overridable(tmp_path):
    target = tmp_path / "baked"
    assert _data_dir({"GEOCODER_DATA_DIR": str(target)}) == str(target)
