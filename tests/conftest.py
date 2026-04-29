import json
from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_event():
    with open(FIXTURES_DIR / "sample_event.json") as f:
        return json.load(f)
