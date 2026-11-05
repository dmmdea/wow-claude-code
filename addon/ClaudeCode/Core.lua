-- Core.lua: saved state, the request outbox, the clipboard envelope, the live
-- channel, slash commands and the handshake with the files the companion writes.
--
-- The addon never talks to anything outside the game. A request leaves either
-- through SavedVariables (written by the client on /reload) or as one line the
-- player copies with Ctrl+C. Answers come back in the ClaudeCode_Data addon on
-- a /reload, or without one through the live slots: load-on-demand addons whose
-- single file the companion rewrites and this addon loads with
-- C_AddOns.LoadAddOn. The only timer here checks those slots while an answer
-- is pending; nothing ever reloads the UI or sends input by itself.

local ADDON_NAME = ...

ClaudeCode = ClaudeCode or {}
local CC = ClaudeCode

CC.ENVELOPE_PREFIX = 'CLAUDECODE-WOW:1:'
CC.DATA_SCHEMA = 1
CC.MAX_OUTBOX = 50
CC.LIVE_PREFIX = 'ClaudeCode_Live'
CC.LIVE_MAX = 400
-- Seconds between live checks while an answer is pending; the last one repeats.
-- Every check spends one slot, so the gaps grow.
CC.POLL_STEPS = { 3, 4, 5, 6, 8, 10, 14, 20, 25, 30, 35, 40, 50, 60 }
CC.POLL_GIVE_UP = 12
CC.VERSION = (C_AddOns and C_AddOns.GetAddOnMetadata and C_AddOns.GetAddOnMetadata(ADDON_NAME, 'Version'))
	or (GetAddOnMetadata and GetAddOnMetadata(ADDON_NAME, 'Version'))
	or '?'

local DEFAULTS = {
	version = 1,
	outbox = {},
	intro_seen = false,         -- the one-time "how to open me" chat line was shown
	settings = {
		auto_reload = true,     -- reload sends reload the UI right away
		send_mode = 'auto',     -- auto | reload | clipboard
		width = 760,
		height = 520,
		minimap = true,         -- show the minimap button
		live = true,            -- read answers through the live slots (no reload)
		sound = true,           -- a soft sound when an answer lands while the window is closed
	},
	ui = {
		session = nil,
		project = nil,
		backend = nil,
		point = nil,
	},
}

local function deepCopy(value)
	if type(value) ~= 'table' then
		return value
	end
	local out = {}
	for k, v in pairs(value) do
		out[k] = deepCopy(v)
	end
	return out
end

local function fillDefaults(target, defaults)
	for k, v in pairs(defaults) do
		if target[k] == nil then
			target[k] = deepCopy(v)
		elseif type(v) == 'table' and type(target[k]) == 'table' then
			fillDefaults(target[k], v)
		end
	end
end

function CC:Print(msg)
	local frame = DEFAULT_CHAT_FRAME
	if frame then
		frame:AddMessage('|cffd97757Claude Code:|r ' .. tostring(msg))
	else
		print('Claude Code: ' .. tostring(msg))
	end
end

-- ------------------------------------------------------------------ saved state

function CC:InitDB()
	if type(ClaudeCodeDB) ~= 'table' then
		ClaudeCodeDB = {}
	end
	fillDefaults(ClaudeCodeDB, DEFAULTS)
	if type(ClaudeCodeDB.outbox) ~= 'table' then
		ClaudeCodeDB.outbox = {}
	end
	self.db = ClaudeCodeDB
	self.ui = ClaudeCodeDB.ui
	self.sent = {}
	return self.db
end

local function validData(data)
	return type(data) == 'table' and data.schema == CC.DATA_SCHEMA and type(data.sessions) == 'table'
end

--- What the companion has told us so far: the table it wrote for the last
--- /reload, updated in place by every live refresh. Nil before it ever ran.
function CC:GetData()
	if self.data then
		return self.data
	end
	local data = _G.ClaudeCode_Data
	if validData(data) then
		self.data = data
		return data
	end
	return nil
end

function CC:DataAge()
	local data = self:GetData()
	if not data or not data.generated_at or data.generated_at == 0 then
		return nil
	end
	return math.max(0, time() - data.generated_at)
end

-- --------------------------------------------------------------------- requests

local counter = 0

