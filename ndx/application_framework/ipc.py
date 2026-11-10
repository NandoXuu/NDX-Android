"""Authenticated JSON-lines IPC so non-Python languages can drive the Application Framework and call NDX tools.

Transport : TCP on 127.0.0.1 only (a non-loopback host is refused unless allow_remote=True is passed explicitly).
Framing   : one JSON object per line, at most `max_message_bytes` per line.
Handshake : the FIRST request on every connection must be
              {"id": 1, "method": "auth", "params": {"token": "<token>"}}
            Tokens are random (secrets.token_urlsafe(32)), issued in-process with IpcServer.issue_token(), carry
            scopes and an expiry, and can be revoked. Scripts get theirs from the NDX_IPC_TOKEN / NDX_IPC_PORT
            environment variables set by the launcher (see docs/IPC.md).
Request   : {"id": 2, "method": "window.create", "params": {...}}
Response  : {"id": 2, "result": ...}  |  {"id": 2, "error": "<message>", "code": "<code>"}
Events    : {"event": "click", "node": "n3", "payload": ...}   (server -> client, subscribed nodes only)
Methods (scope):
  auth{token}
  ui   : window.create{title}, node.add{parent,kind,props}, node.set{node,props}, node.subscribe{node,event}, ui.tree{}
  kv   : services.kv_get{key}, services.kv_set{key,value}
  tools: tools.run{tool,args,environment,cwd,env,timeout,input}, tools.resolve{tool,environment}, tools.list{}
  admin: server.status{}, server.shutdown{}
Error codes: auth_required, auth_failed, forbidden, expired, revoked, rate_limited, too_large, bad_request,
             unknown_method, unavailable, busy, denied, invalid, failed, internal."""
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import socket
import socketserver
import threading
import time

from ..core.errors import NDXError, PermissionDenied, ValidationError
from .backend import HeadlessBackend
from .widgets import Node, Window

log = logging.getLogger("ndx.ipc")

SCOPES = ("ui", "kv", "tools", "admin")
MAX_TOOL_TIMEOUT = 600.0
MAX_TOOL_OUTPUT = 1024 * 1024
MAX_KV_VALUE = 64 * 1024
MAX_AUTH_FAILURES = 3

_NODE_ID = re.compile(r"^n\d{1,12}$")
_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_EVENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,47}$")
_KV_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TOOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,62}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")


class IpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


# ----------------------------------------------------------------------------- helpers
def is_loopback_addr(addr):
    """True for 127.0.0.0/8, ::1 and IPv4-mapped loopback. Anything unparsable is not loopback."""
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def is_loopback_host(host):
    if host in ("localhost", "ip6-localhost"):
        return True
    return is_loopback_addr(host)


class TokenBucket:
    def __init__(self, burst, per_second):
        self.burst, self.rate = float(burst), float(per_second)
        self.tokens, self.t = float(burst), time.monotonic()
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).digest()


def _str(p, key, maxlen=256, required=True, pattern=None):
    v = p.get(key)
    if v is None:
        if required:
            raise IpcError("bad_request", "missing parameter: %s" % key)
        return None
    if not isinstance(v, str) or len(v) > maxlen or (required and not v):
        raise IpcError("bad_request", "invalid parameter: %s" % key)
    if pattern is not None and not pattern.match(v):
        raise IpcError("bad_request", "invalid parameter: %s" % key)
    return v


def _check_json_size(value, limit, name):
    try:
        raw = json.dumps(value)
    except (TypeError, ValueError):
        raise IpcError("bad_request", "%s must be JSON-serializable" % name)
    if len(raw) > limit:
        raise IpcError("too_large", "%s is too large" % name)


def _cap(text, limit=MAX_TOOL_OUTPUT):
    text = text or ""
    if len(text) > limit:
        return text[:limit], True
    return text, False


