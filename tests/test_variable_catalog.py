import os
import unittest

from as_db.variable_catalog import ALIASES, canonical_code, migrate_catalog, migration_target


class CatalogTests(unittest.TestCase):
    def test_legacy_codes_resolve_to_synoptic_names(self):
        self.assertEqual(canonical_code('Rin_1_Avg'),'solar_radiation')
        self.assertEqual(canonical_code('RF_1_Tot300s'),'precipitation')
        self.assertEqual(canonical_code('air_temp'),'air_temp')

    def test_mean_min_and_max_share_source_but_remain_distinct(self):
        targets = [migration_target({'code':code,'unit':'°C'}) for code in ('Tair_1_Avg','Tair_1_Min','Tair_1_Max')]
        self.assertEqual({r[0] for r in targets},{'air_temp','air_temp_min','air_temp_max'})
        self.assertEqual({r[2] for r in targets},{'air_temp'})
        self.assertEqual({r[3] for r in targets},{'mean','min','max'})

    def test_soil_depth_is_preserved_during_migration(self):
        row = {'code':'syn_soil_moisture_set_1_123456789abc_mean','name':'Soil at 10cm','unit':'%',
               'description':'Sensor position: -0.1 m.'}
        target = migration_target(row)
        self.assertNotEqual(target[0],'soil_moisture')
        self.assertEqual(target[1],'Soil at 10cm')
        row['description'] = 'No depth provided.'
        self.assertEqual(migration_target(row)[0],'soil_moisture')


@unittest.skipUnless(os.environ.get('AS_DB_CATALOG_TEST')=='1', 'Opt in to isolated temporary-table catalog checks')
class CatalogDatabaseTests(unittest.TestCase):
    def test_migration_preserves_ids_foreign_keys_custom_names_and_rolls_back_collisions(self):
        import psycopg
        from as_db.web.config import connection_kwargs
        with psycopg.connect(**connection_kwargs(admin=True),autocommit=True) as conn:
            conn.execute('CREATE TEMP TABLE variables(id bigint PRIMARY KEY,code text UNIQUE,name text,unit text,description text)')
            conn.execute('CREATE TEMP TABLE observations(variable_id bigint REFERENCES variables(id),value float)')
            conn.execute("INSERT INTO variables VALUES (1,'Rin_1_Avg','Solar Radiation','W/m²',NULL),(2,'Tair_1_Max','Temperature Max','°C',NULL)")
            conn.execute('INSERT INTO observations VALUES (1,123.5),(2,31)')
            changes = migrate_catalog(conn,schema='pg_temp')
            self.assertEqual(len(changes),2)
            self.assertEqual(conn.execute('SELECT variable_id,value FROM observations ORDER BY variable_id').fetchall(),[(1,123.5),(2,31.)])
            self.assertEqual(conn.execute('SELECT id,code,synoptic_code,aggregation FROM variables ORDER BY id').fetchall(),
                             [(1,'solar_radiation','solar_radiation','mean'),(2,'air_temp_max','air_temp','max')])
            conn.execute("UPDATE variables SET name='Custom Solar Label' WHERE id=1")
            self.assertEqual(migrate_catalog(conn,schema='pg_temp'),[])
            self.assertEqual(conn.execute('SELECT name FROM variables WHERE id=1').fetchone()[0],'Custom Solar Label')
            conn.execute("INSERT INTO variables(id,code,name,unit) VALUES (3,'Rin_1_Avg','Old solar','W/m²')")
            with self.assertRaises(ValueError):
                migrate_catalog(conn,schema='pg_temp')
            self.assertEqual(conn.execute('SELECT code FROM variables WHERE id=3').fetchone()[0],'Rin_1_Avg')