function CC:NewRequest(kind, sessionKey, project, backend, prompt)
	counter = counter + 1
	local data = self:GetData()
	return {
		id = string.format('%d-%04x-%d', time(), math.random(0, 65535), counter),
		kind = kind,
		session_key = sessionKey or '',
		project = project or '',
		backend = backend or '',
		prompt = prompt or '',
		created_at = time(),
		-- the companion's per-install token; the clipboard path requires it
		nonce = (data and data.config and data.config.nonce) or '',
	}
end

function CC:Enqueue(request)
	local outbox = self.db.outbox
	outbox[#outbox + 1] = request
	while #outbox > self.MAX_OUTBOX do
		tremove(outbox, 1)
	end
	return request
end

--- Take a request back out of the outbox (a cancelled copy).
function CC:Withdraw(id)
	local outbox = self.db.outbox
	for i = #outbox, 1, -1 do
		if outbox[i].id == id then
			tremove(outbox, i)
		end
	end
	self.sent[id] = nil
end

--- Drop every outbox entry the companion reports as consumed.
function CC:PruneConsumed(data)
	if not data or type(data.consumed) ~= 'table' then
		return 0
	end
	local consumed = {}
	for _, id in pairs(data.consumed) do
		consumed[tostring(id)] = true
	end
	local kept, removed = {}, 0
	for _, request in ipairs(self.db.outbox) do
		if consumed[tostring(request.id)] then
			removed = removed + 1
			self.sent[request.id] = nil
		else
			kept[#kept + 1] = request
		end
	end
	self.db.outbox = kept
	return removed
end

function CC:PendingCount()
	return #self.db.outbox
end

--- One line the companion's clipboard watcher understands.
function CC:Envelope(request)
	return self.ENVELOPE_PREFIX .. ClaudeCodeJson.encode(request)
end

-- ---------------------------------------------------------------------- reload

--- Reload the UI so the client writes SavedVariables and re-reads the data file.
--- C_UI.Reload is restricted to hardware events, so this is only ever reached
--- from a button click, a keybinding or a typed slash command, never a timer.
function CC:Reload()
	if InCombatLockdown and InCombatLockdown() then
		self:Print('in combat; the request is saved and leaves the game at your next /reload.')
		return false
	end
	if C_UI and C_UI.Reload then
		C_UI.Reload()
	else
		ReloadUI()
	end
	return true
end

--- How a request leaves the game: 'clipboard' when the companion reports its
--- watcher on (or the user forced it), else 'reload'.
function CC:EffectiveSendMode()
	local mode = self.db.settings.send_mode or 'auto'
	if mode ~= 'auto' then
		return mode
	end
	local data = self:GetData()
	if data and data.config and data.config.clipboard_watch then
		return 'clipboard'
	end
	return 'reload'
end

--- Queue a request and move it out of the game by the effective send mode.
function CC:Dispatch(request, forceMode)
	self:Enqueue(request)
	local mode = forceMode or self:EffectiveSendMode()
	if mode == 'clipboard' and self.ShowEnvelope and not self.windowBroken then
		self:ShowEnvelope(request)
		return 'clipboard'
	end
	if self.db.settings.auto_reload then
		self:Reload()
	else
		self:Print('queued; press Refresh (or /reload) to send it to the companion.')
	end
	return 'reload'
end

--- The player pressed Ctrl+C on the envelope: the request is on its way.
function CC:MarkSent(request)
	self.sent[request.id] = time()
	self:StartPolling(true)
end

-- ----------------------------------------------------------------- live channel

local function addonExists(name)
	if C_AddOns and C_AddOns.DoesAddOnExist then
		return C_AddOns.DoesAddOnExist(name) and true or false
	end
	local info = (C_AddOns and C_AddOns.GetAddOnInfo) or GetAddOnInfo
	if not info then
		return false
	end
	local found, _, _, _, reason = info(name)
	return found ~= nil and reason ~= 'MISSING'
end

local function addonLoaded(name)
	local isLoaded = (C_AddOns and C_AddOns.IsAddOnLoaded) or IsAddOnLoaded
	return (isLoaded and isLoaded(name)) and true or false
end

local function loadAddon(name)
	local load = (C_AddOns and C_AddOns.LoadAddOn) or LoadAddOn
	if not load then
		return false, 'NO_API'
	end
	local ok, loaded, reason = pcall(load, name)
	if not ok then
		return false, tostring(loaded)
	end
	return loaded and true or false, reason
end

function CC:SlotName(i)
	return string.format('%s%03d', self.LIVE_PREFIX, i)
end

--- Count the live slots this client knows about. The client lists addon
--- folders when it starts and on a /reload, so slots installed while it runs
--- show up after the next one of those.
function CC:InitLive()
	local live = { count = 0, next = 1, used = 0, seq = 0, ok = false, fails = 0 }
	self.live = live
	for i = 1, self.LIVE_MAX do
		if not addonExists(self:SlotName(i)) then
			break
		end
		live.count = i
	end
	local data = self:GetData()
	live.seq = data and tonumber(data.seq) or 0
	return live
end

--- How many live refreshes are left before a /reload is needed to recharge.
function CC:LiveLeft()
	local live = self.live
	if not live or live.broken or not self.db.settings.live then
		return 0
	end
	return math.max(0, live.count - live.next + 1)
end

--- Fold a live payload into the data table. Returns the sessions that just
--- stopped running.
function CC:MergeLive(payload)
	local data = self:GetData()
	if not data then
		data = { schema = payload.schema, sessions = {}, consumed = {}, config = {} }
		self.data = data
	end
	data.generated_at = payload.generated_at
	data.seq = payload.seq
	data.companion_version = payload.companion_version or data.companion_version
	if type(payload.config) == 'table' then
		data.config = payload.config
	end
	if type(payload.consumed) == 'table' then
		data.consumed = payload.consumed
	end
	local finished = {}
	if type(payload.sessions) == 'table' then
		for key, session in pairs(payload.sessions) do
			local old = data.sessions[key]
			local first = tonumber(session.first) or 0
			if type(session.entries) ~= 'table' then
				session.entries = {}
			end
			-- A long session arrives without its oldest entries (first says how
			-- many); keep the ones this client already holds.
			local oldFirst = old and tonumber(old.first) or 0
			if first > 0 and old and type(old.entries) == 'table' and oldFirst <= first and oldFirst + #old.entries >= first then
				local merged = {}
				for i = 1, first - oldFirst do
					merged[i] = old.entries[i]
				end
				for _, entry in ipairs(session.entries) do
					merged[#merged + 1] = entry
				end
				session.entries = merged
				session.first = oldFirst
			end
			session.key = session.key or key
			if old and old.status == 'running' and session.status ~= 'running' then
				finished[#finished + 1] = session
			end
			data.sessions[key] = session
		end
	end
	self:PruneConsumed(data)
	return finished
end

--- Load the next unused live slot and merge what the companion wrote there.
--- Returns 'new' (newer data merged), 'same' (nothing newer), 'none' (no slot
--- left, or the channel is off) or 'failed', plus the sessions that finished.
function CC:LiveRefresh()
	local live = self.live
	if not live or live.broken or not self.db.settings.live then
		return 'none'
	end
	while live.next <= live.count and addonLoaded(self:SlotName(live.next)) do
		live.next = live.next + 1
	end
	if live.next > live.count then
		return 'none'
	end
	local name = self:SlotName(live.next)
	live.next = live.next + 1
	live.used = live.used + 1
	_G.ClaudeCode_Live = nil
	local loaded, reason = loadAddon(name)
	if not loaded and reason == 'DISABLED' then
		local enable = (C_AddOns and C_AddOns.EnableAddOn) or EnableAddOn
		if enable then
			pcall(enable, name)
		end
		loaded, reason = loadAddon(name)
	end
	local payload = _G.ClaudeCode_Live
	_G.ClaudeCode_Live = nil
	if not loaded then
		live.fails = live.fails + 1
		live.lastError = tostring(reason or 'not loaded')
		if live.fails >= 3 then
			live.broken = live.lastError
		end
		return 'failed'
	end
	live.fails = 0
	if type(payload) ~= 'table' or payload.schema ~= self.DATA_SCHEMA then
		return 'same'
	end
	local seq = tonumber(payload.seq) or 0
	if seq <= live.seq then
		return 'same'
	end
	live.seq = seq
	live.ok = true
	return 'new', self:MergeLive(payload)
end

--- True while something the player is waiting for is still out there.
function CC:IsWaiting()
	for id in pairs(self.sent) do
		for _, request in ipairs(self.db.outbox) do
			if request.id == id then
				return true
			end
		end
	end
	local data = self:GetData()
	if not data then
		return false
	end
	local config = data.config or {}
	if (config.running or '') ~= '' or (tonumber(config.queued) or 0) > 0 then
		return true
	end
	for _, session in pairs(data.sessions) do
		if session.status == 'running' then
			return true
		end
	end
	return false
end

function CC:IsPolling()
	return self.poll ~= nil and self.poll.active
end

--- Check the live slots on a slowing schedule until nothing is pending.
function CC:StartPolling(restart)
	if self:LiveLeft() <= 0 or not (C_Timer and C_Timer.After) then
		return false
	end
	if self:IsPolling() and not restart then
		return true
	end
	self.poll = { active = true, step = 0, quiet = 0 }
	self:SchedulePoll()
	return true
end

function CC:StopPolling()
	if self.poll then
		self.poll.active = false
	end
end

function CC:SchedulePoll()
	local poll = self.poll
	if not poll or not poll.active then
		return
	end
	poll.step = poll.step + 1
	local delay = self.POLL_STEPS[poll.step] or self.POLL_STEPS[#self.POLL_STEPS]
	C_Timer.After(delay, function()
		if self.poll == poll and poll.active then
			self:PollTick()
		end
	end)
end

function CC:PollTick()
	local poll = self.poll
	local result, finished = self:LiveRefresh()
	if result == 'new' then
		poll.quiet = 0
	else
		poll.quiet = poll.quiet + 1
	end
	if result == 'none' or (result == 'failed' and self.live.broken) or not self:IsWaiting() or poll.quiet >= self.POLL_GIVE_UP then
		poll.active = false
	end
	if self.OnLiveUpdate then
		self:OnLiveUpdate(result, finished)
	end
	if poll.active then
		self:SchedulePoll()
	end
end

--- One refresh the player asked for. With a live check left it reads the
--- newest data and never reloads; with none left it reloads the UI, which is
--- what the button says by then.
function CC:Refresh()
	if self:LiveLeft() <= 0 then
		self:Reload()
		return 'reload'
	end
	local result, finished = self:LiveRefresh()
	if result == 'failed' then
		self:Print(string.format('the live check failed (%s). %s', tostring(self.live.lastError or '?'),
			self.live.broken and 'Live answers are off until the next /reload.' or 'Press Refresh to try again.'))
	elseif self:IsWaiting() then
		self:StartPolling()
	end
	if self.OnLiveUpdate then
		self:OnLiveUpdate(result, finished)
	end
	return result
end

-- ------------------------------------------------------------------ sessions

function CC:Sessions()
	local data = self:GetData()
	local list = {}
	if data then
		for key, session in pairs(data.sessions) do
			session.key = session.key or key
			list[#list + 1] = session
		end
	end
	table.sort(list, function(a, b)
		return (a.updated_at or 0) > (b.updated_at or 0)
	end)
	return list
end

function CC:CurrentSession()
	local data = self:GetData()
	if data and self.ui.session and data.sessions[self.ui.session] then
		return data.sessions[self.ui.session]
	end
	return nil
end

function CC:StartNewSession()
	self.ui.session = string.format('s-%d', time())
	self.newSession = true
end

function CC:SendPrompt(text, forceMode)
	text = strtrim(text or '')
	if text == '' then
		return nil
	end
	local kind = 'prompt'
	if self.newSession or not self.ui.session then
		self:StartNewSession()
		kind = 'new'
	end
	local request = self:NewRequest(kind, self.ui.session, self.ui.project or '', self.ui.backend or '', text)
	self.newSession = false
	self:Dispatch(request, forceMode)
	return request
end

function CC:SendStop(forceMode)
	local request = self:NewRequest('stop', self.ui.session or '', '', '', '')
	self:Dispatch(request, forceMode)
	return request
end

-- ------------------------------------------------------------- slash commands

local function slashHandler(input)
	input = strtrim(input or '')
	local command, rest = input:match('^(%S*)%s*(.-)$')
	command = (command or ''):lower()
	if command == '' or command == 'toggle' or command == 'show' then
		CC:Toggle()
	elseif command == 'send' then
		if rest == '' then
			CC:Print('usage: /claude send <prompt>')
		else
			CC:SendPrompt(rest)
		end
	elseif command == 'copy' then
		if rest == '' then
			CC:Print('usage: /claude copy <prompt>')
		else
			CC:SendPrompt(rest, 'clipboard')
		end
	elseif command == 'new' then
		CC:StartNewSession()
		CC:Print('next prompt starts a new session.')
		if CC.Render then
			CC:Render()
		end
	elseif command == 'stop' then
		CC:SendStop()
	elseif command == 'refresh' then
		CC:Refresh()
	elseif command == 'reload' then
		CC:Reload()
	elseif command == 'minimap' then
		if rest == 'reset' then
			CC.ui.minimap_point = nil
			CC.db.settings.minimap = true
		else
			CC.db.settings.minimap = not CC.db.settings.minimap
		end
		if CC.PlaceMinimapButton then
			CC:PlaceMinimapButton()
		end
		CC:Print(CC.db.settings.minimap and 'minimap button shown. Drag it to move it; /claude minimap reset puts it back.'
			or 'minimap button hidden; /claude minimap shows it again.')
	elseif command == 'live' then
		if rest == 'on' or rest == 'off' then
			CC.db.settings.live = (rest == 'on')
			if not CC.db.settings.live then
				CC:StopPolling()
			end
		end
		local live = CC.live or {}
		CC:Print(string.format('live answers %s: %d slot(s) known to the client, %d left this session%s',
			CC.db.settings.live and 'on' or 'off', live.count or 0, CC:LiveLeft(),
			live.broken and ('; stopped after an error: ' .. tostring(live.broken)) or ''))
		if CC.Render then
			CC:Render()
		end
	elseif command == 'sound' then
		CC.db.settings.sound = not CC.db.settings.sound
		CC:Print('answer sound ' .. (CC.db.settings.sound and 'on' or 'off') .. '.')
	elseif command == 'status' then
		local age = CC:DataAge()
		CC:Print(string.format('version %s, %d queued request(s), companion data %s, send by %s, %d live refresh(es) left', CC.VERSION,
			CC:PendingCount(), age and (age .. 's old') or 'missing', CC:EffectiveSendMode(), CC:LiveLeft()))
		if CC.loadError then
			CC:Print(CC.loadError)
		end
	else
		CC:Print('commands: /claude [toggle|send <prompt>|copy <prompt>|new|stop|refresh|reload|status|live [on|off]|sound|minimap [reset]]')
	end
end

SLASH_CLAUDECODE1 = '/claude'
SLASH_CLAUDECODE2 = '/cc'
SlashCmdList.CLAUDECODE = slashHandler

-- ------------------------------------------------------------ bindings, compartment

BINDING_HEADER_CLAUDECODE = 'Claude Code'
BINDING_NAME_CLAUDECODE_TOGGLE = 'Toggle the Claude Code window'

function ClaudeCode_ToggleBinding()
	CC:Toggle()
end

function ClaudeCode_OnAddonCompartmentClick()
	CC:Toggle()
end

-- ------------------------------------------------------------------- loading

--- Build one part of the UI. The client hides Lua errors unless scriptErrors
--- is on, so a failure is kept and shown in chat instead of vanishing.
local function build(what, method)
	if not CC[method] then
		return true
	end
	local ok, err = pcall(CC[method], CC)
	if not ok then
		local line = string.format('%s failed to build: %s', what, tostring(err))
		CC.loadError = CC.loadError and (CC.loadError .. ' / ' .. line) or line
		if geterrorhandler then
			geterrorhandler()(err)
		end
	end
	return ok
end

local loader = CreateFrame('Frame')
loader:RegisterEvent('ADDON_LOADED')
loader:RegisterEvent('PLAYER_ENTERING_WORLD')
loader:SetScript('OnEvent', function(self, event, arg1, arg2)
	if event == 'ADDON_LOADED' then
		if arg1 ~= ADDON_NAME then
			return
		end
		self:UnregisterEvent('ADDON_LOADED')
		CC:InitDB()
		CC:InitLive()
		local removed = CC:PruneConsumed(CC:GetData())
		build('the minimap button', 'CreateMinimapButton')
		CC.windowBroken = not build('the window', 'CreateUI')
		if removed > 0 then
			CC:Print(string.format('%d request(s) picked up by the companion.', removed))
		end
	elseif event == 'PLAYER_ENTERING_WORLD' then
		-- Only the first one after the UI loads: arg1 is isInitialLogin, arg2 is
		-- isReloadingUi. Say how to open the window at login and the first time
		-- the addon ever runs; stay quiet on reloads.
		self:UnregisterEvent('PLAYER_ENTERING_WORLD')
		if not CC.db then
			return
		end
		if CC.loadError then
			CC:Print(CC.loadError)
		elseif not arg2 or not CC.db.intro_seen then
			CC.db.intro_seen = true
			CC:Print(string.format('v%s ready. Type /claude%s to open the window.', CC.VERSION,
				CC.db.settings.minimap and ' or click the minimap button' or ''))
		end
		-- A turn that was still running when the UI loaded keeps updating by itself.
		if CC:IsWaiting() then
			CC:StartPolling()
		end
	end
end)
