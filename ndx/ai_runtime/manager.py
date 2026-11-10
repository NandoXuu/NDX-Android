"""AI runtime manager: registers local models and runs them through external
inference engines. Implemented engine: llama.cpp's `llama-server` (must be
installed by the user: NDX does not bundle it). ONNX Runtime: not integrated."""
import json
import os
import uuid
from dataclasses import dataclass, field, asdict

from ..core.errors import NDXError, ValidationError, ToolUnavailable
from ..core.registry import Module
from ..server_manager.manager import ServerSpec
from ..toolchain_manager.manager import host_which as which


@dataclass
class ModelEntry:
    id: str
    name: str
    path: str
    format: str = "gguf"
    ctx: int = 2048
    threads: int = 4
    gpu_layers: int = 0
    port: int = 8081
    parallel: int = 1
    queue_limit: int = 8


class AIRuntimeManager(Module):
    name = "ai"
    title = "AI Runtime"

    def __init__(self, paths, servers, resources, bus):
        self.paths, self.servers, self.resources, self.bus = paths, servers, resources, bus
        self.file = paths.data / "models.json"
        self.models = {}

    def start(self, core):
        try:
            for d in json.loads(self.file.read_text()):
                m = ModelEntry(**d)
                self.models[m.id] = m
        except (OSError, ValueError, TypeError):
            pass
        if not self.engine_path():
            self.status = "unavailable"
            self.error = "llama-server not found (install it in %s or PATH)" % self.paths.bin

    def engine_path(self):
        return which("llama-server", [self.paths.bin])

    def _persist(self):
        self.file.write_text(json.dumps([asdict(m) for m in self.models.values()], indent=2))

    def register(self, name, path, **kw):
        if not os.path.isfile(path):
            raise ValidationError("model file not found: %s" % path)
        fmt = "gguf" if path.lower().endswith(".gguf") else "onnx" if path.lower().endswith(".onnx") else "unknown"
        m = ModelEntry(uuid.uuid4().hex[:8], name, path, fmt, **kw)
        self.models[m.id] = m
        self._persist()
        return m

    def unregister(self, mid):
        self.unload(mid)
        self.models.pop(mid, None)
        self._persist()

    def estimate_ram_mb(self, m):
        """ESTIMATE: file size + ~15% + KV-cache heuristic. Not a measurement."""
        size_mb = os.path.getsize(m.path) / (1024 * 1024)
        return int(size_mb * 1.15 + m.ctx * 0.5)

    def admission(self, mid):
        """Advice before loading a model (RAM estimate vs budget, concurrent heavy tasks). Estimates, not measurements."""
        m = self.models[mid]
        adv = self.resources.admit("ai", self.estimate_ram_mb(m)) if hasattr(self.resources, "admit") else {"ok": True, "warnings": [], "blocked": []}
        return {**adv, "estimated_ram_mb": self.estimate_ram_mb(m), "estimate": True}

    def load(self, mid):
        m = self.models[mid]
        if m.format != "gguf":
            raise ToolUnavailable("only GGUF models are supported (ONNX Runtime is not integrated)")
        eng = self.engine_path()
        if not eng:
            raise ToolUnavailable("llama-server is not installed")
        sid = "ai-" + mid
        spec = ServerSpec(
            id=sid, name="AI: " + m.name, kind="ai", runtime="custom",
            command=[eng, "-m", m.path, "-c", str(m.ctx), "-t", str(m.threads), "-ngl", str(m.gpu_layers),
                     "--host", "127.0.0.1", "--port", str(m.port), "--parallel", str(m.parallel)],
            port=m.port, ram_mb=self.estimate_ram_mb(m) * 4, threads=m.threads, policy="shutdown",
            max_concurrency=m.parallel, queue_limit=m.queue_limit)
        # The RAM *reservation* uses the estimate; RLIMIT_AS (x4 factor) is address space.
        spec.ram_mb = self.estimate_ram_mb(m)
        if sid in self.servers.runtimes:
            self.servers.runtimes[sid].spec = spec
        else:
            self.servers.add(spec)
        self.servers.start_server(sid)

    def unload(self, mid):
        sid = "ai-" + mid
        if sid in self.servers.runtimes:
            self.servers.stop_server(sid)

    def endpoint(self, mid):
        return "http://127.0.0.1:%d/v1" % self.models[mid].port

    def state(self, mid):
        rt = self.servers.runtimes.get("ai-" + mid)
        return rt.state if rt else "unloaded"
