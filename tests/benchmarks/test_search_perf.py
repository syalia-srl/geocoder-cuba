"""Performance benchmarks for search pipeline."""
import pytest
import random
import time
from geocoder.store import GeoStore
from geocoder.search.pipeline import SearchPipeline
from geocoder.config import SETTINGS


@pytest.fixture(scope="module")
def pipeline():
    """Create search pipeline for benchmarking."""
    if not SETTINGS.DB_PATH.exists():
        pytest.skip("Database not found")
    store = GeoStore(SETTINGS.DB_PATH)
    return SearchPipeline(store)


def get_test_queries():
    """Get a set of test queries for benchmarking."""
    return [
        "Calle 23",
        "Calle 23 415",
        "23 y 12",
        "Hotel Nacional",
        "Plaza de la Revolución",
        "Avenida de los Presidentes",
        "Vedado",
        "municipio:Plaza Calle 23",
        "barrio:Vedado hotel",
        "Calle 12",
        "Calle 12 100",
        "12 y 23",
        "Avenida Independencia",
        "Marianao",
        "Playa",
        "Cerro",
        "Centro Habana",
        "Habana Vieja",
        "Habana del Este",
        "Regla",
        "Guanabacoa",
        "San Miguel del Padrón",
        "La Lisa",
        "Boyeros",
        "Arroyo Naranjo",
        "Cotorro",
        "Diez de Octubre",
    ]


@pytest.mark.benchmark(
    group="search",
    min_rounds=10,
    min_time=1.0,
    timer=time.perf_counter,
    disable_gc=True,
    warmup=False
)
def test_search_performance(benchmark, pipeline):
    """Benchmark search pipeline performance."""
    queries = get_test_queries()
    
    def run_queries():
        for q in queries:
            pipeline.resolve(q, limit=5)
    
    benchmark(run_queries)


@pytest.mark.benchmark(
    group="search",
    min_rounds=50,
    timer=time.perf_counter
)
def test_single_query_latency(benchmark, pipeline):
    """Benchmark single query latency."""
    queries = get_test_queries()
    query = random.choice(queries)
    
    def single_search():
        pipeline.resolve(query, limit=5)
    
    benchmark(single_search)


def test_search_latency_stats(pipeline):
    """Run multiple queries and report latency statistics."""
    queries = get_test_queries() * 4  # 100 queries
    
    latencies = []
    for q in queries:
        start = time.perf_counter()
        pipeline.resolve(q, limit=5)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        latencies.append(elapsed)
    
    latencies.sort()
    n = len(latencies)
    
    print(f"\nSearch Latency Statistics ({n} queries):")
    print(f"  Min:    {latencies[0]:.2f}ms")
    print(f"  P50:    {latencies[n//2]:.2f}ms")
    print(f"  P90:    {latencies[int(n*0.9)]:.2f}ms")
    print(f"  P95:    {latencies[int(n*0.95)]:.2f}ms")
    print(f"  P99:    {latencies[int(n*0.99)]:.2f}ms")
    print(f"  Max:    {latencies[-1]:.2f}ms")
    print(f"  Mean:   {sum(latencies)/n:.2f}ms")
    
    # Sanity ceiling (not a strict SLA — depends on corpus size / hardware).
    # On a real ~65k-lugar Habana corpus, forward-search P95 is a few hundred ms.
    assert latencies[int(n*0.95)] < 2000, f"P95 latency {latencies[int(n*0.95)]:.2f}ms exceeds 2000ms ceiling"


if __name__ == "__main__":
    # Run manually
    if SETTINGS.DB_PATH.exists():
        store = GeoStore(SETTINGS.DB_PATH)
        pipeline = SearchPipeline(store)
        test_search_latency_stats(pipeline)
    else:
        print("Database not found. Run 'geocoder build' first.")