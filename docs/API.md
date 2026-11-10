# Internal API notes

## Events (name -> payload)
`core.started/stopping`, `config.changed{key,value}`, `task.started/finished{id,name,state}`,
`project.created|opened|saved|renamed|deleted{id,name,path}`, `run.output{label,text}`, `run.finished{label,code,denied?}`,
`terminal.started{id,title}`, `server.changed{id,state}`, `plugins.changed{id}`, `linux.changed{id}`,
`resources.rejected{owner,mb}`, `ui.consent.request{title,message,details,respond(bool)}`.
Plugins can only subscribe/publish if granted `event.subscribe` / `event.publish`; their events are namespaced `plugin.<id>.*`.

## Application Framework IPC (JSON lines over loopback TCP)
Request `{"id":1,"method":"window.create","params":{"title":"Demo"}}` -> `{"id":1,"result":"n1"}` or `{"id":1,"error":"..."}`.
Methods: `window.create{title}`, `node.add{parent,kind,props}`, `node.set{node,props}`, `node.subscribe{node,event}`,
`ui.tree{}`, `services.kv_set{key,value}`, `services.kv_get{key}`.
Server -> client events: `{"event":"click","node":"n3","payload":...}`.
Node kinds: window, vbox, hbox, label, button, textinput, table, menu, dialog, form.
Bindings: `bindings/ndxapp.py` (tested), `bindings/ndxapp.js` (Node.js; written to the protocol, untested).

## ndx-plugin.toml
See PLUGINS.md.
