"""Shared historical CSV manifest and per-variable collection boundaries."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

import pandas as pd

from as_db.variable_catalog import canonical_code


@dataclass(frozen=True)
class Dataset:
    path: Path
    variable_code: str
    interval_seconds: int


def history_manifest(root, manifest_path=None):
    root = Path(root).resolve()
    path = Path(manifest_path) if manifest_path else root / 'docker' / 'history-imports.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    datasets, listed = [], set()
    for entry in manifest['datasets']:
        path = (root / entry['file']).resolve()
        if not path.is_relative_to(root / 'data') or path in listed:
            raise ValueError('Each historical dataset must be a unique file inside data/')
        if not path.is_file():
            raise ValueError(f'Historical dataset does not exist: {path.name}')
        if entry['interval_seconds'] != 86400:
            raise ValueError('Historical collection boundaries require daily CSVs')
        listed.add(path)
        datasets.append(Dataset(path, canonical_code(entry['variable_code']), entry['interval_seconds']))
    unlisted = {path.resolve() for path in (root / 'data').glob('*.csv')} - listed
    if unlisted:
        raise ValueError('Add variable mappings to history-imports.json for: '
                         + ', '.join(sorted(path.name for path in unlisted)))
    return manifest['station_timezone'], datasets


def historical_end_dates(datasets):
    ends: dict[str, date] = {}
    for dataset in datasets:
        frame = pd.read_csv(dataset.path, encoding='utf-8-sig',
                            usecols=lambda column: column.strip().casefold() == 'date')
        if len(frame.columns) != 1 or frame.empty:
            raise ValueError(f'{dataset.path.name}: expected a nonempty Date column')
        days = pd.to_datetime(frame.iloc[:, 0], errors='raise')
        if days.isna().any():
            raise ValueError(f'{dataset.path.name}: missing historical date')
        last = days.max().date()
        ends[dataset.variable_code] = max(ends.get(dataset.variable_code, last), last)
    return ends
