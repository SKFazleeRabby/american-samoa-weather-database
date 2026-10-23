"""Bootstrap metadata and load bundled history through the standard CSV importer."""

import argparse
import os
from pathlib import Path

import pandas as pd
import psycopg

from as_db.import_observations import import_observations
from as_db.history import history_manifest
from as_db.insert_stations import insert_stations
from as_db.insert_variables import insert_variables


def seed_history(connection, data_dir):
    """Import the full bundle atomically; existing observations are skipped."""
    root = Path(data_dir).resolve()
    station_timezone, datasets = history_manifest(root, root / 'imports.json')
    total = 0
    with connection.transaction():
        stations = pd.read_csv(root / 'stations.csv', dtype={'Station ID': 'string'})
        variables = pd.read_csv(root / 'variables.csv')
        insert_stations(stations, timezone=station_timezone, connection=connection)
        insert_variables(variables, connection=connection)
        for dataset in datasets:
            path = dataset.path
            result = import_observations(
                path, dataset.variable_code,
                interval_seconds=dataset.interval_seconds, connection=connection,
            )
            if result.ignored_stations:
                print(f'{path.name}: ignoring empty unmapped columns: '
                      + ', '.join(result.ignored_stations), flush=True)
            total += result.written
            print(f'{path.name}: staged {result.written:,} readings; '
                  f'{result.skipped:,} already present; {result.missing:,} missing cells', flush=True)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('/opt/weather/history'))
    args = parser.parse_args()
    database = os.environ.get('POSTGRES_DB')
    if not database:
        parser.error('POSTGRES_DB must be set')
    # PostgreSQL's temporary initialization server accepts Unix-socket connections
    # only. The same connection also works when manually rerunning in the container.
    with psycopg.connect(
        dbname=database, user=os.environ.get('POSTGRES_USER', 'postgres'),
        host='/var/run/postgresql', port=5432, connect_timeout=10,
        options='-c search_path=public -c timezone=UTC',
    ) as connection:
        total = seed_history(connection, args.data_dir)
    print(f'Historical import committed: {total:,} new readings.', flush=True)


if __name__ == '__main__':
    main()
