import json

import hotkeys
import updates


def test_save_writes_only_what_differs(isolated_config):
    config = isolated_config
    cfg = config.load()
    cfg["overlay"]["font_size"] = 26
    assert config.save(cfg)
    with open(config.config_path(), encoding="utf-8") as f:
        assert json.load(f) == {"overlay": {"font_size": 26}}
    assert config.load()["overlay"]["font_size"] == 26


def test_release_flag(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "bundle_dir", lambda: str(tmp_path))
    assert config.is_release_build() is False
    (tmp_path / "build.json").write_text('{"release": true}')
    assert config.is_release_build() is True


def test_default_languages_are_offered_in_settings():
    import config
    from settings_window import LANGUAGES
    assert set(config.DEFAULTS["translation"]["languages"]) <= {c for c, _ in LANGUAGES}


def test_hotkey_names():
    assert hotkeys.pretty("ctrl+alt+p") == "Ctrl+Alt+P"
    assert hotkeys.pretty("shift+f9") == "Shift+F9"
    assert hotkeys.bound({"toggle_pause": "ctrl+alt+p", "reply": ""}) == [
        ("toggle_pause", "Ctrl+Alt+P")]
    assert hotkeys.bound({"enabled": False, "toggle_pause": "ctrl+alt+p"}) == []


def test_version_comparison():
    assert updates.parse_version("v1.2.3") == (1, 2, 3)
    assert updates.newer("v1.1.0", "1.0.0") and not updates.newer("v1.0.0", "1.0.0")
    assert not updates.newer("nightly", "1.0.0")
