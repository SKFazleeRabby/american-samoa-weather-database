"""Insert variable information from a pandas DataFrame."""

from as_db.database import ImportResult, import_transaction, normalized_frame, text
from as_db.variable_catalog import canonical_code


def insert_variables(df, *, connection=None, env_file=None):
    """Upsert by variable code. Accept CSV headers or database column names.

    Provider codes are preserved. Type/Network fields are not variable-table
    columns and are ignored. Values supplied to the observation importer must
    already be in the registered unit; this function does not convert readings.
    """
    frame = normalized_frame(df, {
        'Variable Name': 'name', 'Code': 'code', 'Unit': 'unit',
        'Description': 'description',
    }, ('code', 'name', 'unit'))
    records, seen = [], set()
    for position, row in enumerate(frame.to_dict('records'), 1):
        code, name, unit = (text(row[k]) for k in ('code', 'name', 'unit'))
        code = canonical_code(code)
        if not all((code, name, unit)) or code in seen:
            raise ValueError(f'Row {position}: require a unique code, name, and unit')
        seen.add(code)
        unit = {'celcius': '°C', 'celsius': '°C', 'w/m²': 'W/m²'}.get(unit.lower(), unit)
        records.append((code, name, unit, text(row.get('description'))))
    with import_transaction(connection, env_file) as conn, conn.cursor() as cur:
        cur.executemany('''
            INSERT INTO variables (code, name, unit, description) VALUES (%s,%s,%s,%s)
            ON CONFLICT (code) DO UPDATE SET
                name=EXCLUDED.name, unit=EXCLUDED.unit, description=EXCLUDED.description
        ''', records)
        written = cur.rowcount
    return ImportResult(processed=len(records), written=written)