class _Session:
    def __init__(self, label, scopes, expires):
        self.label, self.scopes, self.expires = label, frozenset(scopes), expires
        self.created = time.time()
        self.connections = 0
        self.revoked = False


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.wlock = threading.Lock()
        self.closed = False
        self.session = None
        self.auth_failures = 0
        self.bucket = TokenBucket(*self.server.app.rate_limit)
        self.last_seen = time.monotonic()
        self.counted = False

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.wlock:
            if self.closed:
                return False
            try:
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (OSError, ValueError):
                self.closed = True
                return False

    def close(self):
        self.closed = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def handle(self):
        app = self.server.app
        if not app._peer_ok(self.client_address[0]):
            log.warning("ipc: refused non-loopback peer %s", self.client_address[0])
            return
        if not app._register(self):
            self.send({"id": None, "error": "server busy", "code": "busy"})
            return
        self.counted = True
        self.connection.settimeout(app.idle_timeout)
        if not app.require_auth:
            self.session = app._open_session_anonymous()
        try:
            while not self.closed and not app._stopping:
                try:
                    raw = self.rfile.readline(app.max_message_bytes + 1)
                except (socket.timeout, OSError, ValueError):
                    break
                if not raw:
                    break
                if len(raw) > app.max_message_bytes:
                    self.send({"id": None, "error": "message too large", "code": "too_large"})
                    break
                if not self._one(raw):
                    break
        finally:
            app._unregister(self)

    # returns False to drop the connection
    def _one(self, raw):
        app = self.server.app
        rid = None
        try:
            try:
                req = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise IpcError("bad_request", "malformed JSON")
            if not isinstance(req, dict):
                raise IpcError("bad_request", "request must be an object")
            rid = req.get("id")
            if not isinstance(rid, (int, str, type(None))) or (isinstance(rid, str) and len(rid) > 64):
                rid = None
                raise IpcError("bad_request", "invalid id")
            method, params = req.get("method"), req.get("params", {})
            if not isinstance(method, str) or len(method) > 64 or not isinstance(params, dict):
                raise IpcError("bad_request", "invalid method or params")
            if not self.bucket.take() or not app._global_bucket.take():
                raise IpcError("rate_limited", "too many requests")
            if method == "auth":
                return self._auth(rid, params)
            if self.session is None:
                self.auth_failures += 1
                self.send({"id": rid, "error": "authentication required", "code": "auth_required"})
                return self.auth_failures < MAX_AUTH_FAILURES
            app._check_session(self)
            self.last_seen = time.monotonic()
            res = app._dispatch(method, params, self)
            self.send({"id": rid, "result": res})
            return True
        except IpcError as e:
            self.send({"id": rid, "error": e.message, "code": e.code})
            return e.code not in ("expired", "revoked")
        except PermissionDenied as e:
            self.send({"id": rid, "error": str(e) or "denied", "code": "denied"})
        except ValidationError as e:
            self.send({"id": rid, "error": str(e) or "invalid", "code": "invalid"})
        except NDXError as e:
            self.send({"id": rid, "error": str(e) or "failed", "code": "failed"})
        except Exception:
            log.exception("ipc: internal error handling a request")
            self.send({"id": rid, "error": "internal error", "code": "internal"})
        return True

    def _auth(self, rid, params):
        app = self.server.app
        token = params.get("token")
        sess = app._lookup(token) if isinstance(token, str) and len(token) <= 256 else None
        if sess is None:
            self.auth_failures += 1
            app._audit("ipc.auth_failed", peer=self.client_address[0])
            time.sleep(min(0.05 * self.auth_failures, 0.3))
            self.send({"id": rid, "error": "authentication failed", "code": "auth_failed"})
            return self.auth_failures < MAX_AUTH_FAILURES
        self.session = sess
        sess.connections += 1
        app._audit("ipc.auth_ok", label=sess.label)
        self.send({"id": rid, "result": {"ok": True, "scopes": sorted(sess.scopes)}})
        return True


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16


