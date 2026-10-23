import shutil
from pathlib import Path
import pytest
from leakage.store import Store

@pytest.fixture
def root(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[1]/'data',tmp_path/'data')
    return tmp_path

@pytest.fixture
def store(root):
    return Store(root)

@pytest.fixture
def actions(store):
    from leakage.actions import Actions
    return Actions(store,store.new_conversation())
