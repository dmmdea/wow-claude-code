-- UI.lua: the in-game window. Sessions on the left, transcript on the right,
-- prompt and controls at the bottom. Everything it shows comes from
-- ClaudeCode_Data; everything it sends goes through Core.lua.

local CC = ClaudeCode

local LEFT_WIDTH = 190
local PAD = 14
local SESSION_ROWS = 12
local ROLE_COLORS = {
	user = '|cff80c0ff',
	assistant = '|cffffffff',
	tool = '|cff9d9d9d',
	result = '|cff9d9d9d',
	error = '|cffff5050',
}
local ROLE_LABELS = {
	user = 'You',
	assistant = 'Claude',
	tool = '  > ',
	result = '  -- ',
	error = 'Error',
}

local function escapePipes(text)
	return (tostring(text or ''):gsub('|', '||'))
end

local function truncate(text, n)
	text = tostring(text or '')
	if #text > n then
		return text:sub(1, n - 3) .. '...'
	end
	return text
end

local function fmtAge(seconds)
	if not seconds then
		return 'never'
	end
	if seconds < 60 then
		return seconds .. 's ago'
	elseif seconds < 3600 then
		return math.floor(seconds / 60) .. 'm ago'
	end
	return math.floor(seconds / 3600) .. 'h ago'
end

