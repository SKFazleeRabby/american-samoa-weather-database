"""FastAPI application and bounded, read-only PostgreSQL queries."""
from contextlib import asynccontextmanager
from datetime import date, timedelta
import logging
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Annotated, Literal
from datetime import datetime
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.middleware.gzip import GZipMiddleware

from .config import connection_kwargs, settings, ROOT
from .queries import Filters, encode_cursor, observation_sql
from .exports import download_archive, station_download
from .analysis import RANGES_SQL, availability_summary

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    kwargs = {**connection_kwargs(), 'row_factory': dict_row}
    app.state.pool = ConnectionPool(kwargs=kwargs, min_size=1, max_size=8, timeout=5, open=False, check=ConnectionPool.check_connection)
    app.state.exports = ConnectionPool(kwargs=kwargs, min_size=0, max_size=2, timeout=2, open=False, check=ConnectionPool.check_connection)
    app.state.cache = TTLCache(maxsize=256, ttl=30)
    app.state.catalog_cache = TTLCache(maxsize=1, ttl=300)
    app.state.analysis_cache = TTLCache(maxsize=16, ttl=300)
    app.state.lock = RLock()
    app.state.pool.open(); app.state.exports.open()
    try:
        yield
    finally:
        app.state.pool.close(); app.state.exports.close()


