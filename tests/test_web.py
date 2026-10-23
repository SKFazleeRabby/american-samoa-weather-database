"""Unit tests plus opt-in, SELECT-only tests against the configured archive."""
import csv
from datetime import date, datetime, timezone
import io
import os
import unittest
import zipfile

from fastapi import HTTPException
from fastapi.testclient import TestClient

from as_db.web.queries import Filters, csv_line, decode_cursor, encode_cursor, observation_sql, where_clause


class QueryTests(unittest.TestCase):
    def test_date_bounds_cover_station_timezones_and_remain_parameterized(self):
        f = Filters(start='2026-01-01', end='2026-01-02', categories=["weather'); DROP TABLE stations; --"], station_ids=[1], variable_ids=[2])
        query, params = observation_sql(f, page_size=10)
        self.assertNotIn(f.categories[0], query)
        self.assertIn(f.categories, params)
        self.assertIn('o.observed_at >= %s', query)
        self.assertIn('BETWEEN %s AND %s', query)
        self.assertEqual(params[0], datetime(2025, 12, 31, 10, tzinfo=timezone.utc))
        self.assertEqual(params[1], datetime(2026, 1, 3, 12, tzinfo=timezone.utc))
        self.assertEqual(params[-1], 11)

    def test_cursors_preserve_timestamp_and_have_deterministic_tiebreakers(self):
        row = {'date': date(2026, 1, 1), 'station_id': 7, 'observed_at': datetime(2026, 1, 1, 11, 5, tzinfo=timezone.utc)}
        token = encode_cursor(row)
        self.assertEqual(decode_cursor(token), (row['date'], 7, row['observed_at']))
        for sort, operator in [('asc', '>'), ('desc', '<')]:
            query, params = observation_sql(Filters(start='2026-01-01', end='2026-01-02', sort=sort), page_size=10, cursor=token)
            self.assertIn(f'o.observed_at) {operator} (%s,%s,%s)', query)
            self.assertEqual(params[-4:-1], [row['date'], 7, row['observed_at']])
        for bad in ('bad', 'e30', 'x'*513):
            with self.assertRaises(HTTPException):
                decode_cursor(bad)

    def test_csv_preserves_zero_negative_values_and_escapes_formulas(self):
        cells = next(csv.reader(io.StringIO(csv_line([0, -2.5, None, '=CMD()', ' name, quoted', '"hello"']))))
        self.assertEqual(cells, ['0', '-2.5', '', "'=CMD()", ' name, quoted', '"hello"'])

    def test_invalid_dates_and_ids(self):
        for data in ({'start': '2026-02-02', 'end': '2026-02-01'}, {'start': '2026-01-01', 'end': '2026-01-02', 'station_ids': [-1]}):
            with self.assertRaises(ValueError):
                Filters(**data)


