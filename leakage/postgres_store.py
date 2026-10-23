"""Durable shared snapshot with database row locks, preserving action semantics."""
import copy
import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .store import Store


def database_url():
    value = os.getenv('DATABASE_URL', '').strip()
    if not value:
        raise ValueError('DATABASE_URL is required for hosted persistence')
    return value


class PostgresStore(Store):
    """Store-compatible transactions; no canonical state on the container disk.

    Schema is initialized explicitly with ``python -m leakage.hosted setup``.
    One snapshot row preserves atomic proposal/receipt/audit commits across
    replicas. Each transaction locks only that workspace row for its duration.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.workspace = os.getenv('REVENUE_WORKSPACE', 'revenue-leakage')
        if not self.workspace or len(self.workspace) > 128:
            raise ValueError('REVENUE_WORKSPACE must contain 1–128 characters')
        self.projection_error = None
        self.pool = ConnectionPool(
            conninfo=database_url(), min_size=0, max_size=3, open=True,
            timeout=15, max_idle=60, name='revenue-store',
            kwargs={'autocommit': True, 'prepare_threshold': None,
                    'row_factory': dict_row, 'connect_timeout': 10},
            check=ConnectionPool.check_connection,
        )

    def read(self):
        with self.pool.connection() as conn:
            row = conn.execute('SELECT state FROM revenue_snapshots WHERE workspace = %s',
                               (self.workspace,)).fetchone()
        if row is None:
            raise ValueError('Hosted workspace is not initialized; run python -m leakage.hosted setup')
        return copy.deepcopy(row['state'])

    def transaction(self, operation):
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '10s'")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            row = conn.execute('SELECT state FROM revenue_snapshots WHERE workspace = %s FOR UPDATE',
                               (self.workspace,)).fetchone()
            if row is None:
                raise ValueError('Hosted workspace is not initialized; run python -m leakage.hosted setup')
            state = copy.deepcopy(row['state'])
            result = operation(state)
            conn.execute('UPDATE revenue_snapshots SET state = %s WHERE workspace = %s',
                         (Jsonb(state), self.workspace))
            # Copy before commit, so an unserializable result cannot turn a
            # successful commit into an apparent failed operation.
            returned = copy.deepcopy(result)
        return returned

    def commit(self, state):
        raise ValueError('Use transaction() for hosted state changes')

    def project(self, state):
        # The database is canonical; file projections are a local-only feature.
        return None

    def close(self):
        self.pool.close()
