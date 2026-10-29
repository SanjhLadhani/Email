"""Shared fixtures: every test gets its own throwaway SQLite file.

No test in this suite talks to Gmail, a model API, or the real ``triage.db`` -
network and mailbox calls are mocked at the boundary (``automation.step2_gmail_reader``,
``automation.step5_drafts``, the model agent). That keeps the suite fast and repeatable.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import config


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point config.DB_PATH at a fresh file per test so tests never touch triage.db."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    yield
