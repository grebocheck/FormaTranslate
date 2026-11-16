from __future__ import annotations

import json
import os

import pytest

from forma.core.models import FormaError
from forma.persistence.session_store import DEFAULT_OPTIONS, MAX_DOCUMENTS, SessionStore


def state():
    return {"version": 1, "selected": "history:one", "mode": "review", "history": ["one"], "options": dict(DEFAULT_OPTIONS),
            "navigation": {"history:one": {"filter": "all", "search": "тиск", "entry": "paragraph:2",
                                          "reportKey": "a" * 64, "fingerprint": "b" * 64}}}


def test_session_navigation_round_trip_is_private_and_has_no_document_copies(tmp_path):
    path = tmp_path / "local" / "workspace.json"
    store = SessionStore(path)
    assert store.load()["mode"] == "translation"
    store.save(state())
    assert SessionStore(path).load() == state()
    assert set(json.loads(path.read_text())) == {"version", "selected", "mode", "history", "navigation", "options"}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content", [b'{"version":999}', b'{broken', b'\xff'])
def test_invalid_session_is_never_replaced(tmp_path, content):
    path = tmp_path / "workspace.json"
    path.write_bytes(content)
    store = SessionStore(path)
    with pytest.raises(FormaError):
        store.load()
    with pytest.raises(FormaError):
        store.save(state())
    assert path.read_bytes() == content


def test_session_rejects_extra_content_and_unbounded_navigation_without_erase(tmp_path):
    store = SessionStore(tmp_path / "workspace.json")
    store.save(state())
    before = store.path.read_bytes()
    extra = state()
    extra["documentText"] = "not navigation metadata"
    with pytest.raises(FormaError):
        store.save(extra)
    large = state()
    large["navigation"] = {str(index): dict(large["navigation"]["history:one"]) for index in range(MAX_DOCUMENTS + 1)}
    with pytest.raises(FormaError):
        store.save(large)
    assert store.path.read_bytes() == before
