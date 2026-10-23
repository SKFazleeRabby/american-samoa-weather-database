from datetime import date
import unittest
import os

from as_db.web.analysis import RANGES_SQL, availability_summary


class AvailabilityTests(unittest.TestCase):
    def test_leap_year_partial_current_year_and_distinct_missing_dates(self):
        stations = [{'id': 1, 'name': 'Station A', 'network': 'Fixture', 'status': 'active'},
                    {'id': 2, 'name': 'No data', 'network': None, 'status': 'inactive'}]
        runs = [{'station_id': 1, 'start': date(2024, 2, 28), 'end': date(2024, 3, 1)},
                {'station_id': 1, 'start': date(2025, 1, 1), 'end': date(2025, 1, 1)}]
        result = availability_summary(stations, runs, date(2025, 1, 2))
        first, empty = result['stations']
        self.assertEqual(result['years'], [2024, 2025])
        self.assertEqual(first['available_days'], 4)
        self.assertEqual(first['missing_days'], (date(2025, 1, 1)-date(2024, 2, 28)).days + 1 - 4)
        self.assertEqual(first['years'][0]['expected_days'], 366)
        self.assertEqual(first['years'][0]['available_days'], 3)
        self.assertEqual(first['years'][1]['percentage'], 50)
        self.assertIsNone(empty['first_date'])
        self.assertTrue(all(y['percentage'] is None for y in empty['years']))

    def test_internal_empty_year_is_zero_outside_record_is_not_applicable(self):
        stations = [{'id': 1, 'name': 'A', 'network': None, 'status': 'active'}]
        runs = [{'station_id': 1, 'start': date(2022, 12, 31), 'end': date(2022, 12, 31)},
                {'station_id': 1, 'start': date(2024, 1, 1), 'end': date(2024, 1, 1)}]
        row = availability_summary(stations, runs, date(2025, 1, 2))['stations'][0]
        self.assertEqual(row['years'][1]['percentage'], 0)
        self.assertIsNone(row['years'][-1]['percentage'])
        self.assertEqual(row['years'][0]['available_days'], 1)

    def test_empty_archive(self):
        result = availability_summary([], [], date(2026, 10, 15))
        self.assertEqual(result['years'], [])
        self.assertIsNone(result['first_date'])

    @unittest.skipUnless(os.environ.get('AS_DB_WEB_TEST') == '1', 'Opt in to isolated temporary-table database checks')
    def test_daily_ranges_deduplicate_fallback_dates_preserve_zero_and_exclude_future(self):
        import psycopg
        from psycopg.rows import dict_row
        from as_db.web.config import connection_kwargs
        with psycopg.connect(**connection_kwargs(admin=True), row_factory=dict_row) as conn:
            with conn.transaction(force_rollback=True):
                conn.execute('CREATE TEMP TABLE stations (id bigint,timezone text) ON COMMIT DROP')
                conn.execute('CREATE TEMP TABLE observations (station_id bigint,variable_id bigint,interval_seconds int,local_date date,observed_at timestamptz,value float) ON COMMIT DROP')
                conn.execute("INSERT INTO stations VALUES (1,'Pacific/Pago_Pago')")
                conn.execute("""INSERT INTO observations VALUES
                    (1,1,86400,NULL,'2025-01-01T10:00Z',0),
                    (1,1,86400,'2024-12-31','2024-12-31T11:00Z',2),
                    (1,1,86400,'2025-01-01','2025-01-01T11:00Z',3),
                    (1,1,86400,'2025-01-02','2025-01-02T11:00Z',4),
                    (1,1,86400,'2024-12-30','2024-12-30T11:00Z',NULL),
                    (1,1,3600,'2024-12-30','2024-12-30T12:00Z',7),
                    (1,2,86400,'2024-12-30','2024-12-30T11:00Z',8)""")
                rows = conn.execute(RANGES_SQL,(1,date(2025,1,1),1,date(2025,1,1))).fetchall()
                self.assertEqual(rows,[{'station_id':1,'start':date(2024,12,31),'end':date(2025,1,1)}])

