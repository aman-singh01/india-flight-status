import sys
from pathlib import Path

# make `import app.*` work when pytest runs from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _clean_board():
    """The schedule board is a module global -- reset it around every test."""
    from app import board

    board._sources.clear()
    yield
    board._sources.clear()
