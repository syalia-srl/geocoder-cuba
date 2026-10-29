"""
FastAPI dependencies - GeoStore and search pipeline injection.
"""
import threading

from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline

# Global instances (initialized on startup)
_store: GeoStore = None
_pipeline: SearchPipeline = None
_warm_thread = None  # background spatial-index warm; daemon, never joined


def init_dependencies(db_path: str):
    """Initialize global dependencies (called at startup)."""
    global _store, _pipeline
    _store = GeoStore(db_path)
    _pipeline = SearchPipeline(_store)
    # Warm the on-first-use caches at startup (street geometry map ~6 s, plus the
    # municipio/barrio lists used by fuzzy matching) so the first user query
    # isn't the one that pays for building them.
    _store._street_index()
    _store._munis()
    _store._barrios()
    _store.gazetteer()  # ~0.7 s (PIP over barrios) — build once at startup
    # The routing graph is the expensive one (~70 s on full Cuba: 352k nodes plus
    # an STRtree edge index). Warm it in the background rather than inline, so
    # /health answers in seconds instead of ~90 — an operator who sees a deployed
    # service that does not respond concludes it is broken. A /route arriving
    # mid-warm blocks on the lock inside pipeline.router() and gets a correct
    # answer; everything else is served throughout.
    # The street STRtree is the other expensive one (~81 s on full Cuba: 25.6k
    # WKT geometries parsed). Reverse geocoding needs it, and building it inside
    # the request would freeze the server for its duration.
    global _warm_thread
    _warm_thread = threading.Thread(
        target=_warm_indexes, name="warm-indexes", daemon=True)
    _warm_thread.start()


def _warm_indexes():
    """Build the expensive spatial indexes off the request path.

    Sequential, not parallel: both are CPU-bound, and racing them just makes
    each finish later. The street tree goes first because it is the cheaper of
    the two (~25 s vs ~70 s) and backs map clicks, which a user reaches for
    immediately; routing is a deliberate second step they take later. The
    portal map (~5 s) goes before both: every numbered address reads it.
    """
    _store.find_portal("", "")
    _store.street_tree()
    _pipeline.router()


def get_store() -> GeoStore:
    """Dependency injection for GeoStore."""
    if _store is None:
        raise RuntimeError("GeoStore not initialized. Call init_dependencies() first.")
    return _store


def get_search_pipeline() -> SearchPipeline:
    """Dependency injection for search pipeline."""
    if _pipeline is None:
        raise RuntimeError("Search pipeline not initialized. Call init_dependencies() first.")
    return _pipeline


def close_dependencies():
    """Close database connections (called at shutdown)."""
    global _store
    if _store:
        _store.close()
        _store = None
