"""CLI entry point using Typer."""
import typer
from geocoder.config import SETTINGS
from geocoder.etl.build import build_database
from geocoder.etl.download import download_pbf
import uvicorn

cli = typer.Typer(help="Cuba Geocoder - Offline geocoding for Cuba")


@cli.command()
def download(
    force: bool = typer.Option(False, "--force", "-f", help="Force re-download"),
    output: str = typer.Option(None, "--output", "-o", help="Output path")
):
    """Download Cuba OSM PBF from Geofabrik."""
    pbf = output or str(SETTINGS.PBF_PATH)
    download_pbf(force=force)
    typer.echo(f"Downloaded to {pbf}")


@cli.command()
def build(
    pbf: str = typer.Option(None, "--pbf", help="Path to PBF file"),
    bbox: str = typer.Option("habana", "--bbox", help="Bounding box: habana, full-cuba, or min_lon,min_lat,max_lon,max_lat"),
    force: bool = typer.Option(False, "--force", "-f", help="Force rebuild (drop tables)"),
    full_cuba: bool = typer.Option(False, "--full-cuba", help="Process entire Cuba")
):
    """
    Build geocoder database from OSM PBF.
    
    Bounding box options:
    - habana: La Habana province (default)
    - full-cuba: Entire Cuba
    - min_lon,min_lat,max_lon,max_lat: Custom bbox
    """
    import time
    
    # Parse bbox
    bbox_tuple = None
    if bbox == "habana":
        bbox_tuple = SETTINGS.HABANA_BBOX
    elif bbox == "full-cuba":
        bbox_tuple = None
    else:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                bbox_tuple = tuple(parts)
            else:
                raise ValueError()
        except ValueError:
            typer.echo("Invalid bbox format. Use: min_lon,min_lat,max_lon,max_lat", err=True)
            raise typer.Exit(1)
    
    pbf_path = pbf or str(SETTINGS.PBF_PATH)
    
    start = time.time()
    stats = build_database(
        pbf_path=pbf_path,
        bbox=bbox_tuple,
        force_rebuild=force,
        full_cuba=full_cuba
    )
    elapsed = time.time() - start
    
    typer.echo(f"Build completed in {elapsed:.1f}s")
    typer.echo(f"Stats: {stats}")


