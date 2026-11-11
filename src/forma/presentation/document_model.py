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
        for index in range(len(self._rows) - 1, -1, -1):
            if self._rows[index]["id"] not in wanted:
                self.beginRemoveRows(QModelIndex(), index, index)
                del self._rows[index]
                self.endRemoveRows()
        self._reindex()
        for index, row in enumerate(rows):
            current = self._positions.get(row["id"])
            if current is None:
                self.beginInsertRows(QModelIndex(), index, index)
                self._rows.insert(index, row)
                self._reindex()
                self.endInsertRows()
            else:
                if current != index:
                    self.beginMoveRows(QModelIndex(), current, current, QModelIndex(), index)
                    self._rows.insert(index, self._rows.pop(current))
                    self._reindex()
                    self.endMoveRows()
                self.update_row(row)

    def update_row(self, row):
        position = self._positions.get(row["id"])
        if position is not None and self._rows[position] != row:
            self._rows[position] = row
            index = self.index(position)
            self.dataChanged.emit(index, index, [self.ROW_ROLE])
