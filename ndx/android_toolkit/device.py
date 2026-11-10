"""Device information. On Android this uses PyJNIus; elsewhere /proc + platform."""
import platform

from ..core.paths import is_android


def device_info():
    info = {"source": "platform"}
    if is_android():
        try:
            from jnius import autoclass
            Build = autoclass("android.os.Build")
            VERSION = autoclass("android.os.Build$VERSION")
            info.update(source="android-api", manufacturer=Build.MANUFACTURER, model=Build.MODEL,
                        device=Build.DEVICE, android=VERSION.RELEASE, sdk=int(VERSION.SDK_INT),
                        abis=list(Build.SUPPORTED_ABIS))
            return info
        except Exception as exc:
            info["note"] = "pyjnius unavailable: %s" % exc
    info.update(machine=platform.machine(), system=platform.system(), release=platform.release())
    return info


def open_developer_settings():
    """Navigate-only: opens Android's Developer options screen. Returns (ok, message)."""
    if not is_android():
        return False, "Not running on Android"
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        intent = Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
        PythonActivity.mActivity.startActivity(intent)
        return True, "Opened Developer options"
    except Exception as exc:
        return False, "Could not open Developer options: %s" % exc


def request_notification_permission():
    """Android 13+ runtime permission, requested only when the user asks for it."""
    if not is_android():
        return False
    try:
        from android.permissions import request_permissions
        request_permissions(["android.permission.POST_NOTIFICATIONS"])
        return True
    except Exception:
        return False


def start_keepalive_service():
    """Starts the foreground keep-alive service declared in buildozer.spec."""
    if not is_android():
        return False, "Not running on Android"
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        pkg = autoclass("org.kivy.android.PythonActivity").mActivity.getPackageName()
        Service = autoclass(pkg + ".ServiceNdxkeepalive")
        Service.start(PythonActivity.mActivity, "")
        return True, "Keep-alive service started"
    except Exception as exc:
        return False, "Could not start service: %s" % exc


def stop_keepalive_service():
    if not is_android():
        return False
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        pkg = PythonActivity.mActivity.getPackageName()
        autoclass(pkg + ".ServiceNdxkeepalive").stop(PythonActivity.mActivity)
        return True
    except Exception:
        return False
