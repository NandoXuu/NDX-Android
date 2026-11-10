"""NDX Application Framework: toolkit-independent UI description (no Kivy)."""
import itertools

_ids = itertools.count(1)


class Node:
    def __init__(self, kind, children=None, **props):
        self.id = "n%d" % next(_ids)
        self.kind, self.props, self.children = kind, props, list(children or [])
        self.handlers = {}

    def on(self, event, fn):
        self.handlers[event] = fn
        return self

    def add(self, child):
        self.children.append(child)
        return child

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "props": self.props,
                "events": sorted(self.handlers), "children": [c.to_dict() for c in self.children]}

    def find(self, nid):
        if self.id == nid:
            return self
        for c in self.children:
            r = c.find(nid)
            if r:
                return r
        return None


def Window(title, *children, **p): return Node("window", children, title=title, **p)
def VBox(*children, **p): return Node("vbox", children, **p)
def HBox(*children, **p): return Node("hbox", children, **p)
def Label(text, **p): return Node("label", text=text, **p)
def Button(text, on_click=None, **p):
    n = Node("button", text=text, **p)
    return n.on("click", on_click) if on_click else n
def TextInput(value="", placeholder="", **p): return Node("textinput", value=value, placeholder=placeholder, **p)
def Table(columns, rows, **p): return Node("table", columns=list(columns), rows=[list(r) for r in rows], **p)
def Menu(items, **p): return Node("menu", items=list(items), **p)
def Dialog(title, message, buttons=("OK",), **p): return Node("dialog", title=title, message=message, buttons=list(buttons), **p)
def Form(fields, **p): return Node("form", fields=list(fields), **p)
