from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Store


@pytest.fixture
async def store(tmp_path: Path):
    store = Store(tmp_path / "shared.db")
    await store.connect()
    yield store
    await store.close()