app = FastAPI(title='Fieldwork observation API', version='1.0.0', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware('http')
async def timing(request, call_next):
    start = perf_counter()
    response = await call_next(request)
    response.headers['Server-Timing'] = f'app;dur={(perf_counter()-start)*1000:.1f}'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if request.url.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif request.url.path == '/':
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.exception_handler(psycopg.Error)
def database_error(request, error):
    logger.warning('Database request failed: %s', type(error).__name__)
    if isinstance(error, psycopg.errors.QueryCanceled):
        return JSONResponse(status_code=503, content={'detail': 'This query took too long. Try a shorter date range or fewer stations.'})
    return JSONResponse(status_code=503, content={'detail': 'The database is temporarily unavailable. Please retry shortly.'})


@app.exception_handler(PoolTimeout)
def busy_error(request, error):
    return JSONResponse(status_code=503, content={'detail': 'The service is busy. Please retry shortly.'}, headers={'Retry-After': '3'})


def filters(
    start: date, end: date,
    station_ids: Annotated[list[int], Query(max_length=100)] = [],
    variable_ids: Annotated[list[int], Query(max_length=100)] = [],
    status: Literal['active', 'inactive', 'all'] = 'active',
    categories: Annotated[list[str], Query(max_length=20)] = [],
    interval_seconds: Annotated[int, Query(ge=0, le=31536000)] = 86400,
    sort: Literal['asc', 'desc'] = 'desc',
):
    try:
        return Filters(start=start, end=end, station_ids=sorted(set(station_ids)), variable_ids=sorted(set(variable_ids)), status=status, categories=sorted(set(categories)), interval_seconds=interval_seconds, sort=sort)
    except ValueError:
        raise HTTPException(422, 'Check the dates, station IDs, and variables in your filters.') from None


FilterDep = Annotated[Filters, Depends(filters)]
DESCRIPTIONS = {
    'precipitation': 'The amount of rain collected during the reporting period.',
    'air_temp': 'Average air temperature during the reporting period.',
    'air_temp_max': 'Highest air temperature during the reporting period.',
    'air_temp_min': 'Lowest air temperature during the reporting period.',
    'relative_humidity': 'Moisture in the air relative to the maximum it can hold.',
    'solar_radiation': 'Incoming sunlight per square meter.',
}


def catalog(request):
    state = request.app.state
    with state.lock:
        result = state.catalog_cache.get('catalog')
    if result is not None:
        return result
    with state.pool.connection() as conn:
        stations = conn.execute('SELECT id, code, name, external_id, operator AS network, latitude, longitude, elevation_m, timezone, category, status FROM stations ORDER BY name, id').fetchall()
        variables = conn.execute('SELECT id, code, name, unit, description, synoptic_code, aggregation FROM variables ORDER BY id').fetchall()
        earliest = conn.execute('SELECT local_date, observed_at FROM observations ORDER BY observed_at ASC LIMIT 1').fetchone()
        latest = conn.execute('SELECT local_date, observed_at FROM observations ORDER BY observed_at DESC LIMIT 1').fetchone()
        intervals = [r['interval_seconds'] for r in conn.execute('SELECT DISTINCT interval_seconds FROM observations ORDER BY interval_seconds').fetchall()]
    for v in variables:
        v['description'] = v['description'] or DESCRIPTIONS.get(v['code'], f"{v['name']}, reported in {v['unit']}.")
    first = (earliest['local_date'] or earliest['observed_at'].date()) if earliest else None
    last = (latest['local_date'] or latest['observed_at'].date()) if latest else None
    default_end = last or date.today()
    result = {'stations': stations, 'variables': variables, 'intervals': intervals, 'first_date': first, 'last_date': last, 'default_start': max(first or default_end, default_end - timedelta(days=6)), 'default_end': default_end}
    with state.lock:
        state.catalog_cache['catalog'] = result
    return result


def validate_ids(request, f):
    meta = catalog(request)
    if set(f.station_ids) - {s['id'] for s in meta['stations']} or set(f.variable_ids) - {v['id'] for v in meta['variables']}:
        raise HTTPException(422, 'A selected station or variable does not exist. Refresh the page and try again.')
    return meta


@app.get('/api/health')
def health(request: Request):
    with request.app.state.pool.connection() as conn:
        conn.execute('SELECT 1')
    return {'status': 'ok', 'database': 'connected'}


@app.get('/api/catalog')
def get_catalog(request: Request):
    return catalog(request)


@app.get('/api/analysis/availability')
def availability(request: Request, variable_id: Annotated[int, Query(gt=0)]):
    meta = catalog(request)
    if variable_id not in {v['id'] for v in meta['variables']}:
        raise HTTPException(422, 'Choose a variable from the catalog.')
    as_of = datetime.now(ZoneInfo('Pacific/Pago_Pago')).date() - timedelta(days=1)
    state = request.app.state
    key = (variable_id, as_of)
    with state.lock:
        result = state.analysis_cache.get(key)
    if result is None:
        with state.pool.connection() as conn:
            ranges = conn.execute(RANGES_SQL, (variable_id, as_of, variable_id, as_of)).fetchall()
        result = availability_summary(meta['stations'], ranges, as_of)
        with state.lock:
            state.analysis_cache[key] = result
    return result


@app.get('/api/observations')
def observations(request: Request, f: FilterDep, page_size: Annotated[int, Query()] = 10, cursor: Annotated[str | None, Query(max_length=512)] = None):
    if page_size not in (10, 50, 100):
        raise HTTPException(422, 'Page size must be 10, 50, or 100')
    validate_ids(request, f)
    key = ('rows', f.model_dump_json(), page_size, cursor)
    state = request.app.state
    with state.lock:
        result = state.cache.get(key)
    if result is None:
        query, params = observation_sql(f, page_size=page_size, cursor=cursor)
        with state.pool.connection() as conn:
            data = conn.execute(query, params).fetchall()
        has_more = len(data) > page_size
        items = data[:page_size]
        result = {'items': items, 'next_cursor': encode_cursor(items[-1]) if has_more else None, 'has_more': has_more}
        with state.lock:
            state.cache[key] = result
    return result


@app.get('/api/observations/count')
def count(request: Request, f: FilterDep):
    validate_ids(request, f)
    state = request.app.state
    key = ('count', f.model_dump_json())
    with state.lock:
        result = state.cache.get(key)
    if result is None:
        query, params = observation_sql(f, count=True)
        with state.pool.connection() as conn:
            result = conn.execute(query, params).fetchone()
        with state.lock:
            state.cache[key] = result
    return result


@app.get('/api/observations/export.zip')
@app.get('/api/observations/export.csv', include_in_schema=False)
def export(request: Request, f: FilterDep):
    return download_archive(request, f, validate_ids(request, f))


@app.get('/api/stations/export.csv')
def export_stations(request: Request):
    return station_download(catalog(request))


# FastAPI serves the compiled React application from the same origin as /api.
static_dir = Path(settings().get('WEB_DIST', ROOT / 'web' / 'dist'))
if static_dir.is_dir():
    @app.get('/analysis', include_in_schema=False)
    def analysis_page():
        from fastapi.responses import FileResponse
        return FileResponse(static_dir / 'index.html', headers={'Cache-Control': 'no-cache'})

    app.mount('/', StaticFiles(directory=static_dir, html=True), name='frontend')
