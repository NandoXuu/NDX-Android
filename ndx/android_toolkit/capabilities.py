"""Developer Capabilities Explorer: a *documented* classification of what an
ordinary (non-root, non-system) Android app can do. Static knowledge plus a few
live probes; nothing here changes device state."""
from ..toolchain_manager.manager import host_which

from ..core.paths import is_android

AVAILABLE, RESTRICTED, UNSUPPORTED, PERMISSION = "Available", "Restricted", "Unsupported", "Permission Required"

# (group, capability, status, explanation)
CAPABILITIES_TABLE = [
    ("Read", "Device model, Android version, ABIs", AVAILABLE, "android.os.Build via PyJNIus; no permission."),
    ("Read", "Own app logs (logcat of NDX itself)", AVAILABLE, "An app can read its own log entries."),
    ("Read", "Other apps' logs", RESTRICTED, "READ_LOGS is signature|privileged; can be granted via `adb pm grant`."),
    ("Read", "Installed applications list", PERMISSION, "Android 11+ package visibility; QUERY_ALL_PACKAGES is Play-restricted."),
    ("Read", "Battery, storage, memory", AVAILABLE, "Public APIs and /proc, no permission."),
    ("Read", "Per-process CPU of other apps", UNSUPPORTED, "/proc of other UIDs is hidden since Android 8."),
    ("Read", "GPU utilization", UNSUPPORTED, "No public API; vendor interfaces are inaccessible."),
    ("Navigate", "Open Developer options screen", AVAILABLE, "Intent ACTION_APPLICATION_DEVELOPMENT_SETTINGS (user must enable them first)."),
    ("Navigate", "Open Wireless debugging screen", RESTRICTED, "Not a stable public intent on all OEMs; falls back to Developer options."),
    ("Navigate", "App info / usage access screens", AVAILABLE, "Public settings intents."),
    ("Control", "Wireless ADB pairing (adb pair)", PERMISSION, "User enables Wireless debugging and types the pairing code; needs an adb binary."),
    ("Control", "Enable Developer options / USB debugging", UNSUPPORTED, "Cannot be toggled by an ordinary app; Android blocks this by design."),
    ("Control", "Write secure settings", PERMISSION, "WRITE_SECURE_SETTINGS can only be granted through adb by the user."),
    ("Control", "Drive other apps' UI", PERMISSION, "Requires the user to enable an Accessibility Service; not implemented in NDX."),
    ("Control", "Install/uninstall other apps silently", UNSUPPORTED, "Only via package installer UI or adb."),
    ("Control", "Foreground service for servers", AVAILABLE, "Needs a persistent notification; POST_NOTIFICATIONS on Android 13+."),
]


def explorer():
    """Table plus live probes (adb present? running on Android?)."""
    rows = [dict(group=g, name=n, status=s, note=t) for g, n, s, t in CAPABILITIES_TABLE]
    probes = {"running_on_android": is_android(), "adb_in_path": host_which("adb") is not None}
    return rows, probes
