import json
from pathlib import Path

import pytest

from collector.stations import Stations

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def stations(tmp_path):
    p = tmp_path / "stations.json"
    p.write_text((FIXTURES / "stations_subset.json").read_text())
    return Stations(p)


@pytest.fixture
def crescent():
    return load_fixture("asm_19_2026-10-10.json")
