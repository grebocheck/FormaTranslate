"""Small helpers for actual pointer interactions with desktop controls."""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest


def visual_item(root, name):
    if root.objectName() == name:
        return root
    for child in root.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


def item(window, name):
    found = window.findChild(QQuickItem, name)
    if found is None:
        found = visual_item(window.contentItem(), name)
    assert found is not None, name
    return found


def click(window, name):
    # A resize or model change lays out controls on the next render/event turn.
    QTest.qWait(20)
    button = item(window, name)
    assert button.isVisible() and button.isEnabled(), name
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), name
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
    QTest.qWait(20)
    return button
