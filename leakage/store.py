"""One canonical atomic snapshot. Projections are recoverable, never authoritative."""
from __future__ import annotations
import copy
import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LEDGERS = ("billing_plans", "invoices", "credit_memos", "exchange_rates")
LOCK = threading.RLock()

def now():
    return datetime.now(timezone.utc).isoformat()

def initial_state(root):
    state = {name: json.loads((Path(root) / "data" / f"{name}.json").read_text()) for name in LEDGERS}
    for name, rows in state.items():
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError(f"{name}.json must contain a JSON array of objects")
    for name in ("billing_plans", "credit_memos"):
        source_key = "plan_id" if name == "billing_plans" else "memo_id"
        ids = [r.get("id", r.get(source_key)) for r in state[name]]
        if None in ids or len(set(ids)) != len(ids):
            raise ValueError(f"Missing or duplicate IDs in {name}")
    state.update(revision=0, proposals={}, receipts={}, audit=[], conversations={})
    return state

def open_store(root):
    backend = os.getenv("PERSISTENCE_BACKEND", "local")
    if os.getenv("VERCEL") == "1" and backend != "postgres":
        raise ValueError("Vercel requires PERSISTENCE_BACKEND=postgres; local state would be temporary")
    if backend == "postgres":
        from .postgres_store import PostgresStore
        return PostgresStore(root)
    if backend != "local":
        raise ValueError("PERSISTENCE_BACKEND must be local or postgres")
    return Store(root)

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sandbox = self.root / "sandbox"
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.path = self.sandbox / "snapshot.json"
        self.projection_error = None
        with self.lock():
            if not self.path.exists():
                self.commit(initial_state(self.root))
            else:
                self.project(self.read())

    @contextmanager
    def lock(self):
        with LOCK:
            with (self.sandbox / ".lock").open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self):
        return json.loads(self.path.read_text())

    def atomic(self, path, value):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w") as stream:
                json.dump(value, stream, indent=2, default=str, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def commit(self, state):
        self.atomic(self.path, state)
        # The replace above is the commit boundary. A projection failure cannot
        # turn a committed action into a failed one; restart repairs projections.
        self.project(state)

    def project(self, state):
        try:
            for name in LEDGERS:
                self.atomic(self.sandbox / f"{name}.json", state[name])
            self.atomic(self.sandbox / "audit_log.json", state["audit"])
            self.projection_error = None
        except OSError as exc:
            self.projection_error = f"Canonical snapshot committed; projection refresh required: {exc}"

    def transaction(self, operation):
        with self.lock():
            state = self.read()
            result = operation(state)
            self.commit(state)
            return copy.deepcopy(result)

    @staticmethod
    def event(state, kind, **details):
        state["audit"].append({"id": uuid.uuid4().hex, "at": now(), "kind": kind, **details})

    def new_conversation(self):
        thread = str(uuid.uuid4())
        def create(state):
            state["conversations"][thread] = {"title": "New investigation", "created_at": now(), "displayed": [], "active_question": None, "dataset": os.getenv("DATASET_MODE", "synthetic")}
            return thread
        return self.transaction(create)

    def displayed(self, thread, keys):
        def update(state):
            conv = state["conversations"][thread]
            conv["displayed"] = keys
            conv["active_question"] = keys if keys else None
        self.transaction(update)
