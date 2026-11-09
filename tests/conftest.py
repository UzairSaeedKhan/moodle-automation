import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # for `import fakes`

FIXTURES = Path(__file__).parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")
