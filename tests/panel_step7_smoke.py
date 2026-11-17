"""Run separately: QWidget smoke test, isolated from suites using QGuiApplication."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QHeaderView, QMessageBox, QComboBox

from app.core import workflow, match_text
from app.panel import PanelWindow, COLS, C_JA, C_VI, C_LINES, C_MEANING, _batch_status
from app.core.semantic_review import confirm_region


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
    assert table.item(0,C_MEANING).text() == "Chưa đối chiếu"
    assert not table.item(0,C_MEANING).flags() & Qt.ItemIsEditable
    confirm_region(project,page,r)
    PanelWindow.fill_table(window)
    assert table.item(0,C_MEANING).text() == "Đã xác nhận"
    PanelWindow._item_changed(window,table.item(0,C_JA))
    assert edits[-1] == ("R01","ja","ありがとうございます")
    PanelWindow._item_changed(window,table.item(0,C_LINES))
    assert edits[-1] == ("R01","lines",["Cảm ơn anh rất nhiều."])
    r["ja"] = "違います"
    PanelWindow.fill_table(window)
    assert table.item(0,C_VI).text() == ""
    assert table.item(0,C_MEANING).text() == "Chưa đối chiếu"
    assert table.item(0,C_LINES).text() == "Cảm ơn anh rất nhiều."
    window.runner = SimpleNamespace(busy=False)
    window.commit_editors = lambda:None
    window._selected_region = lambda:(page,r)
    window.fill_table = lambda:PanelWindow.fill_table(window)
    with patch("app.panel.QMessageBox.question",return_value=QMessageBox.No) as question:
        PanelWindow.confirm_meaning(window)
        assert question.call_args.args[-1] == QMessageBox.No
        assert r["ja"] in question.call_args.args[2] and r["lines"][0] in question.call_args.args[2]
        assert table.item(0,C_MEANING).text() == "Chưa đối chiếu"
    with patch("app.panel.QMessageBox.question",return_value=QMessageBox.Yes):
        PanelWindow.confirm_meaning(window)
        assert table.item(0,C_MEANING).text() == "Đã xác nhận"
    project.file(page,"assignment-preview.png").write_bytes(b"draft fixture")
    window.view_kind=QComboBox()
    window.view_kind.addItem("Raw","raw.png")
    window.view_kind.addItem("Bản gán thử","assignment-preview.png")
    window.view_kind.addItem("Kết quả typeset","preview.png")
    window.runner.steps=["match"]
    window.runner.keys=[page["key"]]
    window.runner.cancel=False
    window.current=page["key"]
    window._busy=lambda value:None
    window.progress=SimpleNamespace(setText=lambda text:None)
    window.refresh_pages=lambda:None
    window.show_page=lambda key:None
    window.log=lambda *args:None
    PanelWindow._finished(window)
    assert window.view_kind.currentData()=="assignment-preview.png"
    captures=[]
    window.view=SimpleNamespace(show_page=lambda *args:captures.append(args))
    PanelWindow.refresh_view(window)
    assert captures[-1][1]==[],"Draft text must be visible without ID boxes over it"
    project.file(page,"preview.png").write_bytes(b"older export")
    project.set_status(page,"typeset","error","Dừng trước typeset — còn câu chưa gán")
    window.view_kind.setCurrentIndex(window.view_kind.findData("preview.png"))
    PanelWindow.refresh_view(window)
    assert window.view_kind.currentData()=="assignment-preview.png"
    assert project.status(page,"typeset")["state"]=="blocked"
    assert _batch_status(["done","warn","blocked","error"]) == "Xong 2/4 trang · 1 cần đối chiếu · 1 lỗi · 1 có cảnh báo"
    table.close()
print("PASS: Vietnamese reference column, read-only flags, Japanese/translation editing, stale reference hiding")
