import os
import re

from kivy.metrics import dp
from kivy.uix.scrollview import ScrollView

from ...ai_runtime import ModelEntry
from ...core.errors import NDXError
from ...server_manager import ServerSpec
from ...server_manager.manager import KINDS, POLICIES
from ...utilities import human_duration, human_size
from ..theme import T
from ..widgets import (ResponsiveGrid, card, chip, column, confirm_dialog, form_dialog, icon, label,
                       message_dialog, primary_btn, row, text_btn, toast, icon_btn)
from .base import NDXScreen

RUNTIMES = ["builtin-static", "python", "flask", "fastapi", "node", "rust", "c", "custom"]


def _int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class ServersScreen(NDXScreen):
    key = "servers"
    title = "Servers"
    refresh_interval = 2

    def actions(self):
        return [["plus", lambda *_: self.edit_dialog(None)]]

    @property
    def sm(self):
        return self.core.services.get("servers")

    def render(self, body):
        c = card()
        c.add_widget(label("Resource manager", "Subtitle1"))
        res = self.core.services.get("resources")
        self.res_lbl = label("", "Body2")
        c.add_widget(self.res_lbl)
        c.add_widget(label("Budgets are enforced by NDX through admission control, request gates and (for "
                           "external processes) an address-space limit. Android may still terminate or throttle "
                           "processes: these are not hard guarantees.", "Caption", "muted"))
        r = row(size_hint_y=None, height=dp(44))
        r.add_widget(text_btn("Set global RAM budget", self.budget_dialog))
        c.add_widget(r)
        body.add_widget(c)
        self.live = column()
        body.add_widget(self.live)
        self.refresh()

    def refresh(self):
        if not hasattr(self, "live"):
            return
        res = self.core.services.get("resources")
        self.res_lbl.text = "RAM budget: %d MB total, %d MB reserved by %d server(s)" % (
            res.ram_budget_mb, res.reserved_mb(), len(res.allocations()))
        self.live.clear_widgets()
        self.live.add_widget(label("Servers", "H6"))
        if not self.sm.runtimes:
            self.live.add_widget(label("No servers configured. Tap + to add one (the built-in static server "
                                       "needs no external tools).", "Body2", "muted"))
        grid = ResponsiveGrid(min_item_dp=340)
        self.live.add_widget(grid)
        for sid, rt in self.sm.runtimes.items():
            grid.add_widget(self._server_card(sid, rt))
        self._ai_section()

    def _server_card(self, sid, rt):
        sp, st = rt.spec, self.sm.stats(sid)
        c = card()
        head = row(size_hint_y=None, height=dp(34))
        head.add_widget(icon("server", "accent", 22))
        head.add_widget(label("%s   [color=%s]%s[/color]" % (sp.name, T.hex(T.status_color(rt.state)), rt.state),
                              "Subtitle2", adaptive_height=False, valign="middle"))
        c.add_widget(head)
        c.add_widget(label("%s · %s · %s:%d%s" % (sp.kind, sp.runtime, sp.host, sp.port,
                                                  "  [color=%s]EXTERNAL[/color]" % T.hex("warn") if sp.allow_external else ""),
                           "Caption", "muted"))
        up = human_duration(st["uptime"]) if st["uptime"] is not None else "-"
        cpu = "%.0f%%" % st["cpu"] if st["cpu"] is not None else "n/a"
        rss = human_size(st["rss"]) if st["rss"] else "n/a"
        req = "%s (%s rejected)" % (st["requests"], st["rejected"]) if st["requests"] is not None else "n/a (external process)"
        c.add_widget(label("Uptime %s   CPU %s   RAM %s%s" % (up, cpu, rss, " [color=#8B949E](app-wide)[/color]" if st.get("rss_scope") else ""), "Body2"))
        c.add_widget(label("Requests: %s   GPU: not exposed" % req, "Caption", "muted"))
        if rt.error:
            c.add_widget(label(rt.error, "Caption", "error"))
        a = row(size_hint_y=None, height=dp(40))
        if rt.state == "running":
            a.add_widget(primary_btn("Stop", lambda sid=sid: self.act(sid, "stop")))
            a.add_widget(text_btn("Restart", lambda sid=sid: self.act(sid, "restart")))
        else:
            a.add_widget(primary_btn("Start", lambda sid=sid: self.act(sid, "start")))
        a.add_widget(icon_btn("text-box-outline", lambda sid=sid: self.logs(sid)))
        a.add_widget(icon_btn("cog-outline", lambda sid=sid: self.edit_dialog(sid)))
        a.add_widget(icon_btn("delete-outline", lambda sid=sid: self.delete(sid), "error"))
        c.add_widget(a)
        return c

    # ---- actions
    def act(self, sid, what):
        def work():
            if what == "start":
                self.sm.start_server(sid)
            elif what == "stop":
                self.sm.stop_server(sid)
            else:
                self.sm.restart(sid)
        self.core.tasks.submit(work, name="%s %s" % (what, sid), on_done=lambda _: self.refresh(),
                               on_error=lambda e: (message_dialog("Server error", str(e)), self.refresh()))

    def logs(self, sid):
        lines = list(self.sm.runtimes[sid].logs)[-40:]
        message_dialog("Logs: " + self.sm.runtimes[sid].spec.name, "\n".join(lines) or "(no output yet)")

    def delete(self, sid):
        confirm_dialog("Delete server?", "Remove this server configuration (it is stopped first).",
                       lambda: (self.sm.remove(sid), self.refresh()), "Delete", danger=True)

    def budget_dialog(self):
        res = self.core.services.get("resources")
        form_dialog("Global RAM budget (MB)", [("mb", "MB", str(res.ram_budget_mb))], self._set_budget, "Save")

    def _set_budget(self, v):
        mb = max(64, _int(v["mb"], 1024))
        self.core.services.get("resources").ram_budget_mb = mb
        self.core.config.set("servers.global_ram_budget_mb", mb)
        self.refresh()

    def edit_dialog(self, sid):
        sp = self.sm.runtimes[sid].spec if sid else ServerSpec(id="", name="", cwd=str(self.core.paths.projects))
        form_dialog("Server" if sid else "New server", [
            ("name", "Name", sp.name), ("port", "Port", str(sp.port)), ("host", "Host (127.0.0.1 = local only)", sp.host),
            ("cwd", "Folder (served / working dir)", sp.cwd), ("command", "Command (non-builtin runtimes)", " ".join(sp.command)),
            ("ram_mb", "RAM budget MB", str(sp.ram_mb)), ("threads", "CPU threads", str(sp.threads)),
            ("max_concurrency", "Max concurrent requests", str(sp.max_concurrency)),
            ("queue_limit", "Queue limit", str(sp.queue_limit)), ("timeout_s", "Queue timeout (s)", str(sp.timeout_s))],
            lambda v, sid=sid, sp=sp: self._save(sid, sp, v), "Save",
            extra=[("kind", KINDS, sp.kind), ("runtime", RUNTIMES, sp.runtime), ("policy", POLICIES, sp.policy),
                   ("external", ["local only", "allow external"], "allow external" if sp.allow_external else "local only")])

    def _save(self, sid, sp, v):
        import shlex
        import uuid
        try:
            spec = ServerSpec(id=sid or uuid.uuid4().hex[:8], name=v["name"] or "server", kind=v["kind"],
                              runtime=v["runtime"], command=shlex.split(v["command"]) if v["command"].strip() else [],
                              cwd=v["cwd"], host=v["host"].strip() or "127.0.0.1", port=_int(v["port"], 8080),
                              ram_mb=_int(v["ram_mb"], 256), threads=_int(v["threads"], 2),
                              max_concurrency=_int(v["max_concurrency"], 8), queue_limit=_int(v["queue_limit"], 16),
                              timeout_s=float(v["timeout_s"] or 10), policy=v["policy"],
                              allow_external=v["external"] == "allow external", env=sp.env)
            (self.sm.update if sid else self.sm.add)(spec)
        except (NDXError, ValueError) as e:
            message_dialog("Invalid server configuration", str(e))
            return
        self.refresh()

    # ---- AI runtime
    def _ai_section(self):
        ai = self.core.services.get("ai")
        self.live.add_widget(label("AI runtime", "H6"))
        c = card()
        eng = ai.engine_path()
        c.add_widget(label("llama.cpp server: " + (eng if eng else "not installed (place llama-server in %s). "
                                                    "ONNX Runtime is not integrated." % self.core.paths.bin),
                           "Body2", "ok" if eng else "warn"))
        c.add_widget(label("Android uses shared CPU/GPU memory: RAM figures are estimates; GPU memory is not shown.",
                           "Caption", "muted"))
        c.add_widget(text_btn("Register model", self.model_dialog))
        self.live.add_widget(c)
        grid = ResponsiveGrid(min_item_dp=340)
        self.live.add_widget(grid)
        for m in ai.models.values():
            cd = card()
            state = ai.state(m.id)
            cd.add_widget(label("%s   [color=%s]%s[/color]" % (m.name, T.hex(T.status_color(state)), state), "Subtitle2"))
            try:
                est = "%d MB (estimate)" % ai.estimate_ram_mb(m)
            except OSError:
                est = "file missing"
            cd.add_widget(label("%s · ctx %d · %d threads · GPU layers %d · est. RAM %s" % (
                m.format, m.ctx, m.threads, m.gpu_layers, est), "Caption", "muted"))
            cd.add_widget(label("Endpoint: %s" % ai.endpoint(m.id), "Caption", "muted"))
            a = row(size_hint_y=None, height=dp(40))
            if state == "running":
                a.add_widget(primary_btn("Unload", lambda m=m: ai.unload(m.id)))
            else:
                a.add_widget(primary_btn("Load", lambda m=m: self.load_model(m)))
            a.add_widget(icon_btn("delete-outline", lambda m=m: (ai.unregister(m.id), self.refresh()), "error"))
            cd.add_widget(a)
            grid.add_widget(cd)

    def load_model(self, m):
        ai = self.core.services.get("ai")
        self.core.tasks.submit(ai.load, m.id, name="load " + m.name, on_done=lambda _: self.refresh(),
                               on_error=lambda e: message_dialog("Cannot load model", str(e)))

    def model_dialog(self):
        form_dialog("Register model", [("name", "Name", ""), ("path", "Path to .gguf file", ""),
                                       ("ctx", "Context size", "2048"), ("threads", "CPU threads", "4"),
                                       ("gpu", "GPU layers (0 = CPU only)", "0"), ("port", "Port", "8081")],
                    self._reg_model, "Register")

    def _reg_model(self, v):
        try:
            self.core.services.get("ai").register(v["name"] or os.path.basename(v["path"]), v["path"],
                                                  ctx=_int(v["ctx"], 2048), threads=_int(v["threads"], 4),
                                                  gpu_layers=_int(v["gpu"], 0), port=_int(v["port"], 8081))
        except NDXError as e:
            message_dialog("Cannot register", str(e))
        self.refresh()
