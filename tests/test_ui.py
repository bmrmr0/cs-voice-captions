from ui import overlay_wants


def test_overlay_shows_translations_from_picked_languages_only():
    picked = ["ru", "uk", "pl"]
    assert overlay_wants({"kind": "chat", "translated": True, "lang": "ru"}, picked)
    assert not overlay_wants({"kind": "chat", "translated": False, "lang": ""}, picked)
    assert not overlay_wants({"kind": "chat", "translated": True, "lang": "de"}, picked)
    assert not overlay_wants({"kind": "chat", "translated": True, "lang": ""}, picked)
    assert overlay_wants({"kind": "chat", "translated": True, "lang": "de"}, [])
    assert overlay_wants({"translated": True, "lang": "ru"}, picked)          # voice


def test_settings_window_round_trip(qapp, isolated_config):
    from settings_window import SettingsWindow
    cfg = isolated_config.load()
    changes = []
    sw = SettingsWindow(cfg, on_change=lambda s, k: changes.append((s, k)))
    sw.refresh()
    ticked = [c for c, cb in sw.lang_boxes.items() if cb.isChecked()]
    assert ticked == cfg["translation"]["languages"]
    sw.cb_any.setChecked(True)
    assert cfg["translation"]["languages"] == []
    sw.cb_any.setChecked(False)                     # falls back to the defaults
    assert cfg["translation"]["languages"] == isolated_config.DEFAULTS["translation"]["languages"]
    sw.cb_overlay.setChecked(False)
    assert ("overlay", "enabled") in changes and cfg["overlay"]["enabled"] is False
    sw.restore_defaults()
    assert cfg["overlay"]["enabled"] is True


def test_welcome_window_builds_for_each_condebug_state(qapp, isolated_config):
    from welcome_window import WelcomeWindow
    cfg = isolated_config.load()
    for status in (True, False, None):
        WelcomeWindow(cfg, status)
