"""Exercise the standard CSV importer without writing to a live database."""

from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from as_db.import_observations import import_observations, main


class CsvImportTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'measurements.csv'
        self.cursor = MagicMock()
        self.cursor.fetchall.side_effect = [
            [(7,)], [(3, 'test_station', 'Test Station', 'Pacific/Pago_Pago')],
        ]
        self.cursor.rowcount = 1
        self.connection = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        transaction = patch('as_db.import_observations.import_transaction',
                            return_value=nullcontext(self.connection))
        self.transaction = transaction.start()
        self.addCleanup(transaction.stop)

    def write_csv(self, content):
        self.path.write_text(content, encoding='utf-8-sig')

    def test_variable_name_daily_zero_missing_and_empty_unknown_station(self):
        self.write_csv('Date,Test Station,Empty Station\n2026-01-01,0,\n2026-01-02,,\n')
        result = import_observations(self.path, ' Air Temperature ')
        self.assertEqual((result.processed, result.written, result.missing), (1, 1, 1))
        self.assertEqual(result.ignored_stations, ('Empty Station',))
        self.assertEqual(self.cursor.execute.call_args_list[0].args[1],
                         (['air temperature', 'air temperature'],) * 2)
        copy = self.cursor.copy.return_value.__enter__.return_value
        reading = copy.write_row.call_args.args[0]
        self.assertEqual(reading[:4], (3, 7, datetime(2026, 1, 1, 11, tzinfo=timezone.utc), 86400))
        self.assertEqual(reading[5], 0)
        statements = [str(call.args[0]) for call in self.cursor.execute.call_args_list]
        inserts = [statement for statement in statements if 'INSERT INTO' in statement]
        self.assertEqual(len(inserts), 1)
        self.assertIn('INSERT INTO observations', inserts[0])

    def test_legacy_variable_code_and_explicit_interval(self):
        self.write_csv('Date,test_station\n2026-01-01,12\n')
        import_observations(self.path, 'tAIR_1_aVG', interval_seconds=300, on_conflict='update')
        self.assertEqual(self.cursor.execute.call_args_list[0].args[1][0],
                         ['tair_1_avg', 'air_temp'])
        copy = self.cursor.copy.return_value.__enter__.return_value
        self.assertEqual(copy.write_row.call_args.args[0][3], 300)
        self.assertTrue(any('DO UPDATE' in str(call.args[0])
                            for call in self.cursor.execute.call_args_list))

    def test_unknown_and_ambiguous_variables_are_rejected(self):
        self.write_csv('Date,Test Station\n2026-01-01,1\n')
        for matches, message in [([], 'Unknown variable'), ([(7,), (8,)], 'Ambiguous variable')]:
            with self.subTest(message=message):
                self.cursor.fetchall.side_effect = [matches]
                with self.assertRaisesRegex(ValueError, message):
                    import_observations(self.path, 'temperature')
                self.cursor.copy.assert_not_called()

    def test_populated_unknown_station_is_rejected(self):
        self.write_csv('Date,Test Station,Unknown\n2026-01-01,1,2\n')
        with self.assertRaisesRegex(ValueError, 'Unknown station'):
            import_observations(self.path, 'Air Temperature')
        self.cursor.copy.assert_not_called()

    def test_explicitly_skip_unknown_station(self):
        self.write_csv('Date,Test Station,Unknown\n2026-01-01,1,2\n')
        result = import_observations(self.path, 'Air Temperature', skip_unknown_stations=True)
        self.assertEqual(result.ignored_stations, ('Unknown',))
        self.assertEqual(result.processed, 1)

    def test_invalid_csv_fails_before_database_connection(self):
        cases = [
            ('', 'CSV is empty'),
            ('Date,Test Station, Test Station\n2026-01-01,1,2\n', 'Duplicate CSV'),
            ('Date,\n2026-01-01,1\n', 'must not be blank'),
            ('Station,Value\nTest Station,1\n', 'Expected Date'),
            ('Date,Test Station\n2026-01-01,1,2\n', 'expected 2 fields'),
            ('Date,Test Station\n2026-01-01\n', 'expected 2 fields'),
        ]
        for content, message in cases:
            with self.subTest(content=content):
                self.write_csv(content)
                with self.assertRaisesRegex(ValueError, message):
                    import_observations(self.path, 'Air Temperature')
        self.transaction.assert_not_called()

    def test_bad_reading_does_not_reach_observations(self):
        self.write_csv('Date,Test Station\n2026-01-01,invalid\n')
        with self.assertRaisesRegex(ValueError, 'invalid number'):
            import_observations(self.path, 'Air Temperature')
        self.assertFalse(any('INSERT INTO observations' in str(call.args[0])
                             for call in self.cursor.execute.call_args_list))

    def test_cli_reports_failure(self):
        with patch('sys.stderr') as stderr:
            with self.assertRaises(SystemExit) as raised:
                main([str(self.path), 'Air Temperature'])
        self.assertEqual(raised.exception.code, 1)
        self.assertIn('Import failed', stderr.write.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
