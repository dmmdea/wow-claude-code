-- Core.lua: saved state, the request outbox, the clipboard envelope, slash
-- commands and the handshake with the ClaudeCode_Data file the companion writes.
--
-- The addon never talks to anything outside the game. It stores requests in its
-- SavedVariables (written by the client on /reload) and reads answers from the
-- ClaudeCode_Data addon on the next /reload. Nothing here runs on a timer.

local ADDON_NAME = ...

ClaudeCode = ClaudeCode or {}
local CC = ClaudeCode

CC.ENVELOPE_PREFIX = 'CLAUDECODE-WOW:1:'
CC.DATA_SCHEMA = 1
CC.MAX_OUTBOX = 50
CC.VERSION = (C_AddOns and C_AddOns.GetAddOnMetadata and C_AddOns.GetAddOnMetadata(ADDON_NAME, 'Version'))
	or (GetAddOnMetadata and GetAddOnMetadata(ADDON_NAME, 'Version'))
	or '?'

local DEFAULTS = {
	version = 1,
	outbox = {},
	intro_seen = false,         -- the one-time "how to open me" chat line was shown
	settings = {
		auto_reload = true,     -- Send reloads the UI right away so the request leaves the game
		send_mode = 'auto',     -- auto | reload | clipboard
		width = 720,
		height = 500,
		minimap = true,         -- show the minimap button
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
		frame:AddMessage('|cff7ec8ffClaude Code:|r ' .. tostring(msg))
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
	return self.db
end

--- The table the companion wrote, or nil when it has not run yet.
function CC:GetData()
	local data = _G.ClaudeCode_Data
	if type(data) == 'table' and data.schema == self.DATA_SCHEMA and type(data.sessions) == 'table' then
		return data
	end
	return nil
end

function CC:DataAge()
	local data = self:GetData()
	if not data or not data.generated_at or data.generated_at == 0 then
		return nil
	end
	return time() - data.generated_at
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
--- Combat behaviour is not documented; the request stays queued until the next
--- reload the player triggers.
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
	if mode == 'clipboard' then
		if self.ShowEnvelope then
			self:ShowEnvelope(request)
		end
		return 'clipboard'
	end
	if self.db.settings.auto_reload then
		self:Reload()
	else
		self:Print('queued; press Refresh (or /reload) to send it to the companion.')
	end
	return 'reload'
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
	elseif command == 'reload' or command == 'refresh' then
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
	elseif command == 'status' then
		local age = CC:DataAge()
		CC:Print(string.format('version %s, %d queued request(s), companion data %s', CC.VERSION, CC:PendingCount(), age and (age .. 's old') or 'missing'))
		if CC.loadError then
			CC:Print(CC.loadError)
		end
	else
		CC:Print('commands: /claude [toggle|send <prompt>|copy <prompt>|new|stop|reload|status|minimap [reset]]')
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
		local removed = CC:PruneConsumed(CC:GetData())
		build('the minimap button', 'CreateMinimapButton')
		CC.windowBroken = not build('the window', 'CreateUI')
		if removed > 0 then
			CC:Print(string.format('%d request(s) picked up by the companion.', removed))
		end
	elseif event == 'PLAYER_ENTERING_WORLD' then
		-- Only the first one after the UI loads: arg1 is isInitialLogin, arg2 is
		-- isReloadingUi. Say how to open the window at login and the first time
		-- the addon ever runs; stay quiet on the reloads Send and Refresh cause.
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
	end
end)
