"""Shared connection, transaction, and validation helpers for DataFrame imports."""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path

from dotenv import dotenv_values
import pandas as pd
import psycopg


@dataclass(frozen=True)
class ImportResult:
    processed: int
    written: int
    skipped: int = 0
    missing: int = 0
    ignored_stations: tuple[str, ...] = ()


def connect_database(env_file=None):
    """Connect using .env; existing environment variables take precedence.

    By default, find .env in the current directory or its parents. Supply
    env_file explicitly when calling from another working directory.
    """
    if env_file is None:
        env_file = next(
            (p / '.env' for p in (Path.cwd(), *Path.cwd().parents) if (p / '.env').is_file()),
            None,
        )
    elif not Path(env_file).is_file():
        raise ValueError(f'Environment file does not exist: {env_file}')
    settings = dict(dotenv_values(env_file)) if env_file is not None else {}
    settings.update(os.environ)
    if not settings.get('POSTGRES_DB'):
        raise ValueError('Set POSTGRES_DB in .env or the environment')
    return psycopg.connect(
        dbname=settings['POSTGRES_DB'],
        user=settings.get('POSTGRES_USER', 'postgres'),
        password=settings.get('POSTGRES_PASSWORD'),
        host=settings.get('POSTGRES_HOST', '127.0.0.1'),
        port=settings.get('POSTGRES_PORT', '5432'),
        connect_timeout=10,
        options='-c search_path=public -c timezone=UTC',
    )


@contextmanager
def import_transaction(connection=None, env_file=None):
    """Commit an import, or use a savepoint inside a caller-owned transaction."""
    if connection is None:
        with connect_database(env_file) as conn:
            yield conn
    else:
        with connection.transaction():
            yield connection


def text(value):
    if pd.isna(value):
        return None
    result = str(value).strip()
    return result or None


def number(value, label):
    if text(value) is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label}: expected a number, got a boolean')
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label}: invalid number {value!r}') from exc
    if not math.isfinite(result):
        raise ValueError(f'{label}: expected a finite number')
    return result


def normalized_frame(df, aliases, required):
    if not isinstance(df, pd.DataFrame):
        raise TypeError('Expected a pandas DataFrame')
    columns = [aliases.get(str(c).strip(), str(c).strip()) for c in df.columns]
    if len(columns) != len(set(columns)):
        raise ValueError('Duplicate DataFrame columns after normalizing column names')
    missing = set(required) - set(columns)
    if missing:
        raise ValueError(f'Missing required columns: {", ".join(sorted(missing))}')
    return df.rename(columns=dict(zip(df.columns, columns)))
