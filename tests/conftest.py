"""pytest setup: make project root importable + isolate persistent state."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_merchant_cache(tmp_path, monkeypatch):
    """Every test gets a brand-new merchant cache file in its own tmp dir.

    Without this, tests that exercise enrichment will read/write the user's
    real merchants.json, leaking data across tests AND polluting the user's
    actual cache. Applies to every test in the suite via autouse.
    """
    try:
        import merchant_cache as mc
    except ImportError:
        # The cache module may not exist yet during very early test
        # collection — skip in that case.
        return
    fresh = tmp_path / "merchants_test.json"
    monkeypatch.setattr(mc, "_CACHE_PATH", fresh, raising=False)
    yield
