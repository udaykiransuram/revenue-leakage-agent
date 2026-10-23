"""Hosted startup refuses ephemeral state and enforces selected providers."""
import os

import pytest

from leakage.hosted import configure_hosted
from leakage.store import open_store, Store


def test_hosted_startup_keeps_openrouter_and_disables_ollama(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test-only')
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('OPENROUTER_BACKUP_ENABLED', 'false')
    for key in ('AGENT_MODE', 'DATASET_MODE', 'PERSISTENCE_BACKEND', 'LANGSMITH_TRACING'):
        monkeypatch.setenv(key, 'old-setting')
    configure_hosted()
    assert os.environ['MODEL_PROVIDER'] == 'groq'
    assert os.environ['OLLAMA_BACKUP_ENABLED'] == 'false'
    assert os.environ['OPENROUTER_BACKUP_ENABLED'] == 'true'
    assert os.environ['PERSISTENCE_BACKEND'] == 'postgres'


@pytest.mark.parametrize('missing', ['DATABASE_URL', 'GROQ_API_KEY', 'OPENROUTER_API_KEY'])
def test_missing_hosted_secret_stops_startup(monkeypatch, missing):
    for key in ('DATABASE_URL', 'GROQ_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.setenv(key, 'test-only')
    monkeypatch.delenv(missing)
    for key in ('AGENT_MODE', 'MODEL_PROVIDER', 'DATASET_MODE', 'PERSISTENCE_BACKEND',
                'OLLAMA_BACKUP_ENABLED', 'OPENROUTER_BACKUP_ENABLED', 'LANGSMITH_TRACING'):
        monkeypatch.setenv(key, 'test-setting')
    with pytest.raises(ValueError, match=missing):
        configure_hosted()


def test_vercel_refuses_local_state_before_writing_any_files(root, monkeypatch):
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('PERSISTENCE_BACKEND', raising=False)
    with pytest.raises(ValueError, match='temporary'):
        open_store(root)
    assert not (root / 'sandbox').exists()


def test_local_backend_remains_available(root, monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('PERSISTENCE_BACKEND', raising=False)
    assert isinstance(open_store(root), Store)


def test_unknown_backend_does_not_silently_fall_back(root, monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('PERSISTENCE_BACKEND', 'unknown')
    with pytest.raises(ValueError, match='local or postgres'):
        open_store(root)
