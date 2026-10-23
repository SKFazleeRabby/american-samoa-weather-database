from pathlib import Path
import os

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[3]


def settings():
    env_file = next((p / '.env' for p in (Path.cwd(), *Path.cwd().parents) if (p / '.env').is_file()), ROOT / '.env')
    return {**dotenv_values(env_file), **os.environ}


def connection_kwargs(admin=False):
    env = settings()
    if not env.get('POSTGRES_DB'):
        raise ValueError('POSTGRES_DB is required')
    user = env.get('POSTGRES_USER', 'postgres') if admin else env.get('APP_DB_USER', 'weather_reader')
    password = env.get('POSTGRES_PASSWORD') if admin else env.get('APP_DB_PASSWORD')
    if not password:
        raise ValueError('Set POSTGRES_PASSWORD' if admin else 'Set APP_DB_PASSWORD and run the web database setup')
    return dict(dbname=env['POSTGRES_DB'], user=user, password=password,
                host=env.get('POSTGRES_HOST', '127.0.0.1'), port=env.get('POSTGRES_PORT', '5432'),
                connect_timeout=5, application_name='fieldwork-setup' if admin else 'fieldwork-api',
                options='-c timezone=UTC -c search_path=public' + ('' if admin else ' -c default_transaction_read_only=on -c statement_timeout=15000'))
