"""Run the addon's Lua outside the game under Lua 5.1 semantics.

A developer tool, not a test suite: it loads Json.lua, Core.lua and UI.lua into
a LuaJIT runtime with a small stand-in for the client API, fires ADDON_LOADED,
and walks the paths a player takes once: type a message and press Enter, press
Ctrl+C on the selected line, watch the answer arrive through the live slots,
cancel a send (Esc, a click outside the window, a second request), run out of
live checks, fall back to a reload. Any Lua syntax
error or runtime error on those paths fails loudly here instead of in the game.

The stand-in keeps just enough state to make that walk honest: which text box
has the keyboard, which frames are hidden, a clock for C_Timer, and a pool of
load-on-demand slots whose content this script controls.

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
local calls = { reload = 0, messages = {}, sounds = 0 }
local frames = {}
local focused = nil
local clock, timers = 0, {}
local keys = { ctrl = false, shift = false }
local mouse = { over = nil }
local slots = { count = 0, loaded = {}, payload = nil }

local Widget = {}
Widget.__index = function(self, key)
    local f = rawget(Widget, key)
    if f then return f end
    -- Any other widget method (the client's are all PascalCase) is accepted and
    -- does nothing. Other keys are the addon's own fields and read as nil.
    if type(key) == 'string' and key:match('^%u') then
        return function(w) return w end
    end
end
local function newWidget(kind, name, parent)
    local w = setmetatable({ kind = kind, name = name, parent = parent, scripts = {}, text = '', shown = true, w = 0, h = 0 }, Widget)
    frames[#frames + 1] = w
    return w
end
local function fire(w, script, ...)
    local h = w.scripts[script]
    if h then return h(w, ...) end
end
local function within(w, ancestor)
    while w do
        if w == ancestor then return true end
        w = w.parent
    end
    return false
end
local function dropFocus()
    local was = focused
    if was then
        focused = nil
        fire(was, 'OnEditFocusLost')
    end
end
function Widget:SetScript(name, fn) self.scripts[name] = fn end
function Widget:GetScript(name) return self.scripts[name] end
function Widget:SetText(t)
    self.text = tostring(t or '')
    fire(self, 'OnTextChanged', false)
end
function Widget:GetText() return self.text end
function Widget:Insert(t) self.text = self.text .. t end
function Widget:Show()
    local was = self.shown
    self.shown = true
    if not was then fire(self, 'OnShow') end
end
function Widget:Hide()
    local was = self.shown
    self.shown = false
    -- a hidden text box cannot keep the keyboard
    if focused and within(focused, self) then dropFocus() end
    if was then fire(self, 'OnHide') end
end
function Widget:SetShown(b) if b then self:Show() else self:Hide() end end
function Widget:IsShown() return self.shown end
function Widget:SetFocus()
    if focused == self then return end
    dropFocus()
    focused = self
    fire(self, 'OnEditFocusGained')
end
function Widget:ClearFocus() if focused == self then dropFocus() end end
function Widget:HasFocus() return focused == self end
function Widget:IsMouseOver() return mouse.over == self end
function Widget:SetSize(w, h) self.w, self.h = w, h end
function Widget:SetWidth(w) self.w = w end
function Widget:SetHeight(h) self.h = h end
function Widget:GetWidth() return self.w end
function Widget:GetHeight() return self.h end
function Widget:GetPoint() return 'CENTER', nil, 'CENTER', 0, 0 end
function Widget:GetFrameLevel() return 1 end
function Widget:GetEffectiveScale() return 1 end
function Widget:GetVerticalScroll() return self.scroll or 0 end
function Widget:SetVerticalScroll(v) self.scroll = v end
function Widget:GetVerticalScrollRange() return 0 end
function Widget:GetStringWidth() return 6 * #self.text end
function Widget:GetUnboundedStringWidth() return 6 * #self.text end
function Widget:CreateFontString() return newWidget('FontString', nil, self) end
function Widget:CreateTexture() return newWidget('Texture', nil, self) end
function Widget:GetFontString() return newWidget('FontString', nil, self) end
function Widget:AddMessage(msg) calls.messages[#calls.messages + 1] = msg end
function Widget:RegisterEvent() end
function Widget:UnregisterEvent() end

CreateFrame = function(kind, name, parent)
    local w = newWidget(kind, name, parent)
    if name then _G[name] = w end
    return w
end
UIParent = newWidget('Frame', 'UIParent')
Minimap = newWidget('Minimap', 'Minimap', UIParent)
geterrorhandler = function() return function(e) error(e, 0) end end
GameTooltip = newWidget('GameTooltip', 'GameTooltip', UIParent)
DEFAULT_CHAT_FRAME = newWidget('MessageFrame', 'DEFAULT_CHAT_FRAME', UIParent)
UISpecialFrames = {}
ChatFontNormal, GameFontNormal, GameFontHighlightSmall = {}, {}, {}
SlashCmdList = {}
SOUNDKIT = { TELL_MESSAGE = 3081 }
PlaySound = function() calls.sounds = calls.sounds + 1 end
PixelUtil = { GetNearestPixelSize = function(size) return size end }

local function slotIndex(name)
    local i = tonumber(tostring(name):match('^ClaudeCode_Live(%d%d%d)$'))
    if i and i >= 1 and i <= slots.count then return i end
end
C_AddOns = {
    GetAddOnMetadata = function() return '0.0.0-smoke' end,
    DoesAddOnExist = function(name) return slotIndex(name) ~= nil end,
    IsAddOnLoaded = function(name) return slots.loaded[name] == true end,
    EnableAddOn = function() end,
    -- like the client: the slot's file runs when the addon is loaded, once
    LoadAddOn = function(name)
        if not slotIndex(name) then return false, 'MISSING' end
        if not slots.loaded[name] then
            slots.loaded[name] = true
            ClaudeCode_Live = slots.payload and slots.payload() or nil
        end
        return true
    end,
}
C_Timer = {
    After = function(delay, fn)
        timers[#timers + 1] = { at = clock + (delay or 0), fn = fn, order = #timers }
    end,
}
C_UI = { Reload = function() calls.reload = calls.reload + 1 end }
ReloadUI = function() calls.reload = calls.reload + 1 end
InCombatLockdown = function() return false end
IsControlKeyDown = function() return keys.ctrl end
IsShiftKeyDown = function() return keys.shift end
IsMetaKeyDown = function() return false end
time = os.time
date = os.date
strtrim = function(s) return (s:gsub('^%s+', ''):gsub('%s+$', '')) end
tinsert = table.insert
tremove = table.remove
wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
format = string.format

-- advance the clock, running every timer that comes due (including new ones)
function __advance(seconds)
    local stop = clock + seconds
    while true do
        local best
        for i, t in ipairs(timers) do
            if t.at <= stop and (not best or t.at < timers[best].at or (t.at == timers[best].at and t.order < timers[best].order)) then
                best = i
            end
        end
        if not best then break end
        local t = table.remove(timers, best)
        clock = math.max(clock, t.at)
        t.fn()
    end
    clock = stop
end
__calls, __frames, __keys, __slots, __fire, __mouse = calls, frames, keys, slots, fire, mouse
__focused = function() return focused end
"""

