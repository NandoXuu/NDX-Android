from ndx.plugin_system import PluginBase


class Plugin(PluginBase):
    def on_load(self, ctx):
        self.count = 0
        ctx.register_terminal_command("hello", self.hello)
        ctx.subscribe("project.created", self.on_project)

    def on_project(self, event):
        self.count += 1

    def hello(self, args, out):
        out("Hello %s! Projects created this session: %d\n" % (" ".join(args) or "world", self.count))
        return 0
