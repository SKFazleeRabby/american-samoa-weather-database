"""Provision the dedicated AI reader with explicit, bounded weather access."""

import psycopg
from psycopg import sql

from .web.config import connection_kwargs, settings


MARKER = 'as-db managed AI reader v1'
TABLES = ('stations', 'variables', 'observations')


def provision(conn, env):
    """Run inside a transaction; refuse to take over an unrelated existing role."""
    user = env.get('AGENT_DB_USER', 'weather_agent')
    password = env.get('AGENT_DB_PASSWORD', '')
    if (not user or len(user.encode('utf-8')) > 63 or user.startswith('pg_')
            or user in {'postgres', env.get('POSTGRES_USER', 'postgres'),
                        env.get('APP_DB_USER', 'weather_reader')}
            or len(password) < 32):
        raise ValueError('Use a separate AGENT_DB_USER and an AGENT_DB_PASSWORD of at least 32 characters')
    if password in {env.get('POSTGRES_PASSWORD'), env.get('APP_DB_PASSWORD')}:
        raise ValueError('AGENT_DB_PASSWORD must differ from the administrator and website passwords')
    role = conn.execute('''SELECT oid, rolsuper, rolcreatedb, rolcreaterole,
        rolreplication, rolbypassrls, shobj_description(oid, 'pg_authid')
        FROM pg_roles WHERE rolname=%s''', (user,)).fetchone()
    if role:
        if any(role[1:6]) or role[6] != MARKER:
            raise ValueError('Choose a new dedicated AGENT_DB_USER; existing role is not a managed reader')
        membership = conn.execute('SELECT 1 FROM pg_auth_members WHERE member=%s', (role[0],)).fetchone()
        ownership = conn.execute('''SELECT 1 FROM pg_shdepend
            WHERE refclassid='pg_authid'::regclass AND refobjid=%s AND deptype='o'
            LIMIT 1''', (role[0],)).fetchone()
        if membership or ownership:
            raise ValueError('Managed reader has unexpected memberships or ownership; review its privileges')
    ident = sql.Identifier(user)
    verb = sql.SQL('ALTER ROLE' if role else 'CREATE ROLE')
    conn.execute(sql.SQL('''{} {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5 PASSWORD {}''').format(
            verb, ident, sql.Literal(password)))
    conn.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(ident, sql.Literal(MARKER)))
    conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
        sql.Identifier(env['POSTGRES_DB']), ident))
    conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(ident))
    conn.execute(sql.SQL('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}').format(ident))
    conn.execute(sql.SQL('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}').format(ident))
    for table in TABLES:
        conn.execute(sql.SQL('GRANT SELECT ON public.{} TO {}').format(sql.Identifier(table), ident))
    for key, value in {
        'default_transaction_read_only': 'on',
        'statement_timeout': '15000',
        'lock_timeout': '3000',
        'idle_in_transaction_session_timeout': '30000',
        'search_path': 'pg_catalog, public',
        'timezone': 'UTC',
    }.items():
        conn.execute(sql.SQL('ALTER ROLE {} SET {} = {}').format(
            ident, sql.Identifier(key), sql.Literal(value)))
    # Defaults are conveniences: object ACLs must enforce the write restriction.
    # PUBLIC grants cannot be removed by revoking privileges from one role.
    writable = conn.execute('''SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%' AND n.nspname NOT LIKE 'pg_temp_%%'
          AND c.relkind IN ('r','p','v','m','f')
          AND has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
        LIMIT 1''', (user,)).fetchone()
    creatable = conn.execute('''SELECT has_database_privilege(%s,current_database(),'CREATE')
        OR EXISTS (SELECT 1 FROM pg_namespace
          WHERE nspname NOT LIKE 'pg_temp_%%' AND has_schema_privilege(%s,oid,'CREATE'))''',
        (user, user)).fetchone()[0]
    if writable or creatable:
        raise ValueError('Reader inherits write/CREATE access (possibly through PUBLIC); review grants before provisioning')


def main():
    env = settings()
    with psycopg.connect(**connection_kwargs(admin=True)) as conn:
        provision(conn, env)
    print('AI reader ready: weather tables only, five connections, 15-second query timeout.')


if __name__ == '__main__':
    main()
