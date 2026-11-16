"""Stable document rows for Qt Quick; progress changes keep existing delegates."""
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt


class DocumentListModel(QAbstractListModel):
    ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._positions: dict[str, int] = {}

    def _reindex(self):
        self._positions = {row["id"]: position for position, row in enumerate(self._rows)}

    def roleNames(self):
        return {self.ROW_ROLE: b"modelData"}

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return self._rows[index.row()] if role == self.ROW_ROLE else None

    def sync(self, rows):
        """Reconcile identities without a model reset or replacing unchanged rows."""
        if not self._rows and rows:
            self.beginInsertRows(QModelIndex(), 0, len(rows) - 1)
            self._rows = list(rows)
            self._reindex()
            self.endInsertRows()
            return
        if self._rows and not rows:
            self.beginRemoveRows(QModelIndex(), 0, len(self._rows) - 1)
            self._rows.clear()
            self._positions.clear()
            self.endRemoveRows()
            return
        wanted = {row["id"] for row in rows}
        index = len(self._rows) - 1
        while index >= 0:
            if self._rows[index]["id"] in wanted:
                index -= 1
                continue
            end = index
            while index >= 0 and self._rows[index]["id"] not in wanted:
                index -= 1
            self.beginRemoveRows(QModelIndex(), index + 1, end)
            del self._rows[index + 1:end + 1]
            self.endRemoveRows()
        self._reindex()

        # Reorder only surviving rows. Filtering normally keeps their relative
        # order, so restoring a large result needs no per-row index rebuilds.
        retained = [row for row in rows if row["id"] in self._positions]
        index = 0
        while index < len(retained):
            current = self._positions[retained[index]["id"]]
            if current == index:
                index += 1
                continue
            length = 1
            while (current + length < len(self._rows)
                   and index + length < len(retained)
                   and self._rows[current + length]["id"] == retained[index + length]["id"]):
                length += 1
            self.beginMoveRows(QModelIndex(), current, current + length - 1, QModelIndex(), index)
            moved = self._rows[current:current + length]
            del self._rows[current:current + length]
            self._rows[index:index] = moved
            self._reindex()
            self.endMoveRows()
            index += length

        retained_ids = set(self._positions)
        index = 0
        while index < len(rows):
            if rows[index]["id"] in retained_ids:
                index += 1
                continue
            start = index
            while index < len(rows) and rows[index]["id"] not in retained_ids:
                index += 1
            self.beginInsertRows(QModelIndex(), start, index - 1)
            self._rows[start:start] = rows[start:index]
            self.endInsertRows()
        self._reindex()
        for row in rows:
            self.update_row(row)

    def update_row(self, row):
        position = self._positions.get(row["id"])
        if position is not None and self._rows[position] != row:
            self._rows[position] = row
            index = self.index(position)
            self.dataChanged.emit(index, index, [self.ROW_ROLE])
