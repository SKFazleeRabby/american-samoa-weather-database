"""Bounded-memory date-by-station CSV files, one per variable."""
from datetime import datetime, timedelta
import re

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from stream_zip import ZIP_64, stream_zip

from .queries import DAY, csv_line, observation_sql, where_clause


def selected_stations(meta, filters):
    return [s for s in meta['stations'] if
            (filters.status == 'all' or s['status'] == filters.status)
            and (not filters.station_ids or s['id'] in filters.station_ids)
            and (not filters.categories or s['category'] in filters.categories)]


def wide_csv(conn, filters, variable, stations):
    current_filters = filters.model_copy(update={'variable_ids': [variable['id']], 'sort': 'asc'})
    query, parameters = observation_sql(current_filters)
    positions = {s['id']: i for i, s in enumerate(stations)}
    with conn.cursor(name=f"variable_export_{variable['id']}") as cursor:
        cursor.execute(query, parameters)
        cursor.itersize = 500
        iterator = iter(cursor)
        pending = next(iterator, None)
        yield csv_line(['Date', *[s['name'] for s in stations]]).encode('utf-8')
        day = filters.start
        while day <= filters.end:
            values = [''] * len(stations)
            while pending is not None and pending['date'] == day:
                values[positions[pending['station_id']]] = pending['values'].get(str(variable['id']), '')
                pending = next(iterator, None)
            yield csv_line([day, *values]).encode('utf-8')
            day += timedelta(days=1)


def archive_chunks(request, filters, meta):
    stations = selected_stations(meta, filters)
    variables = [v for v in meta['variables'] if not filters.variable_ids or v['id'] in filters.variable_ids]
    with request.app.state.exports.connection() as conn:
        conn.execute("SET LOCAL statement_timeout='120s'")

        def files():
            for v in variables:
                name = re.sub(r'[^a-zA-Z0-9_-]+', '_', v['name']).strip('_') or 'variable'
                yield (f"{name}_{v['id']}.csv", datetime.now(), 0o600, ZIP_64, wide_csv(conn, filters, v, stations))

        yield from stream_zip(files())


def download_archive(request, filters, meta):
    if filters.interval_seconds != 86400:
        raise HTTPException(422, 'Date-by-station downloads require the Daily reporting interval.')
    stations = selected_stations(meta, filters)
    if len({s['name'] for s in stations}) != len(stations):
        raise HTTPException(422, 'Selected stations have duplicate names. Select stations with unique names to download.')
    where, parameters = where_clause(filters)
    # There must be exactly one possible value for a date/station/variable cell.
    with request.app.state.exports.connection() as conn:
        duplicate = conn.execute(f'''SELECT 1 FROM observations o JOIN stations s ON s.id=o.station_id
            WHERE {where} GROUP BY {DAY}, o.station_id, o.variable_id HAVING count(*)>1 LIMIT 1''', parameters).fetchone()
    if duplicate:
        raise HTTPException(422, 'More than one daily reading exists for a station and variable. Resolve the duplicate daily readings before downloading.')
    iterator = archive_chunks(request, filters, meta)
    first = next(iterator)

    def stream():
        try:
            yield first
            yield from iterator
        finally:
            iterator.close()

    return StreamingResponse(stream(), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="fieldwork-{filters.start}-to-{filters.end}.zip"',
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}, background=BackgroundTask(iterator.close))


def station_download(meta):
    def lines():
        yield csv_line(['Station Name', 'Longitude', 'Latitude', 'Elev', 'Network'])
        for s in meta['stations']:
            yield csv_line([s['name'], s['longitude'], s['latitude'], s['elevation_m'], s['network']])
    return StreamingResponse(lines(), media_type='text/csv', headers={
        'Content-Disposition': 'attachment; filename="stations.csv"', 'Cache-Control': 'no-store'})