class IpcServer:
    def __init__(self, backend=None, services=None, host="127.0.0.1", port=0, allow_remote=False,
                 require_auth=True, core=None, max_message_bytes=65536, rate_limit=(50, 25.0),
                 session_ttl=3600.0, idle_timeout=300.0, max_clients=16, audit=None):
        if not is_loopback_host(host):
            if not allow_remote:
                raise ValueError("refusing to bind the IPC server to %r: only loopback is allowed "
                                 "(pass allow_remote=True to expose it deliberately)" % host)
            log.warning("ipc: binding to NON-LOOPBACK host %s. Anything that can reach the port and holds a "
                        "token can control NDX.", host)
        self.allow_remote = allow_remote
        self.backend = backend or HeadlessBackend()
        self.core = core
        if services is None and core is not None:
            try:
                from .services import PlatformServices
                services = PlatformServices(core.paths.data / "app_services")
            except Exception:
                log.exception("ipc: could not create platform services")
        self.services = services
        self.require_auth = require_auth
        if not require_auth:
            log.warning("ipc: authentication is DISABLED (require_auth=False). Use only for in-process tests.")
        self.max_message_bytes = int(max_message_bytes)
        self.rate_limit = (float(rate_limit[0]), float(rate_limit[1]))
        self._global_bucket = TokenBucket(self.rate_limit[0] * 4, self.rate_limit[1] * 4)
        self.session_ttl, self.idle_timeout = float(session_ttl), float(idle_timeout)
        self.max_clients = int(max_clients)
        self._audit_fn = audit
        self._tokens = {}                 # sha256(token) -> _Session
        self._tlock = threading.Lock()
        self._clients = set()
        self._clock = threading.Lock()
        self._threads = []
        self._stopping = False
        self._started = None
        self._srv = _Server((host, port), _Handler)
        self._srv.app = self
        self.host = host
        self.port = self._srv.server_address[1]

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        t = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True,
                             name="ndx-ipc")
        t.start()
        self._threads.append(t)
        self._started = time.time()
        return self

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        try:
            if self._started:
                self._srv.shutdown()
        except Exception:
            pass
        try:
            self._srv.server_close()
        except OSError:
            pass
        with self._clock:
            clients = list(self._clients)
        for c in clients:
            c.close()
        for t in self._threads:
            t.join(2.0)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with self._clock:
                if not self._clients:
                    break
            time.sleep(0.02)
        with self._tlock:
            self._tokens.clear()
        self._audit("ipc.stopped")

    # ------------------------------------------------------------------ tokens
    def issue_token(self, scopes=("ui", "kv"), ttl=None, label=""):
        bad = [s for s in scopes if s not in SCOPES]
        if bad:
            raise ValueError("unknown scopes: %s" % ", ".join(map(str, bad)))
        token = secrets.token_urlsafe(32)
        ttl = self.session_ttl if ttl is None else float(ttl)
        sess = _Session(label or "session-" + secrets.token_hex(3), scopes, time.time() + ttl)
        with self._tlock:
            self._tokens[_digest(token)] = sess
        self._audit("ipc.token_issued", label=sess.label, scopes=sorted(sess.scopes))
        return token

    def revoke_token(self, token_or_label):
        n = 0
        with self._tlock:
            for k, s in list(self._tokens.items()):
                if s.label == token_or_label or hmac.compare_digest(k, _digest(str(token_or_label))):
                    s.revoked = True
                    del self._tokens[k]
                    n += 1
        if n:
            self._audit("ipc.token_revoked", count=n)
        return n

    def sessions(self):
        now = time.time()
        with self._tlock:
            return [{"label": s.label, "scopes": sorted(s.scopes), "expires_in": round(s.expires - now, 1),
                     "connections": s.connections} for s in self._tokens.values()]

    def _lookup(self, token):
        d = _digest(token)
        found = None
        with self._tlock:
            for k, s in self._tokens.items():           # compare against every entry: no early exit on a match
                if hmac.compare_digest(k, d):
                    found = s
        if found is not None and (found.revoked or time.time() >= found.expires):
            return None
        return found

    def _open_session_anonymous(self):
        return _Session("anonymous", ("ui", "kv", "tools"), time.time() + self.session_ttl)

    # ------------------------------------------------------------------ connection bookkeeping
    def _peer_ok(self, addr):
        return is_loopback_addr(addr) or self.allow_remote

    def _register(self, h):
        with self._clock:
            if self._stopping or len(self._clients) >= self.max_clients:
                return False
            self._clients.add(h)
            return True

    def _unregister(self, h):
        with self._clock:
            self._clients.discard(h)
        if h.session is not None and h.session.connections > 0 and self.require_auth:
            h.session.connections -= 1
        h.close()

    def _check_session(self, h):
        s = h.session
        if s.revoked:
            raise IpcError("revoked", "session revoked")
        if time.time() >= s.expires:
            raise IpcError("expired", "session expired")
        if time.monotonic() - h.last_seen > self.idle_timeout:
            raise IpcError("expired", "session idle timeout")

    def _audit(self, event, **kw):
        if self._audit_fn:
            try:
                self._audit_fn(event, **kw)
            except Exception:
                pass

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, method, p, h):
        table = {
            "window.create": ("ui", self._m_window_create), "node.add": ("ui", self._m_node_add),
            "node.set": ("ui", self._m_node_set), "node.subscribe": ("ui", self._m_node_subscribe),
            "ui.tree": ("ui", self._m_ui_tree),
            "services.kv_get": ("kv", self._m_kv_get), "services.kv_set": ("kv", self._m_kv_set),
            "tools.run": ("tools", self._m_tools_run), "tools.resolve": ("tools", self._m_tools_resolve),
            "tools.list": ("tools", self._m_tools_list),
            "server.status": ("admin", self._m_status), "server.shutdown": ("admin", self._m_shutdown),
        }
        entry = table.get(method)
        if entry is None:
            raise IpcError("unknown_method", "unknown method")
        scope, fn = entry
        if scope not in h.session.scopes:
            self._audit("ipc.forbidden", label=h.session.label, method=method)
            raise IpcError("forbidden", "this session may not call %s" % method)
        return fn(p, h)

    # ---- ui
    def _find(self, nid):
        if not _NODE_ID.match(nid or ""):
            raise IpcError("bad_request", "invalid node id")
        if self.backend.window is None:
            raise IpcError("invalid", "no window")
        node = self.backend.window.find(nid)
        if node is None:
            raise IpcError("invalid", "unknown node")
        return node

    @staticmethod
    def _props(p, required=False):
        props = p.get("props", {} if not required else None)
        if not isinstance(props, dict) or len(props) > 64 or any(not isinstance(k, str) or len(k) > 64 for k in props):
            raise IpcError("bad_request", "invalid parameter: props")
        _check_json_size(props, 16 * 1024, "props")
        return props

    def _m_window_create(self, p, h):
        w = Window(_str(p, "title", 256, required=False) or "")
        self.backend.show(w)
        return w.id

    def _m_node_add(self, p, h):
        pid = _str(p, "parent", 16, pattern=_NODE_ID)
        kind = _str(p, "kind", 32, pattern=_KIND)
        props = self._props(p)
        parent = self._find(pid)
        node = Node(kind, **props)
        parent.add(node)
        self.backend.update(parent)
        return node.id

    def _m_node_set(self, p, h):
        nid = _str(p, "node", 16, pattern=_NODE_ID)
        props = self._props(p, required=True)
        node = self._find(nid)
        node.props.update(props)
        self.backend.update(node)
        return True

    def _m_node_subscribe(self, p, h):
        nid = _str(p, "node", 16, pattern=_NODE_ID)
        event = _str(p, "event", 48, pattern=_EVENT)
        node = self._find(nid)

        def push(payload, ev=event, nid=node.id):
            try:
                _check_json_size(payload, self.max_message_bytes // 2, "payload")
            except IpcError:
                payload = None
            h.send({"event": ev, "node": nid, "payload": payload})
        node.on(event, push)
        return True

    def _m_ui_tree(self, p, h):
        return self.backend.window.to_dict() if self.backend.window else None

    # ---- kv
    def _need_services(self):
        if self.services is None:
            raise IpcError("unavailable", "platform services are not available")
        return self.services

    def _m_kv_get(self, p, h):
        key = _str(p, "key", 128, pattern=_KV_KEY)
        return self._need_services().kv_get(key)

    def _m_kv_set(self, p, h):
        key = _str(p, "key", 128, pattern=_KV_KEY)
        if "value" not in p:
            raise IpcError("bad_request", "missing parameter: value")
        _check_json_size(p["value"], MAX_KV_VALUE, "value")
        self._need_services().kv_set(key, p["value"])
        return True

    # ---- tools (forwarded to the Core "tools" service)
    def _tools(self):
        svc = self.core.services.get("tools") if self.core is not None else None
        if svc is None:
            raise IpcError("unavailable", "the tools service is not available")
        return svc

    def _m_tools_run(self, p, h):
        tools = self._tools()
        tool = _str(p, "tool", 63, pattern=_TOOL)
        args = p.get("args", [])
        if not isinstance(args, list) or len(args) > 256 or any(not isinstance(a, str) or len(a) > 8192 or "\0" in a
                                                                 for a in args):
            raise IpcError("bad_request", "invalid parameter: args (list of strings expected)")
        environment = _str(p, "environment", 64, required=False, pattern=_ENVIRONMENT) or "host"
        cwd = _str(p, "cwd", 4096, required=False)
        if cwd is not None and "\0" in cwd:
            raise IpcError("bad_request", "invalid parameter: cwd")
        env = p.get("env")
        if env is not None and (not isinstance(env, dict) or len(env) > 64 or any(
                not isinstance(k, str) or not _ENV_NAME.match(k) or not isinstance(v, str) or len(v) > 8192 or "\0" in v
                for k, v in env.items())):
            raise IpcError("bad_request", "invalid parameter: env")
        inp = p.get("input")
        if inp is not None and (not isinstance(inp, str) or len(inp) > MAX_TOOL_OUTPUT):
            raise IpcError("bad_request", "invalid parameter: input")
        timeout = p.get("timeout")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise IpcError("bad_request", "invalid parameter: timeout")
        timeout = min(float(timeout), MAX_TOOL_TIMEOUT) if timeout else MAX_TOOL_TIMEOUT
        caller = "ipc:" + h.session.label
        self._audit("ipc.tools_run", label=h.session.label, tool=tool, environment=environment)
        res = tools.run(tool, list(args), environment=environment, cwd=cwd, env=env, timeout=timeout,
                        input=inp, caller=caller)
        d = dict(res.to_dict() if hasattr(res, "to_dict") else res)
        d["stdout"], t1 = _cap(d.get("stdout"))
        d["stderr"], t2 = _cap(d.get("stderr"))
        d["truncated"] = t1 or t2
        return d

    def _m_tools_resolve(self, p, h):
        tools = self._tools()
        tool = _str(p, "tool", 63, pattern=_TOOL)
        environment = _str(p, "environment", 64, required=False, pattern=_ENVIRONMENT) or "host"
        return tools.resolve_info(tool, environment=environment)

    def _m_tools_list(self, p, h):
        return list(self._tools().list_tools())

    # ---- admin
    def _m_status(self, p, h):
        with self._clock:
            n = len(self._clients)
        return {"clients": n, "sessions": len(self.sessions()), "require_auth": self.require_auth,
                "uptime": round(time.time() - self._started, 1) if self._started else 0.0,
                "host": self.host, "remote_allowed": self.allow_remote}

    def _m_shutdown(self, p, h):
        threading.Thread(target=self.stop, daemon=True, name="ndx-ipc-stop").start()
        return True
