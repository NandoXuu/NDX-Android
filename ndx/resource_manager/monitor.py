"""System/process monitor. Every value carries its quality:
exact (read from OS), estimate (derived), unavailable (not exposed)."""
import os
import time

try:
    import psutil
except Exception:  # psutil may be missing on a given Android build
    psutil = None


def _m(value, quality="exact", unit=""):
    return {"value": value, "quality": quality, "unit": unit}


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read()
    except OSError:
        return None


class ResourceMonitor:
    def __init__(self, storage_path="/"):
        self.storage_path = str(storage_path)
        self._last_cpu = None

    def memory(self):
        if psutil:
            try:
                vm = psutil.virtual_memory()
                return {"total": _m(vm.total, unit="B"), "available": _m(vm.available, unit="B"),
                        "percent": _m(vm.percent, unit="%")}
            except Exception:
                pass
        txt = _read("/proc/meminfo")
        if txt:
            info = {}
            for line in txt.splitlines():
                k, _, v = line.partition(":")
                parts = v.split()
                if parts:
                    info[k] = int(parts[0]) * 1024
            if "MemTotal" in info and "MemAvailable" in info:
                tot, av = info["MemTotal"], info["MemAvailable"]
                return {"total": _m(tot, unit="B"), "available": _m(av, unit="B"),
                        "percent": _m(round(100.0 * (tot - av) / tot, 1), unit="%")}
        return {"total": _m(None, "unavailable"), "available": _m(None, "unavailable"),
                "percent": _m(None, "unavailable")}

    def cpu_percent(self):
        if psutil:
            try:
                return _m(psutil.cpu_percent(interval=None), unit="%")
            except Exception:
                pass
        txt = _read("/proc/stat")
        if txt:
            vals = [int(x) for x in txt.splitlines()[0].split()[1:8]]
            idle, total = vals[3] + vals[4], sum(vals)
            prev, self._last_cpu = self._last_cpu, (idle, total)
            if prev and total != prev[1]:
                return _m(round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1), unit="%")
            return _m(None, "unavailable")
        return _m(None, "unavailable")

    def storage(self):
        try:
            st = os.statvfs(self.storage_path)
            total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
            return {"total": _m(total, unit="B"), "free": _m(free, unit="B"),
                    "used": _m(total - free, unit="B")}
        except OSError:
            return {"total": _m(None, "unavailable"), "free": _m(None, "unavailable"),
                    "used": _m(None, "unavailable")}

    def battery(self):
        if psutil and hasattr(psutil, "sensors_battery"):
            try:
                b = psutil.sensors_battery()
                if b:
                    return {"percent": _m(round(b.percent, 1), unit="%"), "charging": _m(b.power_plugged)}
            except Exception:
                pass
        cap = _read("/sys/class/power_supply/battery/capacity")
        if cap and cap.strip().isdigit():
            return {"percent": _m(int(cap.strip()), unit="%"), "charging": _m(None, "unavailable")}
        return {"percent": _m(None, "unavailable"), "charging": _m(None, "unavailable")}

    def temperature(self):
        t = _read("/sys/class/thermal/thermal_zone0/temp")
        if t and t.strip().lstrip("-").isdigit():
            v = int(t.strip())
            return _m(v / 1000.0 if abs(v) > 1000 else float(v), unit="C")
        return _m(None, "unavailable")

    def network(self):
        if psutil:
            try:
                n = psutil.net_io_counters()
                return {"sent": _m(n.bytes_sent, unit="B"), "recv": _m(n.bytes_recv, unit="B")}
            except Exception:
                pass
        return {"sent": _m(None, "unavailable"), "recv": _m(None, "unavailable")}

    def gpu(self):
        # No portable GPU telemetry from an ordinary Android app.
        return {"info": _m(None, "unavailable")}

    def process(self, pid):
        if psutil:
            try:
                p = psutil.Process(pid)
                with p.oneshot():
                    return {"cpu": _m(p.cpu_percent(interval=None), unit="%"),
                            "rss": _m(p.memory_info().rss, unit="B"),
                            "threads": _m(p.num_threads()),
                            "uptime": _m(time.time() - p.create_time(), unit="s")}
            except Exception:
                pass
        txt = _read("/proc/%d/status" % pid)
        if txt:
            rss = None
            for line in txt.splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
            return {"cpu": _m(None, "unavailable"), "rss": _m(rss, "exact" if rss else "unavailable", "B"),
                    "threads": _m(None, "unavailable"), "uptime": _m(None, "unavailable")}
        return None

    def snapshot(self):
        return {"memory": self.memory(), "cpu": self.cpu_percent(), "storage": self.storage(),
                "battery": self.battery(), "temperature": self.temperature(),
                "network": self.network(), "gpu": self.gpu()}
