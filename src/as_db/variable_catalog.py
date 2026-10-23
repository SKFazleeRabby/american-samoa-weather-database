"""Synoptic-aligned daily catalog and ID-preserving legacy-code migration."""
import re

from psycopg.rows import dict_row
from psycopg import sql

# code, display name, unit, Synoptic base variable, daily aggregation
DEFINITIONS = (
    ('RF_1_Tot300s', 'precipitation', 'Precipitation', 'mm', 'precipitation', 'total'),
    ('Tair_1_Avg', 'air_temp', 'Air Temperature', '°C', 'air_temp', 'mean'),
    ('Tair_1_Max', 'air_temp_max', 'Air Temperature Maximum', '°C', 'air_temp', 'max'),
    ('Tair_1_Min', 'air_temp_min', 'Air Temperature Minimum', '°C', 'air_temp', 'min'),
    ('RH_1_Avg', 'relative_humidity', 'Relative Humidity', '%', 'relative_humidity', 'mean'),
    ('Rin_1_Avg', 'solar_radiation', 'Solar Radiation', 'W/m²', 'solar_radiation', 'mean'),
)
ALIASES = {old: new for old, new, *_ in DEFINITIONS}
ALIASES['SM_1_Avg'] = 'soil_moisture'
CATALOG = {new: (name, unit, source, aggregation)
           for _, new, name, unit, source, aggregation in DEFINITIONS}
CATALOG['soil_moisture'] = ('Soil Moisture', '%', 'soil_moisture', 'mean')


def canonical_code(code):
    return ALIASES.get(code, code)


def migration_target(row):
    code = canonical_code(row['code'])
    if code in CATALOG:
        name, _, source, aggregation = CATALOG[code]
        return code, name, source, aggregation
    match = re.fullmatch(r'syn_(.+_set_\d+d?)_([0-9a-f]{12})_(mean|min|max)', row['code'])
    if not match:
        return None
    sensor, digest, aggregation = match.groups()
    source = sensor.rsplit('_set_', 1)[0]
    description = row.get('description') or ''
    if (sensor == 'soil_moisture_set_1' and row['unit'] == '%'
            and 'Sensor position:' not in description and 'Dimensions:' not in description):
        return 'soil_moisture', 'Soil Moisture', source, aggregation
    return f'{sensor}_{digest}_{aggregation}', row['name'], source, aggregation


def migrate_catalog(conn, schema='public'):
    """Rename rows without rewriting observations; collisions roll back atomically.

    Run as an administrator before starting the updated web/collection services.
    Repeated runs populate metadata but preserve user-edited display names.
    """
    changes = []
    table = sql.Identifier(schema, 'variables')
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(717383452)')
        cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS synoptic_code varchar(100)').format(table))
        cursor.execute(sql.SQL('ALTER TABLE {} ADD COLUMN IF NOT EXISTS aggregation varchar(20)').format(table))
        cursor.execute(sql.SQL('SELECT id,code,name,unit,description FROM {} FOR UPDATE').format(table))
        rows = cursor.fetchall()
        taken = {r['code']: r['id'] for r in rows}
        for row in rows:
            target = migration_target(row)
            if target is None:
                continue
            code, name, source, aggregation = target
            if code in taken and taken[code] != row['id']:
                raise ValueError(f'Variable code {code} already exists under another ID; review before merging catalogs')
            renamed = code != row['code']
            cursor.execute(sql.SQL('''UPDATE {} SET code=%s,name=%s,
                synoptic_code=%s,aggregation=%s WHERE id=%s''').format(table),
                (code, name if renamed else row['name'], source, aggregation, row['id']))
            taken[code] = row['id']
            if renamed:
                changes.append({'id':row['id'], 'old_code':row['code'], 'code':code, 'name':name})
    return changes


def main():
    import psycopg
    from .web.config import connection_kwargs
    with psycopg.connect(**connection_kwargs(admin=True)) as conn:
        changes = migrate_catalog(conn)
    for row in changes:
        print(f"Variable {row['id']}: {row['old_code']} -> {row['code']} ({row['name']})")
    print(f'Catalog ready: {len(changes)} rows renamed; observation IDs preserved.')


if __name__ == '__main__':
    main()
