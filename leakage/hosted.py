"""Vercel container entrypoint and explicit, non-destructive database setup."""
import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def configure_hosted():
    # Enforce the requested deployment providers even if stale project settings
    # or a developer's local environment enabled a local-model fallback.
    os.environ.update(
        AGENT_MODE='live', MODEL_PROVIDER='groq', DATASET_MODE='challenge',
        PERSISTENCE_BACKEND='postgres', OLLAMA_BACKUP_ENABLED='false',
        OPENROUTER_BACKUP_ENABLED='true', LANGSMITH_TRACING='false',
    )
    missing = [key for key in ('DATABASE_URL', 'GROQ_API_KEY', 'OPENROUTER_API_KEY')
               if not os.getenv(key, '').strip()]
    if missing:
        raise ValueError('Set these Vercel environment secrets before starting: ' + ', '.join(missing))


def setup_database(root=ROOT):
    """Run once against a dedicated database before first deploy, and on upgrades.

    Existing snapshot rows and conversations are preserved. Use the provider's
    direct connection URL for schema changes, runtime pooling URL for the app.
    """
    from psycopg import Connection
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from langgraph.checkpoint.postgres import PostgresSaver
    from .postgres_store import database_url
    from .store import initial_state

    url = os.getenv('DATABASE_URL_UNPOOLED') or database_url()
    workspace = os.getenv('REVENUE_WORKSPACE', 'revenue-leakage')
    if not workspace or len(workspace) > 128:
        raise ValueError('REVENUE_WORKSPACE must contain 1–128 characters')
    with Connection.connect(url, autocommit=True, prepare_threshold=None,
                            row_factory=dict_row, connect_timeout=10) as conn:
        # Serialize schema migrations on this direct session. Do not use a
        # transaction pooler for this command: migrations include concurrent DDL.
        conn.execute('SELECT pg_advisory_lock(%s)', (7201042026,))
        try:
            PostgresSaver(conn).setup()
            with conn.transaction():
                conn.execute('''CREATE TABLE IF NOT EXISTS revenue_snapshots (
                    workspace text PRIMARY KEY, state jsonb NOT NULL
                )''')
                conn.execute('INSERT INTO revenue_snapshots (workspace, state) VALUES (%s, %s) '
                             'ON CONFLICT (workspace) DO NOTHING', (workspace, Jsonb(initial_state(root))))
        finally:
            conn.execute('SELECT pg_advisory_unlock(%s)', (7201042026,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'serve'])
    args = parser.parse_args()
    if args.command == 'setup':
        setup_database()
        print('Hosted database initialized; existing records and checkpoints preserved.')
        return
    configure_hosted()
    if os.getenv('HOSTED_AUTO_SETUP') == '1':
        setup_database()
    port = int(os.getenv('PORT', '80'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535')
    os.chdir(ROOT)
    sys.argv = ['streamlit', 'run', str(ROOT / 'app.py'),
                '--server.address=0.0.0.0', f'--server.port={port}',
                '--server.headless=true', '--server.fileWatcherType=none']
    from streamlit.web.cli import main as streamlit_main
    streamlit_main()


if __name__ == '__main__':
    main()
