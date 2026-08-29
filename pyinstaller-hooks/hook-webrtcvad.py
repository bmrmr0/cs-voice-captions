"""Override for the bundled webrtcvad hook.

The stock hook in pyinstaller-hooks-contrib does copy_metadata("webrtcvad"),
which raises PackageNotFoundError when the module comes from the prebuilt
`webrtcvad-wheels` distribution instead (which is what requirements.txt pins,
because plain `webrtcvad` only ships a source tarball and needs a C compiler).

Same import name, different distribution name. Try both, and treat missing
metadata as non-fatal -- webrtcvad does not read its own metadata at runtime.
"""
from PyInstaller.utils.hooks import copy_metadata

datas = []
for _dist in ("webrtcvad-wheels", "webrtcvad"):
    try:
        datas += copy_metadata(_dist)
        break
    except Exception:  # noqa: BLE001  (PackageNotFoundError and friends)
        continue
