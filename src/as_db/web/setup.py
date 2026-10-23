"""Provision a restricted website login; safe to rerun against the current schema."""
import psycopg
from psycopg import sql

from .config import connection_kwargs, settings


def main():
    env = settings()
    user = env.get('APP_DB_USER', 'weather_reader')
    password = env.get('APP_DB_PASSWORD')
    if not password or user == env.get('POSTGRES_USER', 'postgres') or user == 'postgres':
        raise ValueError('Use a separate APP_DB_USER and set APP_DB_PASSWORD')
    with psycopg.connect(**connection_kwargs(admin=True)) as conn:
        from as_db.variable_catalog import migrate_catalog
        migrate_catalog(conn)
        exists = conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (user,)).fetchone()
        role = conn.execute('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=%s', (user,)).fetchone()
        if role and any(role):
            raise ValueError('APP_DB_USER already has administrative privileges; choose a dedicated new role')
        verb = sql.SQL('ALTER ROLE') if exists else sql.SQL('CREATE ROLE')
        conn.execute(sql.SQL('{} {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(verb, sql.Identifier(user), sql.Literal(password)))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(env['POSTGRES_DB']), sql.Identifier(user)))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(user)))
        conn.execute(sql.SQL('GRANT SELECT ON public.stations, public.variables, public.observations TO {}').format(sql.Identifier(user)))
        conn.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(user)))
        conn.execute(sql.SQL('ALTER ROLE {} SET statement_timeout=15000').format(sql.Identifier(user)))
    print('Website read-only database login is ready.')


if __name__ == '__main__':
    main()
