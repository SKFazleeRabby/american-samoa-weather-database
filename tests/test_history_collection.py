"""Historical boundaries, Docker bootstrap, batching, and shared ingestion."""
from contextlib import nullcontext
import csv
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from as_db.collection.runner import collect_provider, create_collectors, DEFAULT_PROVIDERS
from as_db.collection.synoptic import CollectionError, Reading
from as_db.collection.wrcc import WRCCCollector
from as_db.database import ImportResult
from as_db.history import historical_end_dates, history_manifest
from as_db.insert_variables import insert_variables
from as_db.import_observations import import_observations

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo('Pacific/Pago_Pago')


class HistoryTests(unittest.TestCase):
    def test_all_six_csvs_have_codes_and_populated_stations_registered(self):
        _, datasets = history_manifest(ROOT)
        self.assertEqual(len(datasets), 6)
        codes = set(pd.read_csv(ROOT / 'variables.csv')['Code'])
        names = set(pd.read_csv(ROOT / 'stations.csv')['Station'])
        for dataset in datasets:
            with self.subTest(file=dataset.path.name):
                self.assertIn(dataset.variable_code, codes)
                frame = pd.read_csv(dataset.path)
                for column in frame:
                    if column.casefold() != 'date' and frame[column].notna().any():
                        self.assertIn(column, names)
        ends = historical_end_dates(datasets)
        self.assertEqual(ends['precipitation'], date(2026, 9, 25))
        self.assertEqual(ends['soil_moisture'], date(2026, 8, 24))
        self.assertEqual(ends['stream_stage'], date(2026, 8, 8))

    def test_metadata_csv_is_accepted_by_real_metadata_validation(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.rowcount = 6
        with patch('as_db.insert_variables.import_transaction', return_value=nullcontext(connection)):
            result = insert_variables(pd.read_csv(ROOT / 'variables.csv'))
        self.assertEqual(result.processed, 6)

    def test_entire_bundle_passes_standard_import_validation_without_database_writes(self):
        metadata = pd.read_csv(ROOT / 'stations.csv')
        stations = [(position, name.lower().replace(' ', '_'), name, str(ZONE))
                    for position, name in enumerate(metadata.Station, 1)]
        variables = {row['Code'].casefold(): position for position, row in
                     enumerate(pd.read_csv(ROOT / 'variables.csv').to_dict('records'), 1)}

        class Cursor:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query, params=()):
                query = str(query)
                if 'SELECT id FROM variables' in query:
                    self.rows = [(value,) for key, value in variables.items() if key in params[0]]
                elif 'FROM stations' in query:
                    self.rows = stations
                elif 'CREATE TEMP TABLE' in query:
                    self.keys = set()
                elif 'INSERT INTO observations' in query:
                    self.rowcount = len(self.keys)

            def fetchall(self):
                return self.rows

            def copy(self, query):
                return nullcontext(self)

            def write_row(self, row):
                key = row[:4]
                if key in self.keys:
                    raise ValueError('Duplicate observation in historical CSV')
                self.keys.add(key)

        connection = MagicMock()
        connection.transaction.side_effect = lambda: nullcontext()
        connection.cursor.side_effect = lambda **kwargs: Cursor()
        for dataset in history_manifest(ROOT)[1]:
            with self.subTest(file=dataset.path.name):
                expected = int(pd.read_csv(dataset.path).iloc[:, 1:].notna().sum().sum())
                result = import_observations(dataset.path, dataset.variable_code, connection=connection)
                self.assertEqual(result.written, expected)

    def test_maximum_csv_date_includes_empty_readings_and_is_not_last_physical_row(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data' / 'temperatures.csv').write_text(
                'date,Station\n2026-09-30,\n2026-09-01,1\n', encoding='utf-8')
            manifest = {'station_timezone': str(ZONE), 'datasets': [
                {'file': 'data/temperatures.csv', 'variable_code': 'Tair_1_Avg', 'interval_seconds': 86400}]}
            path = root / 'imports.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            _, datasets = history_manifest(root, path)
            self.assertEqual(historical_end_dates(datasets), {'air_temp': date(2026, 9, 30)})
            (root / 'data' / 'unmapped.csv').write_text('Date,Station\n2026-01-01,1\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Add variable mappings'):
                history_manifest(root, path)

    def test_first_boot_passes_every_csv_to_standard_importer_in_one_transaction(self):
        spec = importlib.util.spec_from_file_location('seed_history', ROOT / 'scripts' / 'seed_history.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        connection = MagicMock()
        _, datasets = history_manifest(ROOT)
        with patch.object(module, 'history_manifest', return_value=(str(ZONE), datasets)), \
             patch.object(module, 'insert_stations') as stations, \
             patch.object(module, 'insert_variables') as variables, \
             patch.object(module, 'import_observations', return_value=ImportResult(1, 1)) as importer:
            self.assertEqual(module.seed_history(connection, ROOT), 6)
        connection.transaction.assert_called_once()
        self.assertEqual(importer.call_count, 6)
        self.assertEqual({call.args[0] for call in importer.call_args_list}, {dataset.path for dataset in datasets})
        for call in importer.call_args_list:
            self.assertIs(call.kwargs['connection'], connection)
        stations.assert_called_once()
        variables.assert_called_once()


class FakeConnection:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.transactions = 0

    def execute(self, query, params=()):
        if 'FROM stations' in query:
            return SimpleNamespace(fetchone=lambda: {'id': 1, 'code': 'station', 'timezone': str(ZONE)})
        if 'FROM variables' in query:
            return [{'code': 'air_temp', 'unit': '°C'}, {'code': 'soil_moisture', 'unit': '%'}]
        return [{'local_date': day} for day in self.existing.get(params[1][-1], set())]

    def transaction(self):
        self.transactions += 1
        return nullcontext()


class FakeCollector:
    name = 'fixture'
    station_name = 'Test Station'
    variable_codes = ('Tair_1_Avg', 'SM_1_Avg')
    max_window_days = 31

    def __init__(self):
        self.calls = []

    def fetch(self, first, last, zone, codes):
        self.calls.append((first, last, codes))
        return [Reading(first + timedelta(days=offset), code, 0)
                for code in codes for offset in range((last - first).days + 1)]


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.imported = []
        def importer(source, code, **kwargs):
            rows = list(csv.DictReader(source))
            self.imported.extend((code, row) for row in rows)
            return ImportResult(len(rows), len(rows))
        patched = patch('as_db.collection.runner.import_observations', side_effect=importer)
        self.importer = patched.start()
        self.addCleanup(patched.stop)

    def test_backfill_uses_each_variables_csv_cutoff_and_month_sized_requests(self):
        collector = FakeCollector()
        result = collect_provider(FakeConnection(), collector, end=date(2026, 10, 20), history_dates={
            'air_temp': date(2026, 9, 25), 'soil_moisture': date(2026, 8, 24)})
        self.assertFalse(result['errors'])
        self.assertEqual(len(collector.calls), 3)
        self.assertTrue(all((last - first).days > 1 for first, last, _ in collector.calls))
        first_by_code = {code: min(first for first, _, codes in collector.calls if code in codes)
                         for code in collector.variable_codes}
        self.assertEqual(first_by_code, {'Tair_1_Avg': date(2026, 9, 26), 'SM_1_Avg': date(2026, 8, 25)})
        self.assertEqual(result['inserted'], 25 + 57)
        self.assertTrue(all(row['Test Station'] == '0' for _, row in self.imported))

    def test_daily_mode_requests_exactly_yesterday(self):
        collector = FakeCollector()
        yesterday = datetime.now(ZONE).date() - timedelta(days=1)
        result = collect_provider(FakeConnection(), collector, mode='daily', history_dates={
            'air_temp': date(2020, 1, 1), 'soil_moisture': date(2020, 1, 1)})
        self.assertEqual(collector.calls, [(yesterday, yesterday, ['Tair_1_Avg', 'SM_1_Avg'])])
        self.assertEqual(result['inserted'], 2)

    def test_complete_database_makes_no_provider_requests(self):
        collector = FakeCollector()
        days = {date(2026, 10, day) for day in (1, 2, 3)}
        connection = FakeConnection({'air_temp': days, 'soil_moisture': days})
        result = collect_provider(connection, collector, end=date(2026, 10, 3), history_dates={
            'air_temp': date(2026, 9, 30), 'soil_moisture': date(2026, 9, 30)})
        self.assertFalse(collector.calls)
        self.assertEqual(result['inserted'], 0)

    def test_explicit_start_cannot_cross_historical_csv_boundary(self):
        collector = FakeCollector()
        collect_provider(FakeConnection(), collector, start=date(1966, 1, 1), end=date(2026, 10, 2),
                         history_dates={'air_temp': date(2026, 9, 30)})
        self.assertEqual(collector.calls, [(date(2026, 10, 1), date(2026, 10, 2), ['Tair_1_Avg'])])

    def test_invalid_daily_values_fail_before_import(self):
        for value in (float('inf'), float('nan'), True):
            with self.subTest(value=value):
                collector = FakeCollector()
                collector.fetch = lambda *args: [Reading(date(2026, 10, 1), 'Tair_1_Avg', value)]
                result = collect_provider(FakeConnection(), collector, end=date(2026, 10, 1),
                                          history_dates={'air_temp': date(2026, 9, 30)})
                self.assertTrue(result['errors'])
        self.importer.assert_not_called()

    def test_failed_chunk_does_not_stop_later_chunks_or_report_success(self):
        collector = FakeCollector()
        fetch = collector.fetch
        def fail_first(first, last, zone, codes):
            if first.month == 8:
                raise CollectionError('provider unavailable')
            return fetch(first, last, zone, codes)
        collector.fetch = fail_first
        result = collect_provider(FakeConnection(), collector, end=date(2026, 10, 20),
                                  history_dates={'air_temp': date(2026, 8, 24)})
        self.assertTrue(result['errors'])
        self.assertEqual(result['inserted'], 26)

    def test_shared_import_failure_rolls_back_the_window_counts(self):
        self.importer.side_effect = ValueError('invalid measurement')
        result = collect_provider(FakeConnection(), FakeCollector(), end=date(2026, 10, 1),
                                  history_dates={'air_temp': date(2026, 9, 30)})
        self.assertTrue(result['errors'])
        self.assertEqual(result['inserted'], 0)

    def test_database_unit_mismatch_stops_download_before_any_insertion(self):
        connection = FakeConnection()
        execute = connection.execute
        connection.execute = lambda query, params=(): ([{'code': 'air_temp', 'unit': 'Kelvin'}]
            if 'FROM variables' in query else execute(query, params))
        collector = FakeCollector()
        with self.assertRaisesRegex(CollectionError, 'database units'):
            collect_provider(connection, collector, end=date(2026, 10, 1),
                             history_dates={'air_temp': date(2026, 9, 30)})
        self.assertFalse(collector.calls)
        self.importer.assert_not_called()


class SourceTests(unittest.TestCase):
    def test_default_routes_include_mesonet_wrcc_and_airport(self):
        self.assertEqual(DEFAULT_PROVIDERS, ('mesonet', 'wrcc', 'synoptic'))
        wrcc = create_collectors('wrcc', None, {})
        self.addCleanup(lambda: [collector.close() for collector in wrcc])
        self.assertEqual([(collector.station_name, collector.remote_id) for collector in wrcc],
                         [('Toa Ridge Wrcc', 'sam1'), ('Siufaga Wrcc', 'sam2')])

    def test_wrcc_downloads_year_bounded_ranges_after_csv_end_and_caches(self):
        collector = WRCCCollector('Toa Ridge Wrcc', 'sam1')
        self.addCleanup(collector.close)
        with patch.object(collector, '_download', return_value=[]) as download:
            for _ in range(2):
                collector.fetch(date(2025, 11, 1), date(2026, 2, 1), ZONE, ['Tair_1_Avg'])
            self.assertEqual([call.args for call in download.call_args_list], [
                (date(2025, 11, 1), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 2, 1))])


if __name__ == '__main__':
    unittest.main()
