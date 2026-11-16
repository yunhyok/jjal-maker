"""Desktop entry point."""

import sys


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--version":
        from jjal_maker import __version__
        if sys.stdout:
            print(__version__)
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from jjal_maker.selftest import run
        return run(sys.argv[2])
    from PySide6.QtWidgets import QApplication
    from jjal_maker.ui import MainWindow

    app = QApplication(sys.argv)
    app.setOrganizationName("JjalMaker")
    app.setApplicationName("Jjal Maker")
    app.setApplicationVersion("0.1.0")
    window = MainWindow()
    window.show()
    if len(sys.argv) == 2:
        window.open_path(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
