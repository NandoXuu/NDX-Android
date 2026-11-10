// Node.js client for the NDX IPC (no dependencies). Authenticates first with a session token.
//   const { NdxApp } = require("./ndxapp");
//   const app = await NdxApp.connect();            // NDX_IPC_PORT / NDX_IPC_TOKEN from the environment
//   const r = await app.toolsRun("git", ["status"]);
const net = require("net");

class NdxApp {
  constructor(sock, timeoutMs) {
    this.sock = sock; this.timeoutMs = timeoutMs; this.id = 0; this.pending = new Map(); this.buf = "";
    this.events = []; this.onEvent = null; this.scopes = [];
    sock.setEncoding("utf8");
    sock.on("data", (d) => this._data(d));
    sock.on("close", () => this._failAll(new Error("connection closed by NDX")));
    sock.on("error", (e) => this._failAll(e));
  }
  static connect({ port, host = "127.0.0.1", token, timeoutMs = 10000 } = {}) {
    port = port || process.env.NDX_IPC_PORT; token = token || process.env.NDX_IPC_TOKEN;
    if (!port) return Promise.reject(new Error("no IPC port: pass port or set NDX_IPC_PORT"));
    if (!token) return Promise.reject(new Error("no IPC token: pass token or set NDX_IPC_TOKEN"));
    return new Promise((resolve, reject) => {
      const sock = net.connect({ port: Number(port), host }, async () => {
        const app = new NdxApp(sock, timeoutMs);
        try { app.scopes = (await app.call("auth", { token })).scopes || []; resolve(app); } catch (e) { sock.destroy(); reject(e); }
      });
      sock.once("error", reject);
    });
  }
  _data(d) {
    this.buf += d; let i;
    while ((i = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, i); this.buf = this.buf.slice(i + 1);
      if (!line.trim()) continue;
      let m; try { m = JSON.parse(line); } catch (e) { continue; }
      if (m.event) { if (this.onEvent) this.onEvent(m); else this.events.push(m); continue; }
      const p = this.pending.get(m.id); if (!p) continue;
      this.pending.delete(m.id); clearTimeout(p.timer);
      if (m.error !== undefined) { const e = new Error(String(m.error)); e.code = m.code; p.reject(e); } else p.resolve(m.result);
    }
  }
  _failAll(err) { for (const [, p] of this.pending) { clearTimeout(p.timer); p.reject(err); } this.pending.clear(); }
  call(method, params = {}, timeoutMs = this.timeoutMs) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error("timed out waiting for NDX")); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.sock.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }
  window(title) { return this.call("window.create", { title }); }
  add(parent, kind, props = {}) { return this.call("node.add", { parent, kind, props }); }
  set(node, props) { return this.call("node.set", { node, props }); }
  tree() { return this.call("ui.tree"); }
  kvGet(key) { return this.call("services.kv_get", { key }); }
  kvSet(key, value) { return this.call("services.kv_set", { key, value }); }
  // args is an ARRAY: never build a shell string from untrusted input
  toolsRun(tool, args = [], opts = {}) {
    return this.call("tools.run", { tool, args, environment: "host", ...opts }, ((opts.timeout || 600) + 15) * 1000);
  }
  toolsResolve(tool, environment = "host") { return this.call("tools.resolve", { tool, environment }); }
  toolsList() { return this.call("tools.list"); }
  close() { this.sock.destroy(); }
}
module.exports = { NdxApp };