@cli.command()
def resolve(
    query: str = typer.Argument(..., help="Address / place / area to resolve"),
    limit: int = typer.Option(5, "--limit", "-l", help="Max results (POI/name queries)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Resolve a Cuban address / place / area → ranked results + ± radius.

    Always returns a list: one for an address/intersection/area, up to `limit`
    for a POI/name.
    """
    from geocoder.store import GeoStore
    from geocoder.search.pipeline import SearchPipeline

    store = GeoStore(SETTINGS.DB_PATH)
    res = SearchPipeline(store).resolve(query, limit=limit)
    if not res:
        typer.echo("No result")
        raise typer.Exit(1)
    if json_output:
        import json
        typer.echo(json.dumps([r.to_dict() for r in res], indent=2, ensure_ascii=False))
    else:
        typer.echo(f"\n{len(res)} result(s) for: {query}")
        for i, r in enumerate(res, 1):
            loc = ", ".join(x for x in (r.municipio, r.provincia, r.barrio) if x)
            typer.echo(f"  {i}. {r.nombre} ({r.tipo})")
            typer.echo(f"     {loc}")
            typer.echo(f"     {r.lat:.6f}, {r.lon:.6f}  |  {r.precision}  ±{r.radius_m:.0f} m")


@cli.command()
def region(
    streets: list[str] = typer.Argument(..., help="Street names bounding the region (≥3)"),
    municipio: str = typer.Option(None, "--municipio", "-m", help="Municipio scope"),
    json_output: bool = typer.Option(False, "--json", "-j", help="JSON output"),
):
    """Resolve the convex polygon enclosed by ≥3 streets."""
    from geocoder.store import GeoStore
    from geocoder.search.pipeline import SearchPipeline

    if len(streets) < 3:
        typer.echo("At least 3 streets are required")
        raise typer.Exit(1)

    store = GeoStore(SETTINGS.DB_PATH)
    reg = SearchPipeline(store).resolve_region(streets, municipio)
    if reg is None:
        typer.echo("The streets do not enclose a region")
        raise typer.Exit(1)

    if json_output:
        import json
        typer.echo(json.dumps(reg.to_dict(), indent=2, ensure_ascii=False))
    else:
        typer.echo(f"\nRegion of {len(reg.vertices)} vertices "
                   f"({', '.join(reg.streets)})")
        if reg.unresolved:
            typer.echo(f"  unresolved: {', '.join(reg.unresolved)}")
        typer.echo(f"  centroid: {reg.centroid_lat:.6f}, {reg.centroid_lon:.6f}")
        typer.echo(f"  area: {reg.area_ha:.1f} ha ({reg.area_m2:.0f} m2)")
        loc = ", ".join(x for x in (reg.municipio, reg.provincia) if x)
        if loc:
            typer.echo(f"  {loc}")


@cli.command()
def route(
    origin: str = typer.Argument(..., help="Origin address/place query"),
    destination: str = typer.Argument(..., help="Destination address/place query"),
    geojson: bool = typer.Option(False, "--geojson", help="Print route geometry as GeoJSON"),
):
    """Fastest route between two addresses over the street graph."""
    import os
    from geocoder.store import GeoStore
    from geocoder.search.pipeline import SearchPipeline

    db_path = os.environ.get("GEOCODER_DB_PATH") or str(SETTINGS.DB_PATH)
    store = GeoStore(db_path)
    rt, a, b = SearchPipeline(store).route(origin, destination)
    if a is None:
        typer.echo(f"Could not resolve origin: {origin}")
        raise typer.Exit(1)
    if b is None:
        typer.echo(f"Could not resolve destination: {destination}")
        raise typer.Exit(1)
    if rt is None or rt.reason != "ok":
        typer.echo(f"No route: {rt.reason if rt else 'unknown'}")
        raise typer.Exit(1)

    km = rt.distance_m / 1000
    typer.echo(f"\n{a['nombre']} → {b['nombre']}")
    typer.echo(f"  distance: {km:.2f} km ({rt.distance_m:.0f} m)")
    typer.echo(f"  duration: ~{rt.duration_s / 60:.0f} min")
    if geojson:
        import json
        from shapely import wkt as _wkt
        from shapely.geometry import mapping
        typer.echo(json.dumps(mapping(_wkt.loads(rt.polyline_wkt)), ensure_ascii=False))


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Host"),
    port: int = typer.Option(8000, "--port", "-p", help="Port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on changes")
):
    """Start FastAPI server."""
    uvicorn.run(
        "geocoder.api.main:app",
        host=host,
        port=port,
        reload=reload
    )


@cli.command()
def stamp():
    """Print the build stamp of the database as JSON (exit 1 if unstamped)."""
    import json
    from geocoder.store import GeoStore
    store = GeoStore(SETTINGS.DB_PATH)
    st = store.build_stamp()
    store.close()
    if st is None:
        typer.echo(f"no build stamp in {SETTINGS.DB_PATH} — built before stamping", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(st, indent=2))


@cli.command()
def stats():
    """Show database statistics."""
    from geocoder.store import GeoStore
    store = GeoStore(SETTINGS.DB_PATH)
    s = store.stats()
    typer.echo(f"Database: {SETTINGS.DB_PATH}")
    for k, v in s.items():
        if isinstance(v, dict):
            typer.echo(f"  {k}:")
            for kk, vv in v.items():
                typer.echo(f"    {kk}: {vv}")
        else:
            typer.echo(f"  {k}: {v}")
    store.close()


@cli.command()
def bench(
    queries: int = typer.Option(1000, "--queries", "-n", help="Number of queries"),
    file: str = typer.Option(None, "--file", "-f", help="File with queries (one per line)")
):
    """Run performance benchmark."""
    import time
    
    from geocoder.store import GeoStore
    from geocoder.search.pipeline import SearchPipeline

    store = GeoStore(SETTINGS.DB_PATH)
    pipeline = SearchPipeline(store)
    
    if file:
        with open(file) as f:
            test_queries = [line.strip() for line in f if line.strip()]
    else:
        # Use some common test queries
        test_queries = [
            "Calle 23", "Calle 23 415", "23 y 12",
            "Hotel Nacional", "Plaza de la Revolución",
            "Avenida de los Presidentes", "Vedado",
            "municipio:Plaza Calle 23", "barrio:Vedado hotel"
        ]
        test_queries = test_queries * (queries // len(test_queries) + 1)
    
    test_queries = test_queries[:queries]
    
    typer.echo(f"Running {len(test_queries)} queries...")
    
    start = time.perf_counter()
    for q in test_queries:
        pipeline.resolve(q, limit=5)
    elapsed = time.perf_counter() - start
    
    typer.echo(f"Total time: {elapsed:.2f}s")
    typer.echo(f"Avg: {elapsed/len(test_queries)*1000:.1f}ms/query")
    typer.echo(f"QPS: {len(test_queries)/elapsed:.1f}")


if __name__ == "__main__":
    cli()