"""Daily aggregation and HCDP-first routing without live provider requests."""
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx

from as_db.collection.mesonet import MesonetFallbackCollector
from as_db.collection.synoptic import CollectionError, Reading, SynopticCollector, report_readings

ZONE = ZoneInfo('Pacific/Pago_Pago')
START = datetime(2026, 10, 1, 11, tzinfo=timezone.utc)


def report_payload(days=3):
    count = days * 288 + 1
    return {'UNITS': {'air_temp': 'Celsius', 'solar_radiation': 'W/m**2',
                      'soil_moisture': '%', 'precip_accum_five_minute': 'Millimeters'},
            'STATION': [{'STID': '002AS', 'TIMEZONE': str(ZONE), 'OBSERVATIONS': {
                'date_time': [(START + timedelta(minutes=5 * index)).isoformat() for index in range(count)],
                'air_temp_set_1': [25.] * count,
                'solar_radiation_set_1': [0.] * count,
                'soil_moisture_set_1': [30.] * count,
                'soil_moisture_set_2': [90.] * count,
                'precip_accum_five_minute_set_1': [1.] * count}}]}


class SynopticBatchTests(unittest.TestCase):
    def test_one_response_produces_separate_days_and_preserves_zero_and_primary_sensor(self):
        rows = report_readings(report_payload(), ZONE, date(2026, 10, 1), date(2026, 10, 3),
                               ['Tair_1_Avg', 'Rin_1_Avg', 'SM_1_Avg', 'RF_1_Tot300s'], '002AS')
        self.assertEqual(len(rows), 12)
        for code, value in [('Tair_1_Avg', 25), ('Rin_1_Avg', 0), ('SM_1_Avg', 30), ('RF_1_Tot300s', 288)]:
            selected = [row for row in rows if row.variable_code == code]
            self.assertEqual([row.day for row in selected], [date(2026, 10, day) for day in (1, 2, 3)])
            self.assertEqual({row.value for row in selected}, {value})

    def test_incomplete_rain_and_means_are_omitted_per_day(self):
        payload = report_payload()
        observations = payload['STATION'][0]['OBSERVATIONS']
        observations['precip_accum_five_minute_set_1'][10] = None
        observations['air_temp_set_1'][288:320] = [None] * 32
        rows = report_readings(payload, ZONE, date(2026, 10, 1), date(2026, 10, 3),
                               ['Tair_1_Avg', 'RF_1_Tot300s'], '002AS')
        keys = {(row.day.day, row.variable_code) for row in rows}
        self.assertNotIn((1, 'RF_1_Tot300s'), keys)
        self.assertNotIn((2, 'Tair_1_Avg'), keys)
        self.assertIn((3, 'Tair_1_Avg'), keys)
        self.assertIn((3, 'RF_1_Tot300s'), keys)

    def test_irregular_airport_reports_are_not_aggregated_as_complete_days(self):
        self.assertEqual(report_readings(report_payload(), ZONE, date(2026, 10, 1),
                                         date(2026, 10, 3), ['Tair_1_Avg'], 'NSTU'), [])

    def test_misalignment_conflicts_and_units_are_rejected(self):
        for problem in ('length', 'duplicate', 'units', 'timezone'):
            with self.subTest(problem=problem):
                payload = report_payload()
                station = payload['STATION'][0]
                observations = station['OBSERVATIONS']
                if problem == 'length':
                    observations['air_temp_set_1'].pop()
                elif problem == 'duplicate':
                    observations['date_time'][1] = observations['date_time'][0]
                    observations['air_temp_set_1'][1] = 50
                elif problem == 'units':
                    payload['UNITS']['air_temp'] = 'Fahrenheit'
                else:
                    station['TIMEZONE'] = 'UTC'
                with self.assertRaises(CollectionError):
                    report_readings(payload, ZONE, date(2026, 10, 1), date(2026, 10, 3),
                                    ['Tair_1_Avg'], '002AS')

    def test_service_denial_uses_one_multiday_timeseries_request(self):
        collector = SynopticCollector('test-token', '002AS', 'Aasu Uh')
        self.addCleanup(collector.close)
        calls = []
        def request(service, **params):
            calls.append((service, params))
            if service == 'timeseries':
                return report_payload()
            raise CollectionError('daily service unavailable')
        collector.request = request
        rows = collector.fetch(date(2026, 10, 1), date(2026, 10, 3), ZONE,
                               ['Tair_1_Avg', 'RF_1_Tot300s'])
        self.assertEqual(len(rows), 6)
        self.assertEqual([service for service, _ in calls], ['statistics', 'precip', 'timeseries'])
        self.assertEqual(calls[-1][1]['start'], '202610011100')
        self.assertEqual(calls[-1][1]['end'], '202610041100')

    def test_denied_services_are_not_requested_for_every_chunk(self):
        collector = SynopticCollector('test-token')
        collector.client.close()
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(403)
        collector.client = httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(collector.close)
        for _ in range(2):
            with self.assertRaises(CollectionError):
                collector.request('statistics')
        self.assertEqual(len(calls), 1)


