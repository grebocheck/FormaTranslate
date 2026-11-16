from __future__ import annotations

import random

import pytest
from PySide6.QtCore import QPersistentModelIndex, qInstallMessageHandler
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy

from forma.presentation.document_model import DocumentListModel


@pytest.fixture
def model(qapp):
    failures = []

    def message_handler(kind, context, message):
        if context.category == "qt.modeltest":
            failures.append(message)
        elif previous is not None:
            previous(kind, context, message)

    previous = qInstallMessageHandler(message_handler)
    model = DocumentListModel()
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    try:
        yield model
    finally:
        del tester
        qInstallMessageHandler(previous)
    assert not failures


def rows(identities):
    return [{"id": str(identity), "label": f"Value {identity}"} for identity in identities]


def snapshot(model):
    return [model.data(model.index(index), model.ROW_ROLE) for index in range(model.rowCount())]


def test_large_filter_restores_in_ranges_and_preserves_selected_row(model):
    original = rows(range(10000))
    model.sync(original)
    selected = QPersistentModelIndex(model.index(4321))
    selected_data = model.data(selected, model.ROW_ROLE)
    resets = QSignalSpy(model.modelReset)
    removals = QSignalSpy(model.rowsRemoved)
    insertions = QSignalSpy(model.rowsInserted)
    moves = QSignalSpy(model.rowsMoved)
    changes = QSignalSpy(model.dataChanged)

    model.sync([dict(original[4321])])
    assert selected.isValid() and selected.row() == 0
    assert model.data(selected, model.ROW_ROLE) is selected_data
    assert [removals.at(index)[1:] for index in range(removals.count())] == [
        [4322, 9999], [0, 4320],
    ]

    model.sync([dict(row) for row in original])
    assert selected.isValid() and selected.row() == 4321
    assert model.data(selected, model.ROW_ROLE) is selected_data
    assert snapshot(model) == original
    assert [insertions.at(index)[1:] for index in range(insertions.count())] == [
        [0, 4320], [4322, 9999],
    ]
    assert resets.count() == moves.count() == changes.count() == 0

    model.update_row({**original[4321], "label": "Edited"})
    assert model.data(selected, model.ROW_ROLE)["label"] == "Edited"
    assert changes.count() == 1 and changes.at(0)[0].row() == 4321


def test_sparse_filter_restores_order_and_only_emits_changed_data(model):
    original = rows(range(80))
    model.sync(original)
    selected = QPersistentModelIndex(model.index(18))
    untouched = model.data(model.index(0), model.ROW_ROLE)
    resets = QSignalSpy(model.modelReset)
    moves = QSignalSpy(model.rowsMoved)
    changes = QSignalSpy(model.dataChanged)

    model.sync(original[::3])
    assert selected.isValid() and selected.row() == 6
    restored = [dict(row) for row in original]
    restored[18]["label"] = "Edited"
    model.sync(restored)

    assert snapshot(model) == restored
    assert selected.isValid() and selected.row() == 18
    assert model.data(model.index(0), model.ROW_ROLE) is untouched
    assert resets.count() == moves.count() == 0
    assert changes.count() == 1
    assert changes.at(0)[0].row() == changes.at(0)[1].row() == 18
    assert changes.at(0)[2] == [model.ROW_ROLE]


def test_mixed_insert_remove_and_block_move_keeps_persistent_identities(model):
    original = rows("abcdefgh")
    model.sync(original)
    retained = {
        row["id"]: QPersistentModelIndex(model.index(index))
        for index, row in enumerate(original)
    }
    untouched = model.data(retained["c"], model.ROW_ROLE)
    resets = QSignalSpy(model.modelReset)
    moves = QSignalSpy(model.rowsMoved)
    changes = QSignalSpy(model.dataChanged)
    target = rows("xcdeayhz")
    target[3]["label"] = "Changed"

    model.sync(target)

    assert snapshot(model) == target
    assert model.data(retained["c"], model.ROW_ROLE) is untouched
    assert resets.count() == 0 and moves.count() == 1
    assert moves.at(0)[1:3] == [1, 3] and moves.at(0)[4] == 0
    assert changes.count() == 1 and changes.at(0)[0].row() == 3
    positions = {row["id"]: index for index, row in enumerate(target)}
    for identity, index in retained.items():
        assert index.isValid() == (identity in positions)
        if index.isValid():
            assert index.row() == positions[identity]
            assert model.data(index, model.ROW_ROLE)["id"] == identity


def test_repeated_reconciliation_follows_qt_model_contract(model):
    rng = random.Random(731)
    original = []
    resets = QSignalSpy(model.modelReset)
    for revision in range(80):
        retained = {
            row["id"]: QPersistentModelIndex(model.index(index))
            for index, row in enumerate(original)
        }
        target = rows(rng.sample(range(32), rng.randrange(33)))
        for row in target:
            if rng.randrange(4) == 0:
                row["label"] = f"Edited {revision}"
        model.sync(target)
        assert snapshot(model) == target
        positions = {row["id"]: index for index, row in enumerate(target)}
        for identity, index in retained.items():
            assert index.isValid() == (identity in positions)
            if index.isValid():
                assert index.row() == positions[identity]
                assert model.data(index, model.ROW_ROLE)["id"] == identity
        original = target
    assert resets.count() == 0
