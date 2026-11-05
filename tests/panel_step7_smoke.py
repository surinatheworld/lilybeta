"""Run separately: QWidget smoke test, isolated from suites using QGuiApplication."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QHeaderView

from app.core import workflow, match_text
from app.panel import PanelWindow, COLS, C_JA, C_VI, C_LINES


app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    project = workflow.Project(folder)
    project.sync_documents([{"id":1,"name":"013.jpg"}])
    page = project.page("013.jpg")
    r = workflow.new_region("R01",[10,10,80,90])
    r.update(ja="ありがとうございます",lines=["Cảm ơn anh rất nhiều."],lines_src="manual")
    match_text.save_references([r],{"R01":{"ja":r["ja"],"vi":"Cảm ơn anh.","confidence":.92,"note":""}})
    page["regions"] = [r]
    edits = []
    table = QTableWidget(0,len(COLS))
    table.setHorizontalHeaderLabels(COLS)
    for c in range(len(COLS)):
        table.horizontalHeader().setSectionResizeMode(c,QHeaderView.ResizeToContents)
    window = SimpleNamespace(table=table,project=project,page_obj=lambda:page,selected="",_filling=False,
                             _edit=lambda *args:edits.append(args))
    PanelWindow.fill_table(window)
    assert table.item(0,C_VI).text() == "Cảm ơn anh."
    assert not table.item(0,C_VI).flags() & Qt.ItemIsEditable
    assert table.item(0,C_LINES).data(Qt.UserRole) == "Cảm ơn anh rất nhiều."
    PanelWindow._item_changed(window,table.item(0,C_JA))
    assert edits[-1] == ("R01","ja","ありがとうございます")
    PanelWindow._item_changed(window,table.item(0,C_LINES))
    assert edits[-1] == ("R01","lines",["Cảm ơn anh rất nhiều."])
    r["ja"] = "違います"
    PanelWindow.fill_table(window)
    assert table.item(0,C_VI).text() == ""
    assert table.item(0,C_LINES).text() == "Cảm ơn anh rất nhiều."
    table.close()
print("PASS: Vietnamese reference column, read-only flags, Japanese/translation editing, stale reference hiding")
