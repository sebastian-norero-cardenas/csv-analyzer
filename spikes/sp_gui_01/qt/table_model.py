"""QAbstractTableModel adapter for SP-GUI-01."""
from __future__ import annotations

from collections.abc import Iterable
import time

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QTableView

from spikes.sp_gui_01.common.model import SOURCE_ROW


class FrameTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame: pd.DataFrame | None = None
        self.columns: tuple[str, ...] = ()
        self._column_positions: dict[str, int] = {}
        self.ordered_ids = np.array([], dtype=np.int64)
        self.sort_column: str | None = None
        self.sort_descending = False

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.ordered_ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.frame is None:
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        source_id = self.source_id_at(index.row())
        column = self.columns[index.column()]
        if column == SOURCE_ROW:
            return str(source_id)
        value = self.frame.iat[source_id - 1, self._column_positions[column]]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._canonical(value)
        return self._display(value)

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            column = self.columns[section]
            if column == self.sort_column:
                return column + (" ▼" if self.sort_descending else " ▲")
            return column
        return str(section + 1) if orientation == Qt.Orientation.Vertical else None

    def set_data(
        self,
        frame: pd.DataFrame | None,
        columns: Iterable[str],
        ordered_ids: np.ndarray | Iterable[int],
    ) -> None:
        self.beginResetModel()
        self.frame = frame
        self.columns = tuple(columns)
        self._column_positions = (
            {name: int(frame.columns.get_loc(name)) for name in self.columns if name != SOURCE_ROW}
            if frame is not None
            else {}
        )
        self.ordered_ids = np.asarray(ordered_ids, dtype=np.int64)
        self.endResetModel()

    def set_sort_indicator(self, column: str | None, descending: bool = False) -> None:
        self.sort_column = column
        self.sort_descending = bool(descending)
        if self.columns:
            self.headerDataChanged.emit(
                Qt.Orientation.Horizontal, 0, len(self.columns) - 1
            )

    def source_id_at(self, row: int) -> int:
        return int(self.ordered_ids[row])

    def row_for_source_id(self, source_id: int) -> int | None:
        positions = np.flatnonzero(self.ordered_ids == int(source_id))
        return int(positions[0]) if len(positions) else None

    @staticmethod
    def _display(value) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, (bool, np.bool_)):
            return "True" if bool(value) else "False"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            return format(float(value), ".8g")
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        text = str(value)
        return text if len(text) <= 48 else text[:45] + "…"

    @staticmethod
    def _canonical(value) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, (bool, np.bool_)):
            return "True" if bool(value) else "False"
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            return format(float(value), ".17g")
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return str(value)


class MeasuredTableView(QTableView):
    scroll_measured = Signal(float)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        started = time.perf_counter()
        super().scrollContentsBy(dx, dy)
        self.scroll_measured.emit(time.perf_counter() - started)
