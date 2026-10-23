from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import unittest
import os
import logging

import httpx
from apscheduler.triggers.cron import CronTrigger

from as_db.collection.runner import missing_windows
from as_db.collection.runner import collect_provider
from as_db.collection.synoptic import CollectionError, SynopticCollector, statistic_readings, precipitation_readings
from as_db.collection.synoptic import Reading
from as_db.collection.mesonet import MesonetCollector, clean_records
from as_db.collection.wrcc import parse_wrcc
from as_db.collection.noaa import parse_reports
from as_db.collection.logging_config import SecretFilter


ZONE = ZoneInfo('Pacific/Pago_Pago')


class CollectionTests(unittest.TestCase):
    def test_mesonet_threshold_deduplication_and_rainfall_sum(self):
        start = datetime(2026, 9, 25, 11, tzinfo=timezone.utc)
        records = [{'station_id': '1312', 'timestamp': (start + timedelta(minutes=5*i)).isoformat(), 'variable': code, 'value': str(value), 'flag': 0} for i in range(288) for code,value in [('RF_1_Tot300s',.1),('Tair_1_Avg',25)]]
        records.append(records[0].copy())
        result = clean_records(records, '1312', ['RF_1_Tot300s','Tair_1_Avg'], ZONE, date(2026,9,25), date(2026,9,25))
        self.assertEqual({r.variable_code:r.value for r in result}, {'RF_1_Tot300s':28.8,'Tair_1_Avg':25})
        incomplete = [r for r in records if datetime.fromisoformat(r['timestamp']) < start + timedelta(minutes=5*273)]
        self.assertEqual(clean_records(incomplete, '1312', ['RF_1_Tot300s','Tair_1_Avg'], ZONE, date(2026,9,25), date(2026,9,25)), [])

    def test_wrcc_ascii_and_missing_values(self):
        text='<PRE>Toa Ridge\nDate Precip. Total\nmm\n09/25/2026 1 11.94\n09/26/2026 1 -9999\n09/27/2026 1 M\n09/28/2026 1 0</PRE>'
        result=parse_wrcc(text,date(2026,9,25),date(2026,9,28))
        self.assertEqual([(r.day.day,r.value) for r in result],[(25,11.94),(28,0)])
        self.assertEqual(parse_wrcc('<PRE>Date Precip. Total\nmm\n',date(2026,9,25),date(2026,9,28)),[])
        with self.assertRaises(CollectionError): parse_wrcc(text.replace('mm','inches'),date(2026,9,25),date(2026,9,28))

    def test_wrcc_full_daily_columns_keep_variables_independent(self):
        text = '<PRE>Air Relative\nTemperature Humidity\nDate Ave. Max. Min. Precip. Total\nDeg. C percent mm\n10/15/2026 2026 288 2 2.157 0.89 160 6.71 31.8 33.9 28.9 100 100 100 9.65\n10/16/2026 2026 289 3 M M M M -9999 M M 95 100 90 0</PRE>'
        readings = parse_wrcc(text, date(2026,10,15), date(2026,10,16))
        self.assertEqual({r.variable_code:r.value for r in readings if r.day.day == 15}, {'RF_1_Tot300s':9.65,'Tair_1_Avg':31.8,'RH_1_Avg':100})
        self.assertEqual({r.variable_code:r.value for r in readings if r.day.day == 16}, {'RF_1_Tot300s':0,'RH_1_Avg':95})
        with self.assertRaises(CollectionError):
            parse_wrcc(text.replace('Deg. C','Deg. F'), date(2026,10,15), date(2026,10,16))

    def test_noaa_rolling_report_is_not_mislabeled_calendar_day_data(self):
        instant=datetime(2026,9,26,12,tzinfo=timezone.utc)
        result=parse_reports([{'icaoId':'NSTU','obsTime':instant.timestamp(),'pcp24hr':1}],date(2026,9,25),ZONE)
        self.assertEqual(result['precipitation_24h_mm'],25.4)
        self.assertEqual(result['period'],'rolling_24h')
        self.assertEqual(result['target_date'],'2026-09-25')
        self.assertIsNone(parse_reports([{'icaoId':'NSTU','obsTime':instant.timestamp(),'pcp24hr':None}],date(2026,9,25),ZONE))

    def test_log_filter_redacts_client_request_urls_and_literal_credentials(self):
        record=logging.LogRecord('httpx',logging.INFO,'',0,'GET https://example.test?token=secret-value&stid=NSTU Authorization %s',('bearer-secret',),None)
        SecretFilter(['secret-value','bearer-secret']).filter(record)
        self.assertNotIn('secret-value',record.getMessage())
        self.assertNotIn('bearer-secret',record.getMessage())
        self.assertIn('[REDACTED]',record.getMessage())

    def test_gap_detection_finds_internal_holes_and_limits_chunks(self):
        existing = {date(2026, 1, 1), date(2026, 1, 3)}
        self.assertEqual(list(missing_windows(date(2026, 1, 1), date(2026, 1, 8), existing, max_days=3)), [(date(2026, 1, 2), date(2026, 1, 2)), (date(2026, 1, 4), date(2026, 1, 6)), (date(2026, 1, 7), date(2026, 1, 8))])
        self.assertEqual(list(missing_windows(date(2026, 1, 1), date(2026, 1, 1), existing)), [])

    def test_local_daily_statistics_preserve_zero_and_missing(self):
        payload = {'UNITS': {'air_temp': 'Celsius', 'relative_humidity': '%'}, 'STATION': [{'STID': 'NSTU', 'TIMEZONE': str(ZONE), 'STATISTICS': {'air_temp_set_1': [{'mean': 0, 'max': 31, 'min': None, 'count': 24, 'time_period': {'type': 'day', 'value': '2026-09-25', 'timezone': str(ZONE)}}]}}]}
        readings = statistic_readings(payload, ZONE, date(2026, 9, 25), date(2026, 9, 25), ['Tair_1_Avg', 'Tair_1_Max', 'Tair_1_Min'])
        self.assertEqual([(r.variable_code, r.value) for r in readings], [('Tair_1_Avg', 0), ('Tair_1_Max', 31)])
        payload['STATION'][0]['STATISTICS']['air_temp_set_1'][0]['time_period']['timezone'] = 'Etc/UTC'
        with self.assertRaises(CollectionError):
            statistic_readings(payload, ZONE, date(2026, 9, 25), date(2026, 9, 25), ['Tair_1_Avg'])

    def test_precipitation_uses_local_day_not_utc_day(self):
        payload = {'UNITS': {'precipitation': 'Millimeters'}, 'STATION': [{'STID': 'NSTU', 'OBSERVATIONS': {'precipitation': [{'count': 24, 'interval': 24, 'first_report': '2026-09-25T11:53:00Z', 'last_report': '2026-09-26T10:53:00Z', 'total': 0}]}}]}
        result = precipitation_readings(payload, ZONE, date(2026, 9, 25), date(2026, 9, 25))
        self.assertEqual(result[0].day, date(2026, 9, 25))
        self.assertEqual(result[0].value, 0)
        payload['STATION'][0]['OBSERVATIONS']['precipitation'][0]['interval'] = 1
        with self.assertRaises(CollectionError):
            precipitation_readings(payload, ZONE, date(2026, 9, 25), date(2026, 9, 25))

    def test_access_errors_do_not_disclose_token_or_claim_empty_success(self):
        collector = SynopticCollector('test-secret')
        collector.client.close()
        collector.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'SUMMARY': {'RESPONSE_CODE': 403, 'RESPONSE_MESSAGE': 'no access'}})))
        self.addCleanup(collector.close)
        with self.assertRaises(CollectionError) as error:
            collector.request('statistics')
        self.assertNotIn('test-secret', str(error.exception))
        self.assertIn('403', str(error.exception))

    def test_schedule_is_at_three_in_requested_timezone(self):
        trigger = CronTrigger(hour=3, minute=0, timezone='Pacific/Pago_Pago')
        upcoming = trigger.get_next_fire_time(None, datetime(2026, 10, 16, 2, 59, tzinfo=ZoneInfo('Pacific/Pago_Pago')))
        self.assertEqual((upcoming.hour, upcoming.minute), (3, 0))
        self.assertEqual(str(upcoming.tzinfo), 'Pacific/Pago_Pago')

    @unittest.skipUnless(os.environ.get('AS_DB_WEB_TEST') == '1', 'Opt in to isolated temporary-table database checks')
    def test_import_repairs_internal_gaps_is_idempotent_and_preserves_zero(self):
        import psycopg
        from psycopg.rows import dict_row
        from as_db.web.config import connection_kwargs

        class FixtureCollector:
            name = 'fixture'
            station_name = 'Airport5101'
            variable_codes = ('Tair_1_Avg',)
            calls = []

            def fetch(self, start, end, zone, requested):
                self.calls.append((start, end))
                return [Reading(start, 'Tair_1_Avg', 0)]

        # Temp tables shadow the real names in this connection only. Nothing is
        # inserted, altered, or deleted in the user's permanent station archive.
        with psycopg.connect(**connection_kwargs(admin=True), row_factory=dict_row) as conn:
            with conn.transaction(force_rollback=True):
                conn.execute('CREATE TEMP TABLE stations (id bigint,code text,name text,timezone text) ON COMMIT DROP')
                conn.execute('CREATE TEMP TABLE variables (id bigint,code text,name text,unit text) ON COMMIT DROP')
                conn.execute('''CREATE TEMP TABLE observations (station_id bigint,variable_id bigint,
                    observed_at timestamptz,interval_seconds integer,local_date date,value double precision,
                    quality_flag text,upload_id bigint,PRIMARY KEY(station_id,variable_id,interval_seconds,observed_at)) ON COMMIT DROP''')
                conn.execute("INSERT INTO stations VALUES (1,'airport5101','Airport5101','Pacific/Pago_Pago')")
                conn.execute("INSERT INTO variables VALUES (1,'air_temp','Temperature','°C')")
                conn.execute("INSERT INTO observations VALUES (1,1,'2026-09-01T11:00Z',86400,'2026-09-01',0,'valid',NULL),(1,1,'2026-09-03T11:00Z',86400,'2026-09-03',28,'valid',NULL)")
                collector = FixtureCollector()
                result = collect_provider(conn, collector, start=date(2026, 9, 1), end=date(2026, 9, 4), history_dates={'air_temp': date(2026, 8, 31)})
                self.assertEqual(result['inserted'], 2)
                self.assertEqual(collector.calls, [(date(2026, 9, 2), date(2026, 9, 2)), (date(2026, 9, 4), date(2026, 9, 4))])
                self.assertEqual(collect_provider(conn, collector, start=date(2026, 9, 1), end=date(2026, 9, 4), history_dates={'air_temp': date(2026, 8, 31)})['inserted'], 0)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM observations').fetchone()['n'], 4)
                self.assertEqual(conn.execute("SELECT quality_flag FROM observations WHERE local_date='2026-09-01'").fetchone()['quality_flag'], 'valid')
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM observations WHERE value=0').fetchone()['n'], 3)


if __name__ == '__main__':
    unittest.main()
