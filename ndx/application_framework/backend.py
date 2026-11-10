"""Backend abstraction. A backend turns a Node tree into something visible.
Implemented: HeadlessBackend (text/JSON, used for tests and IPC clients).
Native Android UI, GTK/Qt and custom-renderer backends are future work."""


class Backend:
    name = "abstract"

    def show(self, window):
        raise NotImplementedError

    def update(self, node):
        raise NotImplementedError

    def dispatch(self, node_id, event, payload=None):
        raise NotImplementedError


class HeadlessBackend(Backend):
    name = "headless"

    def __init__(self):
        self.window = None
        self.log = []

    def show(self, window):
        self.window = window
        self.log.append(("show", window.id))

    def update(self, node):
        self.log.append(("update", node.id))

    def dispatch(self, node_id, event, payload=None):
        node = self.window.find(node_id) if self.window else None
        if not node:
            raise KeyError("unknown node " + node_id)
        fn = node.handlers.get(event)
        return fn(payload) if fn else None

    def render_text(self, node=None, depth=0):
        node = node or self.window
        label = node.props.get("text") or node.props.get("title") or node.props.get("value") or ""
        lines = ["%s%s %s" % ("  " * depth, node.kind, label)]
        for c in node.children:
            lines.append(self.render_text(c, depth + 1))
        return "\n".join(lines)
