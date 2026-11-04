"""Run the addon's Lua outside the game under Lua 5.1 semantics.

A developer tool, not a test suite: it loads Json.lua, Core.lua and UI.lua into
a LuaJIT runtime with a small stand-in for the client API, fires ADDON_LOADED,
and drives the public functions once (new request, clipboard envelope,
reload send, consumed pruning, render, slash command). Any Lua syntax error or
runtime error in that path fails loudly here instead of in the game.

Needs the ``lupa`` package (``pip install lupa``); nothing else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from lupa.luajit21 import LuaRuntime  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on the wheel
    from lupa.lua51 import LuaRuntime  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "ClaudeCode"

SHIM = r"""
local calls = { reload = 0, messages = {} }
local frames = {}

local Widget = {}
Widget.__index = function(self, key)
    local f = rawget(Widget, key)
    if f then return f end
    return function(w, ...)
        return w
    end
end
local function newWidget(kind, name)
    local w = setmetatable({ kind = kind, name = name, scripts = {}, text = '', shown = false, children = {} }, Widget)
    frames[#frames + 1] = w
    return w
end
function Widget:SetScript(name, fn) self.scripts[name] = fn end
function Widget:GetScript(name) return self.scripts[name] end
function Widget:SetText(t) self.text = t end
function Widget:GetText() return self.text end
function Widget:Insert(t) self.text = self.text .. t end
function Widget:Show() self.shown = true end
function Widget:Hide() self.shown = false end
function Widget:IsShown() return self.shown end
function Widget:GetPoint() return 'CENTER', nil, 'CENTER', 0, 0 end
function Widget:CreateFontString() return newWidget('FontString') end
function Widget:GetFontString() return newWidget('FontString') end
function Widget:AddMessage(msg) calls.messages[#calls.messages + 1] = msg end
function Widget:RegisterEvent() end
function Widget:UnregisterEvent() end

CreateFrame = function(kind, name, parent, template)
    local w = newWidget(kind, name)
    if name then _G[name] = w end
    return w
end
UIParent = newWidget('Frame', 'UIParent')
Minimap = newWidget('Minimap', 'Minimap')
geterrorhandler = function() return function(e) error(e, 0) end end
GameTooltip = newWidget('GameTooltip', 'GameTooltip')
DEFAULT_CHAT_FRAME = newWidget('MessageFrame', 'DEFAULT_CHAT_FRAME')
UISpecialFrames = {}
ChatFontNormal = {}
BackdropTemplateMixin = {}
SlashCmdList = {}
C_AddOns = { GetAddOnMetadata = function() return '0.1.0-smoke' end }
C_UI = { Reload = function() calls.reload = calls.reload + 1 end }
ReloadUI = function() calls.reload = calls.reload + 1 end
InCombatLockdown = function() return false end
IsControlKeyDown = function() return false end
time = os.time
date = os.date
strtrim = function(s) return (s:gsub('^%s+', ''):gsub('%s+$', '')) end
tinsert = table.insert
tremove = table.remove
wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
format = string.format
__calls = calls
__frames = frames
"""

DRIVER = r"""
local calls, frames = __calls, __frames
-- fire ADDON_LOADED on every frame that listens
for _, f in ipairs(frames) do
    local h = f.scripts and f.scripts.OnEvent
    if h then h(f, 'ADDON_LOADED', 'ClaudeCode') end
end
assert(ClaudeCode.db == ClaudeCodeDB, 'db not initialised')
assert(not ClaudeCode.loadError, ClaudeCode.loadError)
assert(ClaudeCode.frame, 'UI frame not created')
assert(ClaudeCode.minimapButton and ClaudeCode.minimapButton.shown, 'minimap button not shown')

-- the first run arrives as a reload: the intro prints once, then reloads stay quiet
local function enterWorld(isLogin, isReload)
    local before = #calls.messages
    for _, f in ipairs(frames) do
        local h = f.scripts and f.scripts.OnEvent
        if h then h(f, 'PLAYER_ENTERING_WORLD', isLogin, isReload) end
    end
    return #calls.messages - before
end
assert(enterWorld(false, true) == 1 and ClaudeCodeDB.intro_seen, 'intro line not printed on the first run')
assert(enterWorld(false, true) == 0, 'a reload must stay quiet')
assert(enterWorld(true, false) == 1, 'a login must print the intro')
SlashCmdList.CLAUDECODE('minimap')
assert(not ClaudeCode.minimapButton.shown, 'minimap toggle did not hide the button')
SlashCmdList.CLAUDECODE('minimap reset')
assert(ClaudeCode.minimapButton.shown, 'minimap reset did not show the button')

-- companion data present: one consumed id, one session, clipboard on
local stale = ClaudeCode:NewRequest('prompt', 's-1', 'demo', 'claude-code', 'old one')
ClaudeCode:Enqueue(stale)
ClaudeCode_Data = {
    schema = 1, generated_at = time() - 5, companion_version = 'smoke',
    config = { projects = { 'demo' }, default_project = 'demo', backends = { 'claude-code', 'local' },
               default_backend = 'claude-code', clipboard_watch = true, running = '', queued = 0 },
    consumed = { stale.id },
    sessions = { ['s-1'] = { key = 's-1', title = 'old one', project = 'demo', backend = 'claude-code', status = 'done',
        cost_usd = 0.01, created_at = 1, updated_at = 2, partial = '',
        entries = { { role = 'user', text = 'old one', ts = 1 }, { role = 'assistant', text = 'hi | there', ts = 2 } } } },
}
local removed = ClaudeCode:PruneConsumed(ClaudeCode:GetData())
assert(removed == 1 and #ClaudeCodeDB.outbox == 0, 'prune failed')

-- clipboard send (companion reports clipboard on)
ClaudeCode.ui.project = 'demo'
ClaudeCode.ui.backend = 'local'
ClaudeCode:StartNewSession()
local req = ClaudeCode:SendPrompt('hello "world"\nline two | pipe')
assert(req and req.kind == 'new', 'expected a new-session request')
assert(ClaudeCode.envelopeFrame.shown, 'envelope not shown')
local envelope = ClaudeCode.envelopeText
assert(envelope:sub(1, #ClaudeCode.ENVELOPE_PREFIX) == ClaudeCode.ENVELOPE_PREFIX, 'bad prefix')
assert(not envelope:find('|', 1, true), 'envelope contains a pipe')
assert(calls.reload == 0, 'clipboard send must not reload')

-- reload send on the same session
local req2 = ClaudeCode:SendPrompt('follow up', 'reload')
assert(req2.kind == 'prompt' and req2.session_key == req.session_key, 'follow-up should continue the session')
assert(calls.reload == 1, 'reload send must reload once')
assert(#ClaudeCodeDB.outbox == 2, 'both requests queued')

-- render the old session, then a slash command
ClaudeCode.newSession = false
ClaudeCode.ui.session = 's-1'
ClaudeCode:Render()
assert(ClaudeCode.transcriptText:find('hi || there', 1, true), 'pipe not escaped in transcript')
SlashCmdList.CLAUDECODE('status')
ClaudeCode:Toggle()
assert(ClaudeCode.frame:IsShown(), 'toggle did not show')
return envelope, #calls.messages, ClaudeCode.transcriptText
"""


def main() -> int:
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(SHIM)
    loadstring = lua.eval("loadstring")
    for name in ("Json.lua", "Core.lua", "UI.lua"):
        code = (ADDON / name).read_text(encoding="utf-8")
        loaded = loadstring(code, "@" + name)
        chunk, err = (loaded if isinstance(loaded, tuple) else (loaded, None))[:2]
        if chunk is None:
            print(f"SYNTAX ERROR in {name}: {err}")
            return 1
        chunk("ClaudeCode", lua.table())
    envelope, messages, transcript = lua.execute(DRIVER)
    payload = json.loads(envelope.split(":", 2)[2])
    print("envelope:", envelope[:120] + ("..." if len(envelope) > 120 else ""))
    print("decoded prompt:", repr(payload["prompt"]))
    print("chat messages printed:", messages)
    print("transcript head:", transcript.splitlines()[0][:80])
    print("lua smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
