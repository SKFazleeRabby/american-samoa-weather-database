"""Run integration tests only against an explicitly configured disposable DB."""

from datetime import date, datetime, timezone
import os
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg

from as_db.import_observations import _insert_observations, import_observations, observation_time
from as_db.insert_stations import insert_stations
from as_db.insert_variables import insert_variables


ROOT = Path(__file__).resolve().parents[1]


class TimeTests(unittest.TestCase):
    def test_daily_date_is_station_midnight(self):
        instant, local = observation_time('2026-01-01', ZoneInfo('Pacific/Pago_Pago'))
        self.assertEqual(instant, datetime(2026, 1, 1, 11, tzinfo=timezone.utc))
        self.assertEqual(local, date(2026, 1, 1))

    def test_offset_timestamp_and_invalid_local_time(self):
        instant, _ = observation_time('2026-01-01T12:00:00+01:00', ZoneInfo('UTC'))
        self.assertEqual(instant.hour, 11)
        with self.assertRaises(ValueError):
            observation_time('2026-03-08 02:30', ZoneInfo('America/New_York'))


@unittest.skipUnless(os.environ.get('AS_DB_TEST_DSN'), 'Set AS_DB_TEST_DSN to a disposable database')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['AS_DB_TEST_DSN'])
        self.addCleanup(self.conn.close)
        transaction = self.conn.transaction(force_rollback=True)
        transaction.__enter__()
        self.addCleanup(transaction.__exit__, None, None, None)
        insert_stations(pd.DataFrame([{
            'code': 'df-test', 'name': 'DF Station', 'latitude': -14,
            'longitude': -170, 'external_id': 1312.0,
        }]), connection=self.conn)
        insert_variables(pd.DataFrame([{
            'code': 'df-test', 'name': 'Temperature', 'unit': 'celcius',
        }]), connection=self.conn)

    def insert(self, frame, **kwargs):
        return _insert_observations(frame, variable_name='df-test', interval_seconds=86400,
                                   connection=self.conn, **kwargs)

    def test_missing_zero_repeat_and_explicit_update(self):
        frame = pd.DataFrame({'Date': ['2026-01-01', '2026-01-02'], 'DF Station': [0, None]})
        first = self.insert(frame)
        self.assertEqual((first.written, first.missing), (1, 1))
        second = self.insert(frame)
        self.assertEqual((second.written, second.skipped), (0, 1))
        frame.loc[0, 'DF Station'] = 2
        self.insert(frame, on_conflict='update')
        result = self.conn.execute('''SELECT value, observed_at, local_date FROM observations
            WHERE variable_id=(SELECT id FROM variables WHERE code='df-test')''').fetchone()
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], datetime(2026, 1, 1, 11, tzinfo=timezone.utc))
        self.assertEqual(result[2], date(2026, 1, 1))

    def test_unknown_station_and_atomic_validation(self):
        with self.assertRaisesRegex(ValueError, 'Unknown station'):
            self.insert(pd.DataFrame({'Date': ['2026-01-01'], 'Unknown': [1]}))
        frame = pd.DataFrame({'Date': ['2026-01-01', '2026-01-02'], 'DF Station': [1, 'bad']})
        with self.assertRaises(ValueError):
            self.insert(frame)
        count = self.conn.execute("SELECT count(*) FROM observations WHERE variable_id=(SELECT id FROM variables WHERE code='df-test')").fetchone()[0]
        self.assertEqual(count, 0)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.insert(pd.DataFrame({'Date': ['2026-01-01'] * 2, 'DF Station': [1, 2]}))
        count = self.conn.execute("SELECT count(*) FROM observations WHERE variable_id=(SELECT id FROM variables WHERE code='df-test')").fetchone()[0]
        self.assertEqual(count, 0)

    def test_metadata_upsert_preserves_ids_and_frame(self):
        before = self.conn.execute("SELECT id FROM stations WHERE code='df-test'").fetchone()[0]
        frame = pd.DataFrame([{'code': 'df-test', 'name': 'New DF Station'}])
        original = frame.copy(deep=True)
        insert_stations(frame, connection=self.conn)
        self.assertTrue(frame.equals(original))
        after = self.conn.execute("SELECT id FROM stations WHERE code='df-test'").fetchone()[0]
        self.assertEqual(before, after)

    def test_all_supplied_measurement_files(self):
        stations = pd.read_csv(ROOT / 'stations.csv', dtype={'Station ID': 'string'})
        insert_stations(stations, connection=self.conn)
        insert_variables(pd.read_csv(ROOT / 'variables.csv'), connection=self.conn)
        mapping = {
            'rainfall': 'precipitation', 'temperature': 'air_temp',
            'relative_humidity': 'relative_humidity', 'solar_radiation': 'solar_radiation',
            'soil_moisture': 'soil_moisture', 'stream_stage': 'stream_stage',
        }
        names = {r[0] for r in self.conn.execute('SELECT name FROM stations')}
        for stem, code in mapping.items():
            with self.subTest(variable=stem):
                path = next((ROOT / 'data').glob(f'as_{stem}_*.csv'))
                frame = pd.read_csv(path)
                station_columns = [c for c in frame if c in names]
                expected = int(frame[station_columns].notna().sum().sum())
                before = self.conn.execute('''SELECT count(*) FROM observations
                    WHERE variable_id=(SELECT id FROM variables WHERE code=%s)''', (code,)).fetchone()[0]
                result = import_observations(
                    path, code, interval_seconds=86400,
                    skip_unknown_stations=True, connection=self.conn,
                )
                self.assertEqual(result.processed, expected)
                self.assertEqual(result.written, expected - before)
                count = self.conn.execute('''SELECT count(*) FROM observations
                    WHERE variable_id=(SELECT id FROM variables WHERE code=%s)''', (code,)).fetchone()[0]
                self.assertEqual(count, expected)


if __name__ == '__main__':
    unittest.main()
