"""Routing-graph warming must not gate startup.

Building the 352k-node graph + edge index takes ~70 s on full Cuba. Doing it
inside the lifespan means /health does not answer for ~90 s, so an operator sees
"deployed", gets nothing, and concludes it is broken. Warm it in the background
instead: geocoding answers immediately, routing becomes available shortly after.
"""
import tempfile
import threading

from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline


def _pipe():
    return SearchPipeline(GeoStore(tempfile.mktemp(suffix=".db")))


def test_router_is_built_once_under_concurrency():
    """The background warm and an early request must not both build it."""
    p = _pipe()
    got = []
    barrier = threading.Barrier(4)

    def grab():
        barrier.wait()
        got.append(p.router())

    ts = [threading.Thread(target=grab) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert len(got) == 4
    assert all(r is got[0] for r in got), "router built more than once"


def test_router_ready_reports_state():
    p = _pipe()
    assert p.router_ready is False
    p.router()
    assert p.router_ready is True


def test_warm_router_async_returns_a_live_thread():
    p = _pipe()
    t = p.warm_router_async()
    assert isinstance(t, threading.Thread) and t.daemon
    t.join(timeout=30)
    assert not t.is_alive()
    assert p.router_ready is True


def test_street_tree_is_built_once_under_concurrency():
    """81 s to build on full Cuba, and a map click can land mid-warm — without a
    lock the warm thread and the request would each build their own."""
    import tempfile
    s = GeoStore(tempfile.mktemp(suffix=".db"))
    s.put_calle_geom("l", None, "LINESTRING(-82.385 23.130, -82.385 23.150)")
    got = []
    barrier = threading.Barrier(4)

    def grab():
        barrier.wait()
        got.append(s.street_tree())

    ts = [threading.Thread(target=grab) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(got) == 4
    assert all(g is got[0] for g in got), "street tree built more than once"
    s.close()