local function cycle(list, current)
	if #list == 0 then
		return nil
	end
	for i, v in ipairs(list) do
		if v == current then
			return list[(i % #list) + 1]
		end
	end
	return list[1]
end

local function makeButton(parent, text, width, onClick)
	local b = CreateFrame('Button', nil, parent, 'UIPanelButtonTemplate')
	b:SetSize(width, 22)
	b:SetText(text)
	b:SetScript('OnClick', onClick)
	return b
end

local function makeEditBox(parent, name, multiLine)
	local box = CreateFrame('EditBox', name, parent)
	box:SetMultiLine(multiLine)
	box:SetAutoFocus(false)
	box:SetFontObject(ChatFontNormal)
	box:SetMaxLetters(0)
	box:EnableMouse(true)
	box:SetScript('OnEscapePressed', function(self)
		self:ClearFocus()
	end)
	return box
end

-- ------------------------------------------------------------------ building

function CC:CreateUI()
	if self.frame then
		return self.frame
	end
	local settings = self.db.settings
	local f = CreateFrame('Frame', 'ClaudeCodeFrame', UIParent, BackdropTemplateMixin and 'BackdropTemplate' or nil)
	self.frame = f
	f:SetSize(settings.width, settings.height)
	f:SetFrameStrata('DIALOG')
	f:SetMovable(true)
	f:EnableMouse(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag('LeftButton')
	f:SetScript('OnDragStart', f.StartMoving)
	f:SetScript('OnDragStop', function(frame)
		frame:StopMovingOrSizing()
		local point, _, relPoint, x, y = frame:GetPoint()
		self.ui.point = { point, relPoint, x, y }
	end)
	if f.SetBackdrop then
		f:SetBackdrop({
			bgFile = 'Interface\\DialogFrame\\UI-DialogBox-Background',
			edgeFile = 'Interface\\DialogFrame\\UI-DialogBox-Border',
			tile = true, tileSize = 32, edgeSize = 32,
			insets = { left = 11, right = 12, top = 12, bottom = 11 },
		})
	end
	local p = self.ui.point
	if type(p) == 'table' and p[1] then
		f:SetPoint(p[1], UIParent, p[2] or p[1], p[3] or 0, p[4] or 0)
	else
		f:SetPoint('CENTER')
	end
	f:Hide()
	tinsert(UISpecialFrames, 'ClaudeCodeFrame')
	f:SetScript('OnShow', function()
		self:Render()
	end)

	-- title
	local title = f:CreateFontString(nil, 'OVERLAY', 'GameFontNormal')
	title:SetPoint('TOPLEFT', PAD + 4, -PAD - 2)
	title:SetText('Claude Code  |cff9d9d9dv' .. escapePipes(self.VERSION) .. '|r')
	local close = CreateFrame('Button', nil, f, 'UIPanelCloseButton')
	close:SetPoint('TOPRIGHT', -6, -6)
	self.statusText = f:CreateFontString(nil, 'OVERLAY', 'GameFontHighlightSmall')
	self.statusText:SetPoint('TOPRIGHT', -36, -PAD - 4)
	self.statusText:SetJustifyH('RIGHT')

	-- left: sessions
	local newButton = makeButton(f, 'New session', LEFT_WIDTH - 10, function()
		self:StartNewSession()
		self:Render()
		self.promptBox:SetFocus()
	end)
	newButton:SetPoint('TOPLEFT', PAD, -PAD - 24)
	self.sessionButtons = {}
	for i = 1, SESSION_ROWS do
		local b = makeButton(f, '', LEFT_WIDTH - 10, function(button)
			self.ui.session = button.sessionKey
			self.newSession = false
			self:Render()
		end)
		b:SetPoint('TOPLEFT', PAD, -PAD - 24 - i * 24)
		b:GetFontString():SetJustifyH('LEFT')
		b:GetFontString():SetPoint('LEFT', 6, 0)
		b:GetFontString():SetPoint('RIGHT', -6, 0)
		b:SetScript('OnEnter', function(button)
			if button.tooltip then
				GameTooltip:SetOwner(button, 'ANCHOR_RIGHT')
				GameTooltip:SetText(button.tooltip, 1, 1, 1, 1, true)
				GameTooltip:Show()
			end
		end)
		b:SetScript('OnLeave', function()
			GameTooltip:Hide()
		end)
		b:Hide()
		self.sessionButtons[i] = b
	end

	-- right: transcript
	local scroll = CreateFrame('ScrollFrame', 'ClaudeCodeTranscriptScroll', f, 'UIPanelScrollFrameTemplate')
	scroll:SetPoint('TOPLEFT', f, 'TOPLEFT', PAD + LEFT_WIDTH, -PAD - 26)
	scroll:SetPoint('BOTTOMRIGHT', f, 'BOTTOMRIGHT', -PAD - 22, PAD + 96)
	local transcript = makeEditBox(scroll, 'ClaudeCodeTranscript', true)
	transcript:SetWidth(settings.width - LEFT_WIDTH - PAD * 2 - 30)
	transcript:SetScript('OnTextChanged', function(box, userInput)
		if userInput then
			box:SetText(self.transcriptText or '')
		end
	end)
	scroll:SetScrollChild(transcript)
	self.transcriptBox = transcript
	self.transcriptScroll = scroll

	-- bottom: prompt
	local promptScroll = CreateFrame('ScrollFrame', 'ClaudeCodePromptScroll', f, 'UIPanelScrollFrameTemplate')
	promptScroll:SetPoint('BOTTOMLEFT', f, 'BOTTOMLEFT', PAD + LEFT_WIDTH, PAD + 38)
	promptScroll:SetPoint('BOTTOMRIGHT', f, 'BOTTOMRIGHT', -PAD - 22, PAD + 38)
	promptScroll:SetHeight(52)
	local prompt = makeEditBox(promptScroll, 'ClaudeCodePrompt', true)
	prompt:SetWidth(settings.width - LEFT_WIDTH - PAD * 2 - 30)
	prompt:SetScript('OnEnterPressed', function(box)
		if IsControlKeyDown and IsControlKeyDown() then
			self:OnSend()
		else
			box:Insert('\n')
		end
	end)
	promptScroll:SetScrollChild(prompt)
	self.promptBox = prompt
	local hint = f:CreateFontString(nil, 'OVERLAY', 'GameFontHighlightSmall')
	hint:SetPoint('BOTTOMLEFT', promptScroll, 'TOPLEFT', 0, 2)
	hint:SetText('|cff9d9d9dPrompt (Ctrl+Enter sends)|r')

	-- bottom: controls
	local x = PAD + LEFT_WIDTH
	local y = PAD + 10
	self.projectButton = makeButton(f, 'Project', 150, function()
		local data = self:GetData()
		local list = data and data.config and data.config.projects or {}
		self.ui.project = cycle(list, self.ui.project)
		self:Render()
	end)
	self.projectButton:SetPoint('BOTTOMLEFT', x, y)
	self.backendButton = makeButton(f, 'Backend', 150, function()
		local data = self:GetData()
		local list = data and data.config and data.config.backends or {}
		self.ui.backend = cycle(list, self.ui.backend)
		self:Render()
	end)
	self.backendButton:SetPoint('BOTTOMLEFT', x + 154, y)
	local send = makeButton(f, 'Send', 60, function()
		self:OnSend()
	end)
	send:SetPoint('BOTTOMLEFT', x + 308, y)
	local copy = makeButton(f, 'Copy', 60, function()
		self:OnSend('clipboard')
	end)
	copy:SetPoint('BOTTOMLEFT', x + 372, y)
	local stop = makeButton(f, 'Stop', 60, function()
		self:SendStop()
	end)
	stop:SetPoint('BOTTOMLEFT', x + 436, y)
	local refresh = makeButton(f, 'Refresh', 70, function()
		self:Reload()
	end)
	refresh:SetPoint('BOTTOMLEFT', x + 500, y)
	self.footerText = f:CreateFontString(nil, 'OVERLAY', 'GameFontHighlightSmall')
	self.footerText:SetPoint('BOTTOMLEFT', PAD + 4, PAD - 2)
	self.footerText:SetPoint('BOTTOMRIGHT', -PAD, PAD - 2)
	self.footerText:SetJustifyH('LEFT')

	self:CreateEnvelopeFrame()
	return f
end

--- A small panel holding the envelope line, focused and fully selected so one
--- Ctrl+C puts it on the clipboard for the companion's watcher.
function CC:CreateEnvelopeFrame()
	local e = CreateFrame('Frame', 'ClaudeCodeEnvelopeFrame', self.frame, BackdropTemplateMixin and 'BackdropTemplate' or nil)
	e:SetSize(520, 92)
	e:SetPoint('CENTER')
	e:SetFrameStrata('TOOLTIP')
	if e.SetBackdrop then
		e:SetBackdrop({
			bgFile = 'Interface\\DialogFrame\\UI-DialogBox-Background',
			edgeFile = 'Interface\\DialogFrame\\UI-DialogBox-Border',
			tile = true, tileSize = 32, edgeSize = 24,
			insets = { left = 8, right = 8, top = 8, bottom = 8 },
		})
	end
	local label = e:CreateFontString(nil, 'OVERLAY', 'GameFontNormal')
	label:SetPoint('TOP', 0, -14)
	label:SetText('Press Ctrl+C to send, then Escape')
	-- Multi-line on purpose: copying from a multi-line EditBox is the pattern
	-- every "copy this export string" addon relies on.
	local box = makeEditBox(e, 'ClaudeCodeEnvelopeBox', true)
	box:SetPoint('TOPLEFT', 16, -36)
	box:SetWidth(488)
	box:SetHeight(40)
	box:SetScript('OnEnterPressed', function(b)
		b:ClearFocus()
		e:Hide()
	end)
	box:SetScript('OnEscapePressed', function(b)
		b:ClearFocus()
		e:Hide()
	end)
	box:SetScript('OnTextChanged', function(b, userInput)
		if userInput then
			b:SetText(self.envelopeText or '')
			b:HighlightText()
		end
	end)
	box:SetScript('OnEditFocusLost', function()
		e:Hide()
	end)
	e:Hide()
	self.envelopeFrame = e
	self.envelopeBox = box
end

function CC:ShowEnvelope(request)
	self.envelopeText = self:Envelope(request)
	self.envelopeFrame:Show()
	self.envelopeBox:SetText(self.envelopeText)
	self.envelopeBox:SetFocus()
	self.envelopeBox:HighlightText()
	self:Render()
end

function CC:OnSend(forceMode)
	local text = self.promptBox:GetText()
	if strtrim(text or '') == '' then
		self:Print('type a prompt first.')
		return
	end
	self.promptBox:SetText('')
	self.promptBox:ClearFocus()
	self:SendPrompt(text, forceMode)
	self:Render()
end

-- ----------------------------------------------------------------- rendering

function CC:TranscriptFor(session)
	if not session then
		if self.newSession then
			return '|cff9d9d9dNew session. Type a prompt below and press Send.|r'
		end
		local data = self:GetData()
		if not data then
			return '|cff9d9d9dNo companion data yet.\n\nOn your PC, run:\n  python -m wowclaude run\n\nthen press Refresh.|r'
		end
		return '|cff9d9d9dPick a session on the left, or start a new one.|r'
	end
	local lines = {}
	local header = string.format('%s  |cff9d9d9d[%s | %s | %s | $%.2f]|r', escapePipes(session.title or session.key),
		escapePipes(session.backend or '?'), escapePipes(session.project or '-'), escapePipes(session.status or '?'),
		tonumber(session.cost_usd) or 0)
	lines[#lines + 1] = header
	for _, entry in ipairs(session.entries or {}) do
		local role = entry.role or 'assistant'
		local color = ROLE_COLORS[role] or '|cffffffff'
		local label = ROLE_LABELS[role] or role
		if role == 'tool' or role == 'result' then
			lines[#lines + 1] = color .. label .. escapePipes(entry.text) .. '|r'
		else
			lines[#lines + 1] = color .. label .. ':|r ' .. escapePipes(entry.text)
		end
	end
	if session.status == 'running' then
		if session.partial and session.partial ~= '' then
			lines[#lines + 1] = '|cffffff80(streaming)|r ' .. escapePipes(session.partial)
		end
		lines[#lines + 1] = '|cffffff80... running on the companion; press Refresh for progress.|r'
	end
	return table.concat(lines, '\n\n')
end

function CC:Render()
	if not self.frame then
		return
	end
	local data = self:GetData()
	local sessions = self:Sessions()
	if not self.ui.session and not self.newSession and sessions[1] then
		self.ui.session = sessions[1].key
	end
	for i, b in ipairs(self.sessionButtons) do
		local s = sessions[i]
		if s then
			local mark = (s.key == self.ui.session) and '> ' or ''
			b:SetText(mark .. escapePipes(truncate(s.title or s.key, 24)))
			b.sessionKey = s.key
			b.tooltip = string.format('%s\nbackend: %s\nproject: %s\nstatus: %s\ncost: $%.4f', s.title or s.key,
				s.backend or '?', s.project or '-', s.status or '?', tonumber(s.cost_usd) or 0)
			b:Show()
		else
			b:Hide()
		end
	end
	local session = (not self.newSession) and self:CurrentSession() or nil
	self.transcriptText = self:TranscriptFor(session)
	self.transcriptBox:SetText(self.transcriptText)
	self.transcriptScroll:UpdateScrollChildRect()

	local projects = data and data.config and data.config.projects or {}
	local backends = data and data.config and data.config.backends or {}
	if not self.ui.project or (#projects > 0 and not cycle(projects, nil)) then
		self.ui.project = self.ui.project or (data and data.config and data.config.default_project) or nil
	end
	if not self.ui.backend then
		self.ui.backend = (data and data.config and data.config.default_backend) or nil
	end
	self.projectButton:SetText('Project: ' .. escapePipes(truncate(self.ui.project or (#projects == 0 and '(none)' or '?'), 16)))
	self.backendButton:SetText('Backend: ' .. escapePipes(truncate(self.ui.backend or (#backends == 0 and '(none)' or '?'), 14)))

	local age = self:DataAge()
	local running = data and data.config and data.config.running ~= '' and data.config.running or nil
	self.statusText:SetText(string.format('|cff9d9d9dcompanion data %s%s|r', fmtAge(age), running and ' | running' or ''))
	local mode = self:EffectiveSendMode()
	self.footerText:SetText(string.format('|cff9d9d9d%d queued | send by %s | /claude for commands|r', self:PendingCount(),
		mode == 'clipboard' and 'clipboard (Ctrl+C)' or 'reload'))
end

function CC:Toggle()
	if not self.frame then
		return
	end
	if self.frame:IsShown() then
		self.frame:Hide()
	else
		self.frame:Show()
	end
end