@unittest.skipUnless(os.environ.get('AS_DB_WEB_TEST') == '1', 'Set AS_DB_WEB_TEST=1 for SELECT-only real archive tests')
class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from as_db.web.api import app
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.meta = cls.client.get('/api/catalog').json()
        cls.base = {'start': cls.meta['default_start'], 'end': cls.meta['default_end'], 'status': 'all'}

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_real_metadata_and_restricted_login(self):
        self.assertTrue(self.meta['stations'])
        self.assertTrue(self.meta['variables'])
        with self.client.app.state.pool.connection() as conn:
            role = conn.execute("SELECT rolsuper, current_setting('transaction_read_only') AS readonly, has_table_privilege(current_user,'observations','INSERT') AS can_insert FROM pg_roles WHERE rolname=current_user").fetchone()
        self.assertFalse(role['rolsuper'])
        self.assertFalse(role['can_insert'])
        self.assertEqual(role['readonly'], 'on')

    def test_availability_matches_distinct_non_null_daily_dates(self):
        variable = self.meta['variables'][0]['id']
        response = self.client.get('/api/analysis/availability', params={'variable_id': variable})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload['stations']), len(self.meta['stations']))
        with self.client.app.state.pool.connection() as conn:
            expected = conn.execute('''SELECT o.station_id,count(DISTINCT COALESCE(o.local_date,
                (o.observed_at AT TIME ZONE s.timezone)::date)) AS n FROM observations o
                JOIN stations s ON s.id=o.station_id WHERE o.variable_id=%s AND interval_seconds=86400
                AND value IS NOT NULL AND COALESCE(o.local_date,(o.observed_at AT TIME ZONE s.timezone)::date)<=%s
                GROUP BY o.station_id''', (variable,payload['as_of'])).fetchall()
        counts = {r['station_id']:r['n'] for r in expected}
        for station in payload['stations']:
            self.assertEqual(station['available_days'], counts.get(station['id'], 0))
            self.assertEqual(sum(y['available_days'] for y in station['years']), station['available_days'])
            self.assertTrue(all(y['percentage'] is None or 0 <= y['percentage'] <= 100 for y in station['years']))
        self.assertEqual(self.client.get('/api/analysis/availability', params={'variable_id':999999}).status_code,422)
        self.assertEqual(self.client.get('/api/analysis/availability').status_code,422)

    def test_keyset_pagination_matches_export_in_both_directions(self):
        for sort in ('asc', 'desc'):
            filters = {**self.base, 'sort': sort}
            result = []
            cursor = None
            while True:
                response = self.client.get('/api/observations', params={**filters, 'page_size': 10, **({'cursor': cursor} if cursor else {})})
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                result.extend(payload['items'])
                if not payload['next_cursor']:
                    break
                self.assertNotEqual(payload['next_cursor'], cursor)
                cursor = payload['next_cursor']
                self.assertLess(len(result), 2000)
            keys = [(r['date'], r['station_id'], r['observed_at']) for r in result]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(keys, sorted(keys, reverse=sort == 'desc'))
            self.assertEqual(len(result), self.client.get('/api/observations/count', params=filters).json()['total'])
            export = self.client.get('/api/observations/export.zip', params=filters)
            self.assertEqual(export.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
                self.assertEqual(len(archive.namelist()), len(self.meta['variables']))
                for name in archive.namelist():
                    records = list(csv.DictReader(io.StringIO(archive.read(name).decode('utf-8'))))
                    self.assertEqual(len(records), (date.fromisoformat(filters['end']) - date.fromisoformat(filters['start'])).days + 1)
                    self.assertEqual(list(records[0]), ['Date', *[s['name'] for s in self.meta['stations']]])

    def test_filtered_values_match_postgres_and_export_columns(self):
        page = self.client.get('/api/observations', params=self.base).json()
        self.assertTrue(page['items'])
        row = page['items'][0]
        variable_id = int(next(iter(row['values'])))
        query = {**self.base, 'station_ids': row['station_id'], 'variable_ids': variable_id}
        result = self.client.get('/api/observations', params=query).json()['items']
        self.assertTrue(all(r['station_id'] == row['station_id'] and list(r['values']) == [str(variable_id)] for r in result))
        first = result[0]
        with self.client.app.state.pool.connection() as conn:
            value = conn.execute('SELECT value FROM observations WHERE station_id=%s AND variable_id=%s AND observed_at=%s AND interval_seconds=86400', (row['station_id'], variable_id, first['observed_at'])).fetchone()['value']
        self.assertEqual(first['values'][str(variable_id)], value)
        export = self.client.get('/api/observations/export.zip', params=query)
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            self.assertEqual(len(archive.namelist()), 1)
            records = list(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode('utf-8'))))
            station_name = next(s['name'] for s in self.meta['stations'] if s['id'] == row['station_id'])
            self.assertEqual(list(records[0]), ['Date', station_name])
            match = next(r for r in records if r['Date'] == first['date'])
            self.assertEqual(float(match[station_name]), value)

    def test_exports_cross_multiple_batches(self):
        filters = {'start': '2018-01-01', 'end': '2018-12-31', 'status': 'all'}
        total = self.client.get('/api/observations/count', params=filters).json()['total']
        self.assertGreater(total, 500)
        response = self.client.get('/api/observations/export.zip', params=filters)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            nonempty = 0
            for name in archive.namelist():
                records = list(csv.DictReader(io.StringIO(archive.read(name).decode('utf-8'))))
                self.assertEqual(len(records), 365)
                nonempty += sum(1 for row in records for key, value in row.items() if key != 'Date' and value != '')
            self.assertGreaterEqual(nonempty, total)
        self.assertIn('attachment', response.headers['content-disposition'])

    def test_station_information_export_and_daily_export_guard(self):
        response = self.client.get('/api/stations/export.csv')
        records = list(csv.DictReader(io.StringIO(response.text)))
        self.assertEqual(list(records[0]), ['Station Name', 'Longitude', 'Latitude', 'Elev', 'Network'])
        self.assertEqual(len(records), len(self.meta['stations']))
        response = self.client.get('/api/observations/export.zip', params={**self.base, 'interval_seconds': 300})
        self.assertEqual(response.status_code, 422)

    def test_validation_empty_results_and_all_page_sizes(self):
        for extra in ({'page_size': 11}, {'cursor': 'broken'}, {'station_ids': -1}, {'start': '2026-03-01', 'end': '2026-01-01'}):
            self.assertEqual(self.client.get('/api/observations', params={**self.base, **extra}).status_code, 422)
        for size in (10, 50, 100):
            response = self.client.get('/api/observations', params={**self.base, 'page_size': size})
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(response.json()['items']), size)
        result = self.client.get('/api/observations', params={'start': '1900-01-01', 'end': '1900-01-02'}).json()
        self.assertEqual(result['items'], [])
        self.assertIsNone(result['next_cursor'])


if __name__ == '__main__':
    unittest.main()
