-- Lua client for the NDX IPC. Needs luasocket (`require "socket"`); no other dependency.
--   local ndx = require("ndx")
--   local app = ndx.connect()                 -- NDX_IPC_PORT / NDX_IPC_TOKEN from the environment
--   local r = app:tools_run("git", {"status"})
-- STATUS: written but NOT run against a live NDX (no Lua interpreter was available when it was written).
local M = {}

local ok_socket, socket = pcall(require, "socket")

-- ---------------------------------------------------------------- tiny JSON
local function esc(s)
  return s:gsub('[%c"\\]', function(c)
    local m = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t', ['\b'] = '\\b', ['\f'] = '\\f' }
    return m[c] or string.format("\\u%04x", c:byte())
  end)
end

local function is_array(t)
  local n = 0
  for k in pairs(t) do
    if type(k) ~= "number" then return false end
    n = n + 1
  end
  return n == #t
end

local function encode(v)
  local t = type(v)
  if t == "nil" then return "null"
  elseif t == "boolean" then return tostring(v)
  elseif t == "number" then return string.format("%.14g", v)
  elseif t == "string" then return '"' .. esc(v) .. '"'
  elseif t == "table" then
    if next(v) == nil then return (getmetatable(v) == M.object_mt) and "{}" or "[]" end
    local out = {}
    if is_array(v) then
      for i = 1, #v do out[i] = encode(v[i]) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    for k, x in pairs(v) do out[#out + 1] = '"' .. esc(tostring(k)) .. '":' .. encode(x) end
    return "{" .. table.concat(out, ",") .. "}"
  end
  error("cannot encode " .. t)
end
M.object_mt = {}
M.encode = encode

local function decode(s)
  local pos = 1
  local function ws() pos = s:find("%S", pos) or #s + 1 end
  local value
  local function str()
    local out, i = {}, pos + 1
    while true do
      local c = s:sub(i, i)
      if c == "" then error("unterminated string") end
      if c == '"' then pos = i + 1; return table.concat(out) end
      if c == "\\" then
        local n = s:sub(i + 1, i + 1)
        local map = { n = "\n", r = "\r", t = "\t", b = "\b", f = "\f", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
        if n == "u" then
          local cp = tonumber(s:sub(i + 2, i + 5), 16)
          if not cp then error("bad \\u escape") end
          out[#out + 1] = utf8 and utf8.char(cp) or "?"
          i = i + 6
        else out[#out + 1] = map[n] or n; i = i + 2 end
      else out[#out + 1] = c; i = i + 1 end
    end
  end
  function value()
    ws()
    local c = s:sub(pos, pos)
    if c == "{" then
      local t = setmetatable({}, M.object_mt); pos = pos + 1; ws()
      if s:sub(pos, pos) == "}" then pos = pos + 1; return t end
      while true do
        ws(); local k = str(); ws()
        if s:sub(pos, pos) ~= ":" then error("expected ':'") end
        pos = pos + 1; t[k] = value(); ws()
        local d = s:sub(pos, pos); pos = pos + 1
        if d == "}" then return t elseif d ~= "," then error("expected ',' or '}'") end
      end
    elseif c == "[" then
      local t = {}; pos = pos + 1; ws()
      if s:sub(pos, pos) == "]" then pos = pos + 1; return t end
      while true do
        t[#t + 1] = value(); ws()
        local d = s:sub(pos, pos); pos = pos + 1
        if d == "]" then return t elseif d ~= "," then error("expected ',' or ']'") end
      end
    elseif c == '"' then return str()
    elseif s:sub(pos, pos + 3) == "true" then pos = pos + 4; return true
    elseif s:sub(pos, pos + 4) == "false" then pos = pos + 5; return false
    elseif s:sub(pos, pos + 3) == "null" then pos = pos + 4; return nil
    else
      local num = s:match("^-?%d+%.?%d*[eE]?[+-]?%d*", pos)
      if not num or num == "" then error("bad JSON at " .. pos) end
      pos = pos + #num; return tonumber(num)
    end
  end
  return value()
end
M.decode = decode

-- ---------------------------------------------------------------- client
local App = {}
App.__index = App

function M.connect(opts)
  opts = opts or {}
  if not ok_socket then
    error("ndx.lua needs luasocket (install it, e.g. `luarocks install luasocket`)", 2)
  end
  local port = opts.port or os.getenv("NDX_IPC_PORT")
  local token = opts.token or os.getenv("NDX_IPC_TOKEN")
  if not port then error("no IPC port: pass port= or set NDX_IPC_PORT", 2) end
  if not token then error("no IPC token: pass token= or set NDX_IPC_TOKEN", 2) end
  local sock = assert(socket.tcp())
  sock:settimeout(opts.timeout or 10)
  local ok, err = sock:connect(opts.host or "127.0.0.1", tonumber(port))
  if not ok then error("cannot connect to NDX: " .. tostring(err), 2) end
  local self = setmetatable({ sock = sock, id = 0, timeout = opts.timeout or 10 }, App)
  self.scopes = self:call("auth", { token = token }).scopes
  return self
end

function App:call(method, params)
  self.id = self.id + 1
  local line = encode({ id = self.id, method = method, params = params or setmetatable({}, M.object_mt) }) .. "\n"
  local _, err = self.sock:send(line)
  if err then error("send failed: " .. tostring(err), 2) end
  while true do
    local reply, rerr = self.sock:receive("*l")
    if not reply then error("NDX connection error: " .. tostring(rerr), 2) end
    local msg = decode(reply)
    if not msg.event then
      if msg.error ~= nil then error(string.format("NDX error [%s]: %s", tostring(msg.code), tostring(msg.error)), 2) end
      return msg.result
    end
  end
end

function App:window(title) return self:call("window.create", { title = title }) end
function App:add(parent, kind, props) return self:call("node.add", { parent = parent, kind = kind, props = props or setmetatable({}, M.object_mt) }) end
function App:set(node, props) return self:call("node.set", { node = node, props = props }) end
function App:tree() return self:call("ui.tree") end
function App:kv_get(key) return self:call("services.kv_get", { key = key }) end
function App:kv_set(key, value) return self:call("services.kv_set", { key = key, value = value }) end
-- args is an ARRAY of strings: never build a shell string from untrusted input
function App:tools_run(tool, args, opts)
  opts = opts or {}
  local p = { tool = tool, args = args or {}, environment = opts.environment or "host",
              cwd = opts.cwd, env = opts.env, timeout = opts.timeout, input = opts.input }
  self.sock:settimeout((opts.timeout or 600) + 15)
  local r = self:call("tools.run", p)
  self.sock:settimeout(self.timeout)
  return r
end
function App:tools_resolve(tool, environment) return self:call("tools.resolve", { tool = tool, environment = environment or "host" }) end
function App:tools_list() return self:call("tools.list") end
function App:close() self.sock:close() end

return M
