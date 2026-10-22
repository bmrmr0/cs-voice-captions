"""Shared setup. Every name and chat line in these tests is made up: the
repository must never carry real players' names or messages."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Widgets are built without a screen, on CI and locally alike.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """config.load()/save() against a throwaway folder, never the real one."""
    import config
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(config, "config_path", lambda: str(tmp_path / "config.json"))
    return config


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])
