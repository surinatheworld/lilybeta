import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    for arg in sys.argv[1:]:
        if arg.lower().endswith((".docx", ".txt")):
            win.load_docs(arg)
        else:
            win.load_image(arg)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
