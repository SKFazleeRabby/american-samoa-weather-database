"""Security checks; database tests require an explicitly disposable PostgreSQL DB."""
import os
import secrets
import unittest

import psycopg

from as_db.agent_setup import provision


class CredentialTests(unittest.TestCase):
    def test_invalid_credentials_fail_before_connecting(self):
        base = {'AGENT_DB_PASSWORD': 'a' * 64, 'POSTGRES_PASSWORD': 'b' * 64}
        for override in ({'AGENT_DB_USER': 'postgres'},
                         {'AGENT_DB_USER': 'weather_reader'},
                         {'AGENT_DB_USER': 'pg_monitor'},
                         {'AGENT_DB_USER': 'x' * 64},
                         {'AGENT_DB_USER': '\u00e9' * 32},
                         {'AGENT_DB_PASSWORD': 'short'},
                         {'AGENT_DB_PASSWORD': 'b' * 64}):
            with self.subTest(override=list(override)):
                with self.assertRaises(ValueError):
                    provision(None, {**base, **override})


@unittest.skipUnless(os.environ.get('AS_DB_SECURITY_TEST_DSN'),
                     'Set AS_DB_SECURITY_TEST_DSN to a disposable PostgreSQL database')
class ReaderPrivilegesTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(os.environ['AS_DB_SECURITY_TEST_DSN'])
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        # All DDL and role changes roll back, including deliberately hostile grants.
        for table in ('stations', 'variables', 'observations', 'api_keys'):
            self.conn.execute(f'CREATE TABLE public.{table} (id integer)')
        self.env = {'POSTGRES_DB': self.conn.info.dbname,
                    'AGENT_DB_USER': 'test_agent_' + secrets.token_hex(8),
                    'AGENT_DB_PASSWORD': secrets.token_hex(32)}

    def test_reader_can_select_but_cannot_write_even_with_readonly_off(self):
        provision(self.conn, self.env)
        provision(self.conn, self.env)  # Rotation/reruns retain the managed role.
        from psycopg import sql
        self.conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(self.env['AGENT_DB_USER'])))
        self.conn.execute('SET LOCAL default_transaction_read_only=off')
        self.conn.execute('SELECT * FROM public.observations').fetchall()
        for query in ('INSERT INTO public.observations VALUES (1)',
                      'UPDATE public.observations SET id=1',
                      'DELETE FROM public.observations',
                      'TRUNCATE public.observations',
                      'SELECT * FROM public.api_keys',
                      'CREATE TABLE public.forbidden (id integer)'):
            with self.subTest(query=query):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with self.conn.transaction():
                        self.conn.execute(query)

    def test_public_write_grant_is_rejected(self):
        self.conn.execute('GRANT INSERT ON public.observations TO PUBLIC')
        with self.assertRaisesRegex(ValueError, 'write/CREATE'):
            provision(self.conn, self.env)

    def test_public_schema_create_is_rejected(self):
        self.conn.execute('GRANT CREATE ON SCHEMA public TO PUBLIC')
        with self.assertRaisesRegex(ValueError, 'write/CREATE'):
            provision(self.conn, self.env)

    def test_unmanaged_role_is_rejected(self):
        from psycopg import sql
        self.conn.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(self.env['AGENT_DB_USER'])))
        with self.assertRaisesRegex(ValueError, 'not a managed reader'):
            provision(self.conn, self.env)
