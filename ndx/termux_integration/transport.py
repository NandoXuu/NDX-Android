"""Transports for the Termux integration.

NDX talks to Termux ONLY through Termux's documented RUN_COMMAND intent (service
com.termux.app.RunCommandService, permission com.termux.permission.RUN_COMMAND). It never reads or writes
Termux's private storage; the Termux paths below are plain strings handed to Termux, which is the one that
opens them.

AndroidIntentTransport needs PyJNIus + a device and has NOT been tested on Android (see docs/TERMUX.md).
Tests use a fake transport."""
import threading
import uuid

from ..core.paths import is_android

TERMUX_PACKAGE = "com.termux"
TERMUX_API_PACKAGE = "com.termux.api"
RUN_COMMAND_PERMISSION = "com.termux.permission.RUN_COMMAND"
RUN_COMMAND_ACTION = "com.termux.RUN_COMMAND"
RUN_COMMAND_SERVICE = "com.termux.app.RunCommandService"
FLAG_UPDATE_CURRENT, FLAG_MUTABLE = 0x08000000, 0x02000000      # PendingIntent flags (Termux must fill extras)


class TermuxTransport:
    """-> True/False/None (None = cannot be determined, e.g. Android package visibility)."""

    def package_installed(self, package=TERMUX_PACKAGE):
        return None

    def permission_granted(self):
        return None

    def request_permission(self):
        return False

    def execute(self, path, args, cwd=None, timeout=60):
        """Run `path args` in Termux (background, no shell) and wait for the result.
        -> dict(stdout, stderr, exit_code, err, errmsg, timed_out)."""
        raise NotImplementedError


class UnavailableTransport(TermuxTransport):
    reason = "Termux integration needs Android"

    def package_installed(self, package=TERMUX_PACKAGE):
        return False

    def permission_granted(self):
        return False

    def execute(self, *a, **k):
        from ..core.errors import ToolUnavailable
        raise ToolUnavailable(self.reason)


class AndroidIntentTransport(TermuxTransport):
    def __init__(self):
        from jnius import autoclass          # fails off-Android: the caller falls back to UnavailableTransport
        self.J = autoclass
        self.activity = autoclass("org.kivy.android.PythonActivity").mActivity

    def package_installed(self, package=TERMUX_PACKAGE):
        try:
            self.activity.getPackageManager().getPackageInfo(package, 0)
            return True
        except Exception as e:
            # NameNotFoundException is also what Android 11+ package-visibility filtering produces
            return None if "NameNotFound" in str(e) and not self._queries_declared() else False

    @staticmethod
    def _queries_declared():
        return False          # NDX cannot read its own manifest <queries> reliably: treat "not found" as "unknown"

    def permission_granted(self):
        try:
            return self.activity.checkSelfPermission(RUN_COMMAND_PERMISSION) == 0
        except Exception:
            return None

    def request_permission(self):
        try:
            from android.permissions import request_permissions
            request_permissions([RUN_COMMAND_PERMISSION])
            return True
        except Exception:
            return False

    def execute(self, path, args, cwd=None, timeout=60):
        from android.broadcast import BroadcastReceiver       # python-for-android
        J = self.J
        Intent, PendingIntent = J("android.content.Intent"), J("android.app.PendingIntent")
        action = "org.ndx.TERMUX_RESULT." + uuid.uuid4().hex
        box, done = {}, threading.Event()

        def on_result(context, intent):
            b = intent.getBundleExtra("result")
            if b is not None:
                box.update(stdout=b.getString("stdout") or "", stderr=b.getString("stderr") or "",
                           exit_code=b.getInt("exitCode", -1), err=b.getInt("err", -1), errmsg=b.getString("errmsg") or "")
            done.set()

        receiver = BroadcastReceiver(on_result, actions=[action])
        receiver.start()
        try:
            cb = Intent(action)
            cb.setPackage(self.activity.getPackageName())
            pending = PendingIntent.getBroadcast(self.activity, abs(hash(action)) % (2 ** 30), cb,
                                                 FLAG_UPDATE_CURRENT | FLAG_MUTABLE)
            it = Intent()
            it.setClassName(TERMUX_PACKAGE, RUN_COMMAND_SERVICE)
            it.setAction(RUN_COMMAND_ACTION)
            it.putExtra("com.termux.RUN_COMMAND_PATH", path)
            it.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", [str(a) for a in args])
            if cwd:
                it.putExtra("com.termux.RUN_COMMAND_WORKDIR", cwd)
            it.putExtra("com.termux.RUN_COMMAND_BACKGROUND", True)
            it.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pending)
            try:
                self.activity.startService(it)
            except Exception as e:
                return {"stdout": "", "stderr": "", "exit_code": None, "err": -2, "errmsg": "startService failed: %s" % e,
                        "timed_out": False}
            ok = done.wait(timeout)
        finally:
            receiver.stop()
        if not ok:
            return {"stdout": "", "stderr": "", "exit_code": None, "err": -3, "timed_out": True,
                    "errmsg": "no result from Termux within %ss (is allow-external-apps enabled?)" % timeout}
        box.setdefault("timed_out", False)
        return box


def default_transport():
    if is_android():
        try:
            return AndroidIntentTransport()
        except Exception:
            pass
    return UnavailableTransport()