class RoutingTests(unittest.TestCase):
    def collector(self):
        collector = MesonetFallbackCollector('hcdp-token', 'synoptic-token', 'Aasu Uh', '1312', '002AS')
        self.addCleanup(collector.close)
        return collector

    def test_complete_hcdp_response_never_contacts_synoptic(self):
        with patch('as_db.collection.mesonet.MesonetCollector') as primary, \
             patch('as_db.collection.mesonet.SynopticCollector') as fallback:
            primary.return_value.fetch.return_value = [
                Reading(date(2026, 10, day), 'Tair_1_Avg', 25) for day in (1, 2, 3)]
            rows = self.collector().fetch(date(2026, 10, 1), date(2026, 10, 3), ZONE, ['Tair_1_Avg'])
            self.assertEqual(len(rows), 3)
            fallback.assert_not_called()

    def test_partial_hcdp_response_fills_only_missing_values_and_keeps_hcdp_priority(self):
        with patch('as_db.collection.mesonet.MesonetCollector') as primary, \
             patch('as_db.collection.mesonet.SynopticCollector') as fallback:
            primary.return_value.fetch.return_value = [Reading(date(2026, 10, 1), 'Tair_1_Avg', 0)]
            fallback.return_value.fetch.return_value = [
                Reading(date(2026, 10, day), 'Tair_1_Avg', 30) for day in (1, 2, 3)]
            rows = self.collector().fetch(date(2026, 10, 1), date(2026, 10, 3), ZONE, ['Tair_1_Avg'])
            self.assertEqual({row.day.day: row.value for row in rows}, {1: 0, 2: 30, 3: 30})
            fallback.return_value.fetch.assert_called_once_with(
                date(2026, 10, 1), date(2026, 10, 3), ZONE, ['Tair_1_Avg'])

    def test_hcdp_auth_failure_falls_back_and_is_not_retried_for_each_chunk(self):
        with patch('as_db.collection.mesonet.MesonetCollector') as primary, \
             patch('as_db.collection.mesonet.SynopticCollector') as fallback:
            primary.return_value.fetch.side_effect = CollectionError('HCDP returned HTTP 403')
            fallback.return_value.fetch.return_value = [Reading(date(2026, 10, 1), 'Tair_1_Avg', 25)]
            collector = self.collector()
            for _ in range(2):
                collector.fetch(date(2026, 10, 1), date(2026, 10, 1), ZONE, ['Tair_1_Avg'])
            self.assertEqual(primary.return_value.fetch.call_count, 1)
            self.assertEqual(fallback.return_value.fetch.call_count, 2)

    def test_absent_hcdp_token_uses_synoptic(self):
        with patch('as_db.collection.mesonet.SynopticCollector') as fallback:
            fallback.return_value.fetch.return_value = [Reading(date(2026, 10, 1), 'Tair_1_Avg', 25)]
            collector = MesonetFallbackCollector(None, 'synoptic-token', 'Aasu Uh', '1312', '002AS')
            self.addCleanup(collector.close)
            self.assertEqual(len(collector.fetch(date(2026, 10, 1), date(2026, 10, 1), ZONE, ['Tair_1_Avg'])), 1)

    def test_both_sources_failing_is_not_reported_as_success(self):
        with patch('as_db.collection.mesonet.MesonetCollector') as primary, \
             patch('as_db.collection.mesonet.SynopticCollector') as fallback:
            primary.return_value.fetch.side_effect = CollectionError('HCDP unavailable')
            fallback.return_value.fetch.side_effect = CollectionError('Synoptic unavailable')
            with self.assertRaises(CollectionError):
                self.collector().fetch(date(2026, 10, 1), date(2026, 10, 3), ZONE, ['Tair_1_Avg'])
