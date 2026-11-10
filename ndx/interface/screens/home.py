from kivy.metrics import dp
from kivy.uix.image import Image
from kivy.uix.scrollview import ScrollView
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.progressbar import MDProgressBar

from ...utilities import human_size, human_duration
from ..theme import T
from ..widgets import ResponsiveGrid, card, chip, column, icon, label, primary_btn, row, text_btn
from .base import NDXScreen


def _section(title, icon_name, accent=False):
    c = card()
    head = row(size_hint_y=None, height=dp(32))
    head.add_widget(icon(icon_name, "accent", 22))
    head.add_widget(label(title, "Subtitle1", adaptive_height=False, valign="middle"))
    c.add_widget(head)
    return c


def _quality(q):
    return {"exact": "", "estimate": " [color=#D29922](estimate)[/color]",
            "unavailable": " [color=#8B949E](not exposed by Android)[/color]"}.get(q, "")


class HomeScreen(NDXScreen):
    key = "home"
    title = "NDX"
    refresh_interval = 3

    def actions(self):
        return [["refresh", lambda *_: self.rebuild()]]

    def render(self, body):
        core, sv = self.core, self.core.services
        grid = ResponsiveGrid(min_item_dp=320)
        body.add_widget(grid)

        # ---- recent projects (horizontal carousel)
        c = _section("Recent projects", "folder-clock-outline")
        recents = sv.get("projects").recent()
        if recents:
            sc = ScrollView(size_hint_y=None, height=dp(104), do_scroll_y=False, bar_width=0)
            strip = MDBoxLayout(orientation="horizontal", spacing=dp(10), adaptive_width=True, size_hint_y=1)
            for p in recents:
                pc = card(size_hint=(None, 1), width=dp(170), on_release=lambda *_, p=p: self.app.open_project(p))
                pc.add_widget(label(p.name, "Subtitle2", shorten=True, shorten_from="right"))
                lang = sv.get("languages").get(p.language)
                pc.add_widget(label(lang.name if lang else "Mixed", "Caption", "muted"))
                strip.add_widget(pc)
            sc.add_widget(strip)
            c.add_widget(sc)
        else:
            c.add_widget(Image(source=str(core.paths.assets / "images" / "empty_projects.png"),
                               size_hint_y=None, height=dp(120)))
            c.add_widget(label("No projects yet.", "Body2", "muted"))
            c.add_widget(primary_btn("New project", lambda: self.app.go("projects")))
        grid.add_widget(c)

        # ---- resources
        c = _section("Device resources", "chip")
        mon = sv.get("monitor")
        mem, cpu, st, bat, tmp = mon.memory(), mon.cpu_percent(), mon.storage(), mon.battery(), mon.temperature()
        pct = mem["percent"]["value"]
        c.add_widget(label("RAM: %s of %s%s" % (
            "%.0f%%" % pct if pct is not None else "n/a",
            human_size(mem["total"]["value"]) if mem["total"]["value"] else "n/a", _quality(mem["percent"]["quality"])), "Body2"))
        c.add_widget(MDProgressBar(value=pct or 0, color=T.c("accent"), size_hint_y=None, height=dp(4)))
        c.add_widget(label("CPU: %s%s" % ("%.0f%%" % cpu["value"] if cpu["value"] is not None else "n/a",
                                          _quality(cpu["quality"])), "Body2"))
        if st["total"]["value"]:
            used = 100.0 * st["used"]["value"] / st["total"]["value"]
            c.add_widget(label("Storage: %s used / %s total" % (human_size(st["used"]["value"]),
                                                              human_size(st["total"]["value"])), "Body2"))
            c.add_widget(MDProgressBar(value=used, color=T.c("accent"), size_hint_y=None, height=dp(4)))
        c.add_widget(label("Battery: %s   Temperature: %s%s" % (
            "%s%%" % bat["percent"]["value"] if bat["percent"]["value"] is not None else "n/a",
            "%.0f C" % tmp["value"] if tmp["value"] is not None else "n/a", _quality(tmp["quality"])), "Body2", "muted"))
        c.add_widget(label("GPU: not exposed to apps by Android", "Caption", "muted"))
        grid.add_widget(c)

        # ---- languages & compilers
        langs = sv.get("languages")
        c = _section("Installed languages", "code-tags")
        ready = [l for l in langs.all() if l.tools and langs.is_ready(l)]
        missing = [l for l in langs.all() if l.tools and not langs.is_ready(l)]
        flow = MDBoxLayout(orientation="horizontal", adaptive_height=True, spacing=dp(6))
        if ready:
            for l in ready[:4]:
                flow.add_widget(chip(l.name, "ok"))
            c.add_widget(flow)
            if len(ready) > 4:
                c.add_widget(label("+%d more" % (len(ready) - 4), "Caption", "muted"))
        else:
            c.add_widget(label("No toolchain found on the Android host.", "Body2", "muted"))
        c.add_widget(label("%d languages need a toolchain (use Environments)." % len(missing), "Caption", "muted"))
        grid.add_widget(c)

        c = _section("Installed compilers", "hammer-wrench")
        tools = {}
        for l in langs.all():
            for t, pth in langs.toolchain_status(l).items():
                if pth:
                    tools[t] = pth
        c.add_widget(label(", ".join(sorted(tools)) or "None found. Install a Linux environment, then a compiler inside it.",
                           "Body2", "text" if tools else "muted"))
        grid.add_widget(c)

        # ---- servers / linux / processes
        c = _section("Active servers", "server-network")
        run = sv.get("servers").running()
        if run:
            for r in run:
                c.add_widget(label("%s  [color=#8B949E]%s:%s[/color]" % (r.spec.name, r.spec.host, r.spec.port), "Body2"))
        else:
            c.add_widget(label("No server running.", "Body2", "muted"))
        c.add_widget(text_btn("Manage", lambda: self.app.go("servers")))
        grid.add_widget(c)

        c = _section("Linux environments", "layers-outline")
        envs = sv.get("linux").list()
        for e in envs:
            c.add_widget(label("%s (%s)" % (e["name"], e["id"]), "Body2"))
        if not envs:
            c.add_widget(label("None installed.", "Body2", "muted"))
        c.add_widget(label("PRoot: " + ("available" if sv.get("linux").backend.available() else "binary not found"),
                           "Caption", "ok" if sv.get("linux").backend.available() else "warn"))
        grid.add_widget(c)

        c = _section("Running processes", "cog-sync-outline")
        term = sv.get("terminal")
        live = [s for s in term.sessions.values() if s.alive()]
        c.add_widget(label("Terminal sessions: %d" % len(live), "Body2"))
        c.add_widget(label("Servers: %d   Background tasks: %d" % (len(run), len(core.tasks.active())), "Body2"))
        grid.add_widget(c)

        c = _section("Recent terminal activity", "console-line")
        for t in reversed(term.activity[-4:]):
            c.add_widget(label("• " + t, "Body2"))
        if not term.activity:
            c.add_widget(label("No sessions started yet.", "Body2", "muted"))
        grid.add_widget(c)

        c = _section("Notifications & diagnostics", "bell-outline")
        issues = [m for m in core.modules.all() if m.status in ("failed", "unavailable")]
        for m in issues:
            c.add_widget(label("[color=%s]%s[/color]: %s" % (T.hex("error" if m.status == "failed" else "warn"),
                                                           m.title, m.error or m.status), "Body2"))
        for _t, lvl, name, msg in [r for r in core.logs.recent(50) if r[1] in ("WARNING", "ERROR")][-3:]:
            c.add_widget(label(msg[:140], "Caption", "muted"))
        if not issues:
            c.add_widget(label("All modules healthy.", "Body2", "ok"))
        grid.add_widget(c)
