"""Compact station availability summaries; daily readings count once per local date."""
from datetime import date


RANGES_SQL = '''WITH daily AS MATERIALIZED (
    SELECT DISTINCT o.station_id,o.local_date AS day
    FROM observations o
    WHERE o.variable_id=%s AND o.interval_seconds=86400 AND o.value IS NOT NULL
      AND o.local_date<=%s
    UNION
    SELECT DISTINCT o.station_id,(o.observed_at AT TIME ZONE s.timezone)::date AS day
    FROM observations o JOIN stations s ON s.id=o.station_id
    WHERE o.variable_id=%s AND o.interval_seconds=86400 AND o.value IS NOT NULL
      AND o.local_date IS NULL AND (o.observed_at AT TIME ZONE s.timezone)::date<=%s
), numbered AS (
    SELECT station_id,day,day-(row_number() OVER (PARTITION BY station_id ORDER BY day))::integer AS island
    FROM daily
)
SELECT station_id,min(day) AS start,max(day) AS end FROM numbered
GROUP BY station_id,island ORDER BY station_id,start'''


def availability_summary(stations, ranges, as_of):
    grouped = {s['id']: [] for s in stations}
    for r in ranges:
        grouped[r['station_id']].append((r['start'], r['end']))
    earliest = min((a for rs in grouped.values() for a, _ in rs), default=None)
    years = list(range(earliest.year, as_of.year + 1)) if earliest else []
    result = []
    for station in stations:
        runs = grouped[station['id']]
        first, last = (runs[0][0], runs[-1][1]) if runs else (None, None)
        counts = {}
        for start, end in runs:
            for year in range(start.year, end.year + 1):
                days = (min(end, date(year, 12, 31)) - max(start, date(year, 1, 1))).days + 1
                counts[year] = counts.get(year, 0) + days
        annual = []
        for year in years:
            expected = (min(date(year, 12, 31), as_of) - date(year, 1, 1)).days + 1
            available = counts.get(year, 0)
            in_range = bool(first and first.year <= year <= last.year)
            annual.append({'year': year, 'available_days': available, 'expected_days': expected,
                           'percentage': round(100 * available / expected, 1) if in_range else None})
        total = sum(counts.values())
        span = (last-first).days + 1 if first else 0
        result.append({'id': station['id'], 'name': station['name'], 'network': station['network'],
                       'status': station['status'], 'first_date': first, 'last_date': last,
                       'available_days': total, 'missing_days': span-total,
                       'ranges': [{'start': a, 'end': b} for a, b in runs], 'years': annual})
    return {'as_of': as_of, 'first_date': earliest, 'years': years, 'stations': result}