DRIVER = r"""
local calls, frames, keys, slots, fire, mouse = __calls, __frames, __keys, __slots, __fire, __mouse
local CC = ClaudeCode

local function event(name, ...)
    local before = #calls.messages
    for _, f in ipairs(frames) do
        if f.scripts.OnEvent then f.scripts.OnEvent(f, name, ...) end
    end
    return #calls.messages - before
end
local function inOutbox(id)
    for _, request in ipairs(ClaudeCodeDB.outbox) do
        if request.id == id then return true end
    end
    return false
end

-- what the companion wrote for the last reload: clipboard on, one old session,
-- one request already picked up; five live slots exist
local stale = { id = 'stale-1', kind = 'prompt', session_key = 's-1', prompt = 'old one' }
ClaudeCodeDB = { outbox = { stale } }
slots.count = 5
ClaudeCode_Data = {
    schema = 1, seq = 100, generated_at = time() - 5, companion_version = 'smoke',
    config = { projects = { 'demo' }, default_project = 'demo', backends = { 'claude-code', 'local' },
               default_backend = 'claude-code', clipboard_watch = true, running = '', queued = 0, nonce = 'n1', live_slots = 5 },
    consumed = { 'stale-1' },
    sessions = { ['s-1'] = { key = 's-1', title = 'old one', project = 'demo', backend = 'claude-code', status = 'done',
        cost_usd = 0.01, created_at = 1, updated_at = 2, partial = '',
        entries = { { role = 'user', text = 'old one', ts = 1 }, { role = 'assistant', text = 'hi | there', ts = 2 } } } },
}

assert(event('ADDON_LOADED', 'ClaudeCode') == 1, 'the picked-up request was not reported')
assert(CC.db == ClaudeCodeDB and #ClaudeCodeDB.outbox == 0, 'consumed request not pruned at load')
assert(not CC.loadError, CC.loadError)
assert(CC.frame and not CC.windowBroken, 'window not built')
assert(not CC.frame:IsShown(), 'the window must start closed')
assert(CC.minimapButton and CC.minimapButton:IsShown(), 'minimap button not shown')
assert(CC.live.count == 5 and CC:LiveLeft() == 5, 'live slots not counted')

-- the first run arrives as a reload: the intro prints once, then reloads stay quiet
assert(event('PLAYER_ENTERING_WORLD', false, true) == 1 and ClaudeCodeDB.intro_seen, 'intro line not printed on the first run')
SlashCmdList.CLAUDECODE('minimap')
assert(not CC.minimapButton:IsShown(), 'minimap toggle did not hide the button')
SlashCmdList.CLAUDECODE('minimap reset')
assert(CC.minimapButton:IsShown(), 'minimap reset did not show the button')

-- open the window; nothing takes the keyboard by itself
SlashCmdList.CLAUDECODE('')
assert(CC.frame:IsShown() and __focused() == nil, 'opening the window must not take the keyboard')
assert(CC.transcriptText:find('hi || there', 1, true), 'pipe not escaped in the transcript')
assert(CC.refreshButton.label:GetText() == 'Refresh', 'Refresh must not say reload while live checks are left')

-- type a message (the client doubles a typed "|") and press Enter
CC.ui.project, CC.ui.backend = 'demo', 'local'
CC:StartNewSession()
CC.promptBox:SetFocus()
CC.promptBox:SetText('hello "world"\nline two || pipe')
fire(CC.promptBox, 'OnEnterPressed')
local request = CC.copying
assert(request and request.kind == 'new' and request.nonce == 'n1', 'expected a new-session request waiting for Ctrl+C')
assert(request.prompt == 'hello "world"\nline two | pipe', 'the typed pipe was not undoubled')
assert(__focused() == CC.envelopeBox and CC.envelopeBox:IsShown(), 'the selected line does not have the keyboard')
local envelope = CC.envelopeBox:GetText()
assert(envelope:sub(1, #CC.ENVELOPE_PREFIX) == CC.ENVELOPE_PREFIX, 'bad prefix')
assert(not envelope:find('|', 1, true) and not envelope:find('\n', 1, true), 'the envelope must be one line without a pipe')
assert(CC.sendButton.label:GetText() == 'Cancel' and CC.hint:GetText():find('Ctrl+C', 1, true), 'no Ctrl+C guidance')
assert(calls.reload == 0 and not CC:IsPolling(), 'nothing may happen before Ctrl+C')

-- typing over the selected line puts it back
CC.envelopeBox.text = 'x'
fire(CC.envelopeBox, 'OnTextChanged', true)
assert(CC.envelopeBox:GetText() == envelope, 'the selected line was not restored')

-- Ctrl+C: the request counts as sent a moment later, the keyboard goes back to the game
keys.ctrl = true
fire(CC.envelopeBox, 'OnKeyDown', 'C')
keys.ctrl = false
assert(CC.copying, 'the focus must not move in the same frame as the copy')
__advance(0.1)
assert(not CC.copying and CC.sent[request.id] and __focused() == nil, 'Ctrl+C did not finish the send')
assert(not CC.envelopeBox:IsShown() and CC.promptBox:GetText() == '', 'the message box was not reset')
assert(inOutbox(request.id) and CC:IsPolling(), 'a sent request must stay queued and start the live checks')
assert(CC.transcriptText:find('(sent)', 1, true), 'the sent message is not echoed')

-- the companion picks it up and streams; the next live check shows it
slots.payload = function()
    return { schema = 1, seq = 200, generated_at = time(), companion_version = 'smoke',
        config = { projects = { 'demo' }, default_project = 'demo', backends = { 'claude-code', 'local' }, default_backend = 'claude-code',
                   clipboard_watch = true, running = request.id, queued = 0, nonce = 'n1', live_slots = 5 },
        consumed = { 'stale-1', request.id },
        sessions = { [request.session_key] = { key = request.session_key, title = 'hello', project = 'demo', backend = 'local',
            status = 'running', created_at = 5, updated_at = 6, partial = 'Thinking about **it**', first = 0, total = 1,
            entries = { { role = 'user', text = request.prompt, ts = 5 } } } } }
end
__advance(3.5)
assert(CC.live.next == 2 and CC.live.ok, 'the first live check did not run')
assert(not inOutbox(request.id) and CC:IsPolling(), 'a picked-up request leaves the outbox and the checks go on')
assert(CC.transcriptText:find('Thinking about', 1, true) and not CC.transcriptText:find('**', 1, true), 'partial answer not drawn')
assert(CC.stopButton:IsShown(), 'Stop must show while a turn runs')

-- the turn ends: the answer is drawn, the checks stop, nothing reloaded
slots.payload = function()
    return { schema = 1, seq = 300, generated_at = time(), companion_version = 'smoke',
        config = { projects = { 'demo' }, default_project = 'demo', backends = { 'claude-code', 'local' }, default_backend = 'claude-code',
                   clipboard_watch = true, running = '', queued = 0, nonce = 'n1', live_slots = 5 },
        consumed = { 'stale-1', request.id },
        sessions = { [request.session_key] = { key = request.session_key, title = 'hello', project = 'demo', backend = 'local',
            status = 'done', created_at = 5, updated_at = 9, partial = '', first = 0, total = 3,
            entries = { { role = 'user', text = request.prompt, ts = 5 },
                        { role = 'assistant', text = '# Plan\nUse `/reload` **never**.\n```lua\nprint("a|b")\n```', ts = 8 },
                        { role = 'result', text = '2.0s, 1 turn', ts = 9 } } } } }
end
__advance(5)
assert(CC.live.next == 3 and not CC:IsPolling() and not CC:IsWaiting(), 'the checks must stop when the turn is done')
assert(CC.transcriptText:find('print("a||b")', 1, true) and not CC.transcriptText:find('```', 1, true), 'answer not drawn')
assert(not CC.stopButton:IsShown() and calls.reload == 0, 'a live answer must not reload')
local answered = CC.transcriptText

-- an answer that lands while the window is closed is announced
CC.frame:Hide()
local follow = CC:SendPrompt('second', 'reload')   -- queued by reload this time
assert(calls.reload == 1 and follow.kind == 'prompt' and follow.session_key == request.session_key, 'reload send must reload once and continue the chat')
CC.sent[follow.id] = time()
slots.payload = function()
    return { schema = 1, seq = 400, generated_at = time(), config = ClaudeCode_Data.config, consumed = { follow.id },
        sessions = { [request.session_key] = { key = request.session_key, title = 'hello', backend = 'local', status = 'running', partial = '', first = 3, total = 4,
            entries = { { role = 'user', text = 'second', ts = 10 } } } } }
end
assert(CC:Refresh() == 'new', 'a refresh with live checks left reads a slot')
slots.payload = function()
    return { schema = 1, seq = 500, generated_at = time(), config = ClaudeCode_Data.config, consumed = { follow.id },
        sessions = { [request.session_key] = { key = request.session_key, title = 'hello', backend = 'local', status = 'done', partial = '', first = 3, total = 5,
            entries = { { role = 'user', text = 'second', ts = 10 }, { role = 'assistant', text = 'done', ts = 11 } } } } }
end
local before = #calls.messages
__advance(4)
assert(#calls.messages == before + 1 and calls.sounds == 1 and CC.unread, 'the finished answer was not announced')
assert(#CC:CurrentSession().entries == 5, 'a trimmed live session must keep the entries the client already had')
CC.frame:Show()
assert(not CC.unread, 'opening the window clears the unread mark')

-- cancel a send: the request is withdrawn and the text comes back
CC.promptBox:SetFocus()
CC.promptBox:SetText('take || this back')
fire(CC.promptBox, 'OnEnterPressed')
local cancelled = CC.copying
assert(cancelled and inOutbox(cancelled.id), 'second send not staged')
fire(CC.envelopeBox, 'OnEscapePressed')
assert(not CC.copying and not inOutbox(cancelled.id) and CC.promptBox:GetText() == 'take || this back', 'cancel did not restore the message')

-- a click outside the window gives the keyboard back to the game; one inside does not
fire(CC.promptBox, 'OnEnterPressed')
local staged = CC.copying
assert(staged and __focused() == CC.envelopeBox, 'third send not staged')
mouse.over = CC.frame
event('GLOBAL_MOUSE_DOWN', 'LeftButton')
assert(CC.copying == staged, 'a click inside the window must not take the message back')
mouse.over = nil
event('GLOBAL_MOUSE_DOWN', 'RightButton')
assert(not CC.copying and not inOutbox(staged.id) and __focused() == nil, 'a click outside the window did not give the keyboard back')
assert(CC.promptBox:GetText() == 'take || this back', 'the message did not return to the box')

-- one staged request at a time: a second one takes the first back
fire(CC.promptBox, 'OnEnterPressed')
local first = CC.copying
SlashCmdList.CLAUDECODE('copy another one')
local second = CC.copying
assert(first and second and second ~= first and second.prompt == 'another one', 'the second request was not staged')
assert(not inOutbox(first.id) and inOutbox(second.id), 'the first staged request was not taken back')
assert(CC.promptBox:GetText() == 'take || this back', 'the first message did not return to the box')
-- the client takes the keyboard away in the frame that staged the line: the
-- request stays staged and the line gets the keyboard back on the next frame
CC.envelopeBox:ClearFocus()
assert(CC.copying == second, 'losing the keyboard in the staging frame must not take the request back')
__advance(0.01)
assert(CC.copying == second and __focused() == CC.envelopeBox, 'the selected line did not get the keyboard back')
CC.envelopeBox:ClearFocus()
assert(not CC.copying and not inOutbox(second.id), 'losing the keyboard later takes the request back')
assert(CC.promptBox:GetText() == 'take || this back', 'a draft in the message box must not be overwritten')

-- the last live check is used up: Refresh says what it does, and only then reloads
assert(CC:LiveLeft() == 1 and CC:Refresh() == 'same' and calls.reload == 1, 'the last live check must not reload')
assert(CC:LiveLeft() == 0 and CC.refreshButton.label:GetText() == 'Reload UI', 'the button must say it reloads')
assert(CC:Refresh() == 'reload' and calls.reload == 2, 'with no live check left Refresh reloads')

-- reload mode (clipboard path off): a send reloads at once
ClaudeCode_Data.config.clipboard_watch = false
assert(CC:EffectiveSendMode() == 'reload', 'send mode should follow the companion')
CC:SendPrompt('by reload')
assert(calls.reload == 3 and not CC.copying, 'reload send must reload')
-- the send mode can be forced from the chat line, whatever the companion reports
ClaudeCode_Data.config.clipboard_watch = true
SlashCmdList.CLAUDECODE('mode reload')
assert(CC:EffectiveSendMode() == 'reload', 'mode reload must force the reload path')
SlashCmdList.CLAUDECODE('mode auto')
assert(CC:EffectiveSendMode() == 'clipboard', 'mode auto must follow the companion again')
SlashCmdList.CLAUDECODE('status')
SlashCmdList.CLAUDECODE('live')
return envelope, #calls.messages, answered
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
    print("transcript after the live answer:")
    for line in transcript.splitlines():
        print("   ", line[:110])
    print("lua smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
