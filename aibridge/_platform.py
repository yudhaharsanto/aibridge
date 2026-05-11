"""macOS-specific: hide dock icon when running as background daemon."""
from __future__ import annotations

import os
import platform


def hide_dock_icon() -> None:
    """
    Kalau dipanggil sebelum GUI framework aktif, Python process gak nongol
    di dock macOS. No-op di Linux/Windows.

    Cara kerjanya: set NSApplicationActivationPolicyProhibited via PyObjC.
    PyObjC udah ke-install sebagai dependency camoufox (pyobjc-framework-Cocoa).
    """
    if platform.system() != "Darwin":
        return
    # ENV opt-out (debug)
    if os.environ.get("AIBRIDGE_SHOW_DOCK", "") == "1":
        return
    try:
        from AppKit import NSApp, NSApplication  # type: ignore
        # NSApplicationActivationPolicyProhibited = 2
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(2)
    except Exception:
        # PyObjC mungkin belum ada → silent fallback
        pass
