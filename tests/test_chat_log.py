import chat_log

LRM, AT = chr(0x200E), chr(0xFE6B)          # what CS2 puts around names
SCOPES = {"ALL", "TEAM"}

VDF = """"UserLocalConfigStore"
{
  "Software"
  {
    "Valve"
    {
      "Steam"
      {
        "apps"
        {
          APPS
        }
      }
    }
  }
}
"""


def test_cs2_all_chat_line():
    ev = chat_log.parse_chat_line(f"10/04 22:08:48  [ALL] Ghost{LRM}: gg wp", SCOPES)
    assert ev == {"scope": "ALL", "side": "", "name": "Ghost", "location": "",
                  "message": "gg wp"}


def test_team_chat_is_tagged_with_side_and_location():
    ev = chat_log.parse_chat_line(
        f"10/04 22:08:48  [CT] Ghost{LRM}{AT}B Site: rotate", SCOPES)
    assert ev["scope"] == "TEAM" and ev["side"] == "CT"
    assert ev["location"] == "B Site" and ev["message"] == "rotate"


def test_csgo_timestamp_and_dead_marker():
    ev = chat_log.parse_chat_line(
        f"10/04/2024 - 22:08:48: [T] Ghost{LRM} [DEAD]: nt", SCOPES)
    assert ev["name"] == "Ghost" and ev["message"] == "nt"


def test_a_message_cannot_forge_another_line():
    # Someone typing "[ALL] Admin: ..." must not become a line from Admin.
    ev = chat_log.parse_chat_line(
        f"10/04 22:08:48  [ALL] Ghost{LRM}: [ALL] Admin: hello", SCOPES)
    assert ev["name"] == "Ghost" and ev["message"] == "[ALL] Admin: hello"
    assert chat_log.parse_chat_line("Ghost said [ALL] Admin: hi", SCOPES) is None


def test_scope_filter_and_junk():
    assert chat_log.parse_chat_line(f"[ALL] Ghost{LRM}: hi", {"TEAM"}) is None
    assert chat_log.parse_chat_line("Connected to server", SCOPES) is None
    assert chat_log.parse_chat_line(f"[ALL] Ghost{LRM}:", SCOPES) is None


def _account(steam, account, options):
    """A Steam userdata folder whose CS2 launch options are `options`
    (None: the account never ran CS2)."""
    folder = steam / "userdata" / account / "config"
    folder.mkdir(parents=True)
    apps = "" if options is None else f'"730" {{ "LaunchOptions" "{options}" }}'
    (folder / "localconfig.vdf").write_text(VDF.replace("APPS", apps), encoding="utf-8")
    return str(steam)


def test_condebug_found_missing_and_unknown(tmp_path):
    def status(sub, options):
        steam = _account(tmp_path / sub, "1", options)
        return chat_log.condebug_status(console_log="nowhere", steam=steam, account="1")
    assert status("a", "-novid -condebug +fps_max 0") is True
    assert status("b", "-novid -condebugger") is False
    assert status("c", None) is None


def test_condebug_checks_the_signed_in_account_only(tmp_path):
    steam = _account(tmp_path, "111", "-condebug")          # another account
    _account(tmp_path, "222", "-novid")                      # the one in use
    assert chat_log.condebug_status(console_log="nowhere", steam=steam,
                                    account="222") is False
