-- UI.lua: the in-game window. A flat dark panel with the chats on the left, the
-- transcript on the right and the message box under it. Everything it shows
-- comes from the companion's data; everything it sends goes through Core.lua.
-- Only solid colors and the client's own fonts are used, so it looks the same
-- on every client flavor.

local CC = ClaudeCode

local MIN_W, MIN_H, MAX_W, MAX_H = 620, 400, 1400, 1000
local HEAD_H, SIDE_W, PAD = 30, 176, 12
local ROW_H, ROW_GAP, ROWS_TOP = 24, 2, 44
local INPUT_H, FOOT_H, STATUS_H = 62, 34, 18
local SEND_W = 58
-- A very long transcript in one text box stalls the client, so only the newest
-- part is drawn.
local TRANSCRIPT_MAX, ENTRY_MAX = 40000, 12000

local COLOR = {
	bg = { 0.059, 0.063, 0.075 },
	side = { 0.082, 0.086, 0.102 },
	head = { 0.106, 0.110, 0.129 },
	line = { 0.200, 0.208, 0.240 },
	field = { 0.035, 0.039, 0.047 },
	hover = { 0.149, 0.157, 0.184 },
	pick = { 0.176, 0.145, 0.133 },
	accent = { 0.851, 0.467, 0.341 },
	accentHover = { 0.925, 0.565, 0.439 },
	ink = { 0.086, 0.059, 0.051 },
	text = { 0.902, 0.906, 0.922 },
	dim = { 0.545, 0.557, 0.600 },
	good = { 0.400, 0.800, 0.520 },
	warn = { 0.953, 0.753, 0.318 },
	bad = { 0.949, 0.467, 0.478 },
}
-- the same palette as color escapes for the transcript
local HEX = {
	text = 'ffe6e7eb',
	you = 'ff8ab4f8',
	assistant = 'ffd97757',
	dim = 'ff8b8e99',
	faint = 'ff6b6e78',
	bad = 'fff2777a',
	warn = 'fff3c051',
	head = 'fff0b392',
	bold = 'ffffffff',
	code = 'ff9ccfb8',
}

local function esc(text)
	return (tostring(text or ''):gsub('|', '||'))
end

local function paintText(hex, text)
	return '|c' .. hex .. text .. '|r'
end

local function truncate(text, n)
	text = tostring(text or '')
	if #text > n then
		return text:sub(1, n - 3) .. '...'
	end
	return text
end

--- The last n bytes of a text, never starting inside a UTF-8 sequence.
local function tail(text, n)
	if #text <= n then
		return text
	end
	local start = #text - n + 1
	while start <= #text do
		local byte = text:byte(start)
		if byte < 128 or byte >= 192 then
			break
		end
		start = start + 1
	end
	return '[...] ' .. text:sub(start)
end

local function cycle(list, current)
	if type(list) ~= 'table' or #list == 0 then
		return nil
	end
	for i, v in ipairs(list) do
		if v == current then
			return list[(i % #list) + 1]
		end
	end
	return list[1]
end

local function clamp(value, low, high)
	if value < low then
		return low
	end
	if value > high then
		return high
	end
	return value
end

--- Light markdown for answers: headings, bold, inline code and code blocks get
--- a color and lose their markers. Everything else is shown as written.
local function formatInline(text)
	text = text:gsub('%*%*(.-)%*%*', '|c' .. HEX.bold .. '%1|r')
	text = text:gsub('`([^`]+)`', '|c' .. HEX.code .. '%1|r')
	return text
end

local function formatAnswer(text)
	text = tostring(text or ''):gsub('%s+$', '')
	local out, inCode = {}, false
	for raw in (text .. '\n'):gmatch('([^\n]*)\n') do
		local row = raw:gsub('\r$', '')
		if row:match('^%s*```') then
			inCode = not inCode
		elseif inCode then
			out[#out + 1] = paintText(HEX.code, esc(row))
		else
			local title = row:match('^#+%s+(.+)$')
			if title then
				out[#out + 1] = paintText(HEX.head, esc((title:gsub('%*%*', ''))))
			else
				out[#out + 1] = formatInline(esc(row))
			end
		end
	end
	return table.concat(out, '\n')
end

-- ------------------------------------------------------------------ primitives

local function setColor(texture, c, alpha)
	texture:SetColorTexture(c[1], c[2], c[3], alpha or 1)
end

local function tex(parent, layer, c, alpha)
	local t = parent:CreateTexture(nil, layer)
	setColor(t, c, alpha)
	return t
end

local function paint(frame, c, alpha)
	local t = tex(frame, 'BACKGROUND', c, alpha)
	t:SetAllPoints(frame)
	frame.fill = t
	return t
end

-- Thin lines are sized to exactly one screen pixel, so they stay sharp at any
-- UI scale. Each is remembered and resized when the window is laid out.
local HAIRLINES = {}

local function onePixel()
	if PixelUtil and PixelUtil.GetNearestPixelSize and UIParent.GetEffectiveScale then
		local scale = UIParent:GetEffectiveScale()
		if scale and scale > 0 then
			return PixelUtil.GetNearestPixelSize(1, scale, 1)
		end
	end
	return 1
end

local function hairline(parent, c, horizontal)
	local t = tex(parent, 'BORDER', c)
	if horizontal then
		t:SetHeight(1)
	else
		t:SetWidth(1)
	end
	HAIRLINES[#HAIRLINES + 1] = { t, horizontal }
	return t
end

local function refreshHairlines()
	local size = onePixel()
	for _, entry in ipairs(HAIRLINES) do
		if entry[2] then
			entry[1]:SetHeight(size)
		else
			entry[1]:SetWidth(size)
		end
	end
end

--- A one pixel border made of four solid lines.
local function outline(frame, c)
	local edges = {}
	local specs = { { 'TOPLEFT', 'TOPRIGHT' }, { 'BOTTOMLEFT', 'BOTTOMRIGHT' }, { 'TOPLEFT', 'BOTTOMLEFT' }, { 'TOPRIGHT', 'BOTTOMRIGHT' } }
	for i, spec in ipairs(specs) do
		local t = hairline(frame, c, i <= 2)
		t:SetPoint(spec[1], frame, spec[1], 0, 0)
		t:SetPoint(spec[2], frame, spec[2], 0, 0)
		edges[i] = t
	end
	frame.edges = edges
	return edges
end

local function recolor(edges, c)
	for _, t in ipairs(edges) do
		setColor(t, c)
	end
end

local function label(parent, font, c, justify)
	local fs = parent:CreateFontString(nil, 'OVERLAY', font or 'GameFontHighlightSmall')
	fs:SetTextColor(c[1], c[2], c[3])
	fs:SetJustifyH(justify or 'LEFT')
	fs:SetWordWrap(false)
	return fs
end

local function showTip(owner, anchor)
	if not owner.tip or owner.tip == '' then
		return
	end
	GameTooltip:SetOwner(owner, anchor or 'ANCHOR_TOP')
	GameTooltip:ClearLines()
	GameTooltip:AddLine(owner.tip, 1, 1, 1, true)
	GameTooltip:Show()
end

--- A flat button. kind: 'accent' (filled with the accent color), 'ghost'
--- (no fill until hovered) or nil (a quiet outlined button).
local function flatButton(parent, text, width, height, kind)
	local b = CreateFrame('Button', nil, parent)
	b:SetSize(width, height)
	b.kind = kind
	b.fill = b:CreateTexture(nil, 'BACKGROUND')
	b.fill:SetAllPoints(b)
	outline(b, COLOR.line)
	b.label = label(b, 'GameFontHighlightSmall', COLOR.text, 'CENTER')
	b.label:SetPoint('LEFT', b, 'LEFT', 6, 0)
	b.label:SetPoint('RIGHT', b, 'RIGHT', -6, 0)
	b.label:SetText(text or '')
	function b:Paint()
		local ink = COLOR.text
		if self.kind == 'accent' then
			setColor(self.fill, self.over and COLOR.accentHover or COLOR.accent)
			ink = COLOR.ink
		elseif self.kind == 'ghost' then
			setColor(self.fill, self.hoverColor or COLOR.hover, self.over and 1 or 0)
		else
			setColor(self.fill, self.over and COLOR.hover or COLOR.head)
		end
		self.label:SetTextColor(ink[1], ink[2], ink[3])
		for _, edge in ipairs(self.edges) do
			edge:SetShown(not self.kind)
		end
	end
	b:SetScript('OnEnter', function(self)
		self.over = true
		self:Paint()
		showTip(self)
	end)
	b:SetScript('OnLeave', function(self)
		self.over = false
		self:Paint()
		GameTooltip:Hide()
	end)
	b:Paint()
	return b
end

--- A text box inside a scroll frame is as tall as its text. Its clickable area
--- is cut to the part that shows, and stretched over the whole view when the
--- text is shorter than the view.
local function fitHitRect(scroll, box)
	local offset = scroll:GetVerticalScroll() or 0
	box:SetHitRectInsets(0, 0, offset, (box:GetHeight() or 0) - offset - (scroll:GetHeight() or 0))
end

local function paintRow(row)
	if row.picked then
		setColor(row.fill, COLOR.pick, 1)
	else
		setColor(row.fill, COLOR.hover, row.over and 1 or 0)
	end
	row.mark:SetShown(row.picked and true or false)
end

-- ------------------------------------------------------------------ building

function CC:CreateUI()
	if self.frame then
		return self.frame
	end
	local settings = self.db.settings
	local f = CreateFrame('Frame', 'ClaudeCodeFrame', UIParent)
	self.frame = f
	f:SetSize(clamp(tonumber(settings.width) or 760, MIN_W, MAX_W), clamp(tonumber(settings.height) or 520, MIN_H, MAX_H))
	f:SetFrameStrata('HIGH')
	f:SetToplevel(true)
	f:SetClampedToScreen(true)
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetResizable(true)
	if f.SetResizeBounds then
		f:SetResizeBounds(MIN_W, MIN_H, MAX_W, MAX_H)
	elseif f.SetMinResize then
		f:SetMinResize(MIN_W, MIN_H)
	end
	paint(f, COLOR.bg, 0.97)
	outline(f, COLOR.line)
	local p = self.ui.point
	if type(p) == 'table' and p[1] then
		f:SetPoint(p[1], UIParent, p[2] or p[1], p[3] or 0, p[4] or 0)
	else
		f:SetPoint('CENTER', UIParent, 'CENTER', 0, 0)
	end
	f:Hide()
	tinsert(UISpecialFrames, 'ClaudeCodeFrame')

	local function savePlace()
		local point, _, relPoint, x, y = f:GetPoint()
		self.ui.point = { point, relPoint, x, y }
		settings.width = math.floor(f:GetWidth() + 0.5)
		settings.height = math.floor(f:GetHeight() + 0.5)
	end

	-- header: drag handle, title, companion status, close
	local head = CreateFrame('Frame', nil, f)
	head:SetPoint('TOPLEFT', f, 'TOPLEFT', 1, -1)
	head:SetPoint('TOPRIGHT', f, 'TOPRIGHT', -1, -1)
	head:SetHeight(HEAD_H)
	paint(head, COLOR.head)
	head:EnableMouse(true)
	head:RegisterForDrag('LeftButton')
	head:SetScript('OnDragStart', function()
		f:StartMoving()
	end)
	head:SetScript('OnDragStop', function()
		f:StopMovingOrSizing()
		savePlace()
	end)
	local logo = tex(head, 'ARTWORK', COLOR.accent)
	logo:SetSize(10, 10)
	logo:SetPoint('LEFT', head, 'LEFT', 12, 0)
	local title = label(head, 'GameFontNormal', COLOR.text)
	title:SetPoint('LEFT', logo, 'RIGHT', 8, 0)
	title:SetText('Claude Code')
	local close = flatButton(head, 'x', 24, 20, 'ghost')
	close.hoverColor = COLOR.bad
	close:SetPoint('RIGHT', head, 'RIGHT', -5, 0)
	close:SetScript('OnClick', function()
		f:Hide()
	end)
	self.statusDot = tex(head, 'ARTWORK', COLOR.dim)
	self.statusDot:SetSize(8, 8)
	self.statusDot:SetPoint('RIGHT', close, 'LEFT', -8, 0)
	self.statusText = label(head, 'GameFontHighlightSmall', COLOR.dim, 'RIGHT')
	self.statusText:SetPoint('RIGHT', self.statusDot, 'LEFT', -6, 0)
	self.statusText:SetPoint('LEFT', title, 'RIGHT', 16, 0)

	-- left: the chats
	local side = CreateFrame('Frame', nil, f)
	side:SetPoint('TOPLEFT', head, 'BOTTOMLEFT', 0, 0)
	side:SetPoint('BOTTOMLEFT', f, 'BOTTOMLEFT', 1, 1)
	side:SetWidth(SIDE_W)
	paint(side, COLOR.side)
	local divider = hairline(side, COLOR.line, false)
	divider:SetPoint('TOPRIGHT', side, 'TOPRIGHT', 0, 0)
	divider:SetPoint('BOTTOMRIGHT', side, 'BOTTOMRIGHT', 0, 0)
	self.side = side
	self.rows = {}
	self.rowOffset = 0
	local newChat = flatButton(side, '+  New chat', SIDE_W - 16, 26)
	newChat:SetPoint('TOPLEFT', side, 'TOPLEFT', 8, -9)
	newChat:SetScript('OnClick', function()
		self:StartNewSession()
		self.stickBottom = true
		self:Render()
		self.promptBox:SetFocus()
	end)
	side:EnableMouseWheel(true)
	side:SetScript('OnMouseWheel', function(_, delta)
		local extra = math.max(0, #self:Sessions() - (self.rowsVisible or 1))
		self.rowOffset = clamp((self.rowOffset or 0) - delta, 0, extra)
		self:RenderSessions()
	end)

	-- right: transcript, guidance line, message box, controls
	local main = CreateFrame('Frame', nil, f)
	main:SetPoint('TOPLEFT', side, 'TOPRIGHT', 0, 0)
	main:SetPoint('BOTTOMRIGHT', f, 'BOTTOMRIGHT', -1, 1)
	self.main = main

	local tscroll = CreateFrame('ScrollFrame', 'ClaudeCodeTranscriptScroll', main)
	tscroll:SetPoint('TOPLEFT', main, 'TOPLEFT', PAD, -PAD)
	tscroll:SetPoint('BOTTOMRIGHT', main, 'BOTTOMRIGHT', -(PAD + 8), FOOT_H + INPUT_H + STATUS_H + 10)
	local transcript = CreateFrame('EditBox', 'ClaudeCodeTranscript', tscroll)
	transcript:SetMultiLine(true)
	transcript:SetAutoFocus(false)
	transcript:SetFontObject(ChatFontNormal)
	transcript:SetMaxLetters(0)
	transcript:EnableMouse(true)
	transcript:SetTextColor(COLOR.text[1], COLOR.text[2], COLOR.text[3])
	if transcript.SetSpacing then
		transcript:SetSpacing(3)
	end
	transcript:SetWidth(400)
	transcript:SetScript('OnEscapePressed', function(box)
		box:ClearFocus()
	end)
	-- read-only: the box exists so text can be selected and copied
	transcript:SetScript('OnTextChanged', function(box, userInput)
		if userInput then
			box:SetText(self.transcriptText or '')
		end
	end)
	-- after a copy the keyboard goes back to the game
	transcript:SetScript('OnKeyDown', function(box, key)
		if key == 'C' and ((IsControlKeyDown and IsControlKeyDown()) or (IsMetaKeyDown and IsMetaKeyDown())) then
			C_Timer.After(0.05, function()
				box:ClearFocus()
			end)
		end
	end)
	tscroll:SetScrollChild(transcript)
	tscroll:EnableMouseWheel(true)
	tscroll:SetScript('OnMouseWheel', function(scroll, delta)
		local range = scroll:GetVerticalScrollRange() or 0
		local offset = clamp((scroll:GetVerticalScroll() or 0) - delta * 48, 0, math.max(0, range))
		scroll:SetVerticalScroll(offset)
		self.stickBottom = offset >= range - 2
		self:UpdateThumb()
	end)
	tscroll:SetScript('OnScrollRangeChanged', function(scroll, _, yrange)
		if self.stickBottom then
			scroll:SetVerticalScroll(math.max(0, yrange or scroll:GetVerticalScrollRange() or 0))
		end
		fitHitRect(scroll, transcript)
		self:UpdateThumb()
	end)
	tscroll:SetScript('OnVerticalScroll', function(scroll)
		fitHitRect(scroll, transcript)
		self:UpdateThumb()
	end)
	self.transcriptBox = transcript
	self.tscroll = tscroll
	self.stickBottom = true
	self.thumb = tex(main, 'ARTWORK', COLOR.line)
	self.thumb:SetWidth(3)
	self.thumb:Hide()

	-- one line of guidance between the transcript and the message box
	local status = CreateFrame('Frame', nil, main)
	status:SetPoint('BOTTOMLEFT', main, 'BOTTOMLEFT', PAD, FOOT_H + INPUT_H + 4)
	status:SetPoint('BOTTOMRIGHT', main, 'BOTTOMRIGHT', -PAD, FOOT_H + INPUT_H + 4)
	status:SetHeight(STATUS_H)
	status.fill = tex(status, 'BACKGROUND', COLOR.accent, 0)
	status.fill:SetAllPoints(status)
	status:EnableMouse(true)
	status:SetScript('OnEnter', function(strip)
		showTip(strip, 'ANCHOR_TOPLEFT')
	end)
	status:SetScript('OnLeave', function()
		GameTooltip:Hide()
	end)
	self.hint = label(status, 'GameFontHighlightSmall', COLOR.dim)
	self.hint:SetPoint('LEFT', status, 'LEFT', 6, 0)
	self.hint:SetPoint('RIGHT', status, 'RIGHT', -6, 0)
	self.statusLine = status

	-- the message box
	local input = CreateFrame('Frame', nil, main)
	input:SetPoint('BOTTOMLEFT', main, 'BOTTOMLEFT', PAD, FOOT_H)
	input:SetPoint('BOTTOMRIGHT', main, 'BOTTOMRIGHT', -PAD, FOOT_H)
	input:SetHeight(INPUT_H)
	paint(input, COLOR.field)
	outline(input, COLOR.line)
	self.inputBox = input

	local pscroll = CreateFrame('ScrollFrame', nil, input)
	pscroll:SetPoint('TOPLEFT', input, 'TOPLEFT', 8, -7)
	pscroll:SetPoint('BOTTOMRIGHT', input, 'BOTTOMRIGHT', -(SEND_W + 14), 7)
	local prompt = CreateFrame('EditBox', 'ClaudeCodePrompt', pscroll)
	prompt:SetMultiLine(true)
	prompt:SetAutoFocus(false)
	prompt:SetFontObject(ChatFontNormal)
	prompt:SetMaxLetters(0)
	prompt:EnableMouse(true)
	prompt:SetTextColor(COLOR.text[1], COLOR.text[2], COLOR.text[3])
	prompt:SetWidth(300)
	pscroll:SetScrollChild(prompt)
	self.placeholder = label(input, 'GameFontHighlightSmall', COLOR.dim)
	self.placeholder:SetPoint('TOPLEFT', input, 'TOPLEFT', 10, -10)
	self.placeholder:SetText('Message Claude.  Enter sends, Shift+Enter adds a line.')
	prompt:SetScript('OnEscapePressed', function(box)
		box:ClearFocus()
	end)
	prompt:SetScript('OnEnterPressed', function(box)
		if IsShiftKeyDown and IsShiftKeyDown() then
			box:Insert('\n')
		else
			self:OnSend()
		end
	end)
	prompt:SetScript('OnTextChanged', function(box)
		self.placeholder:SetShown(not self.copying and box:GetText() == '')
	end)
	-- keep the cursor line inside the visible part of the box
	prompt:SetScript('OnCursorChanged', function(_, _, y, _, lineHeight)
		local top = -(y or 0)
		local view = pscroll:GetHeight() or 0
		local offset = pscroll:GetVerticalScroll() or 0
		if top < offset then
			pscroll:SetVerticalScroll(top)
		elseif top + (lineHeight or 0) > offset + view then
			pscroll:SetVerticalScroll(top + (lineHeight or 0) - view)
		end
	end)
	prompt:SetScript('OnEditFocusGained', function()
		recolor(input.edges, COLOR.accent)
	end)
	prompt:SetScript('OnEditFocusLost', function()
		recolor(input.edges, COLOR.line)
	end)
	pscroll:SetScript('OnScrollRangeChanged', function(scroll)
		fitHitRect(scroll, prompt)
	end)
	pscroll:SetScript('OnVerticalScroll', function(scroll)
		fitHitRect(scroll, prompt)
	end)
	-- a click anywhere in the box puts the cursor in it
	input:EnableMouse(true)
	input:SetScript('OnMouseDown', function()
		if self.copying then
			self.envelopeBox:SetFocus()
			self.envelopeBox:HighlightText()
		else
			prompt:SetFocus()
		end
	end)
	self.promptBox = prompt
	self.pscroll = pscroll

	-- While a request waits for Ctrl+C the box shows the message on top and,
	-- under it, the one selected line that Ctrl+C copies.
	self.staged = label(input, 'GameFontHighlightSmall', COLOR.text)
	self.staged:SetPoint('TOPLEFT', input, 'TOPLEFT', 10, -8)
	self.staged:SetJustifyV('TOP')
	self.staged:SetWordWrap(true)
	if self.staged.SetMaxLines then
		self.staged:SetMaxLines(2)
	end
	self.staged:SetSize(300, 26)
	self.staged:Hide()
	local envelope = CreateFrame('EditBox', 'ClaudeCodeEnvelopeBox', input)
	envelope:SetAutoFocus(false)
	envelope:SetFontObject(GameFontHighlightSmall)
	envelope:SetMaxLetters(0)
	if envelope.SetMaxBytes then
		envelope:SetMaxBytes(0)
	end
	envelope:EnableMouse(true)
	envelope:SetTextColor(COLOR.dim[1], COLOR.dim[2], COLOR.dim[3])
	envelope:SetHeight(16)
	envelope:SetPoint('BOTTOMLEFT', input, 'BOTTOMLEFT', 10, 7)
	envelope:SetPoint('BOTTOMRIGHT', input, 'BOTTOMRIGHT', -(SEND_W + 16), 7)
	envelope:Hide()
	envelope:SetScript('OnKeyDown', function(_, key)
		if key == 'C' and ((IsControlKeyDown and IsControlKeyDown()) or (IsMetaKeyDown and IsMetaKeyDown())) then
			-- The client copies on this same key press. Taking the focus away
			-- in the same frame would leave the clipboard empty, so wait a moment.
			C_Timer.After(0.05, function()
				self:OnCopied()
			end)
		end
	end)
	envelope:SetScript('OnTextChanged', function(box, userInput)
		if userInput and self.copying then
			box:SetText(self.envelopeText or '')
			box:SetCursorPosition(0)
			box:HighlightText()
		end
	end)
	envelope:SetScript('OnEnterPressed', function(box)
		box:HighlightText()
	end)
	envelope:SetScript('OnEscapePressed', function()
		self:CancelCopy()
	end)
	envelope:SetScript('OnEditFocusLost', function()
		if self.copying then
			self:CancelCopy()
		end
	end)
	self.envelopeBox = envelope

	local send = flatButton(input, 'Send', SEND_W, 24, 'accent')
	send:SetPoint('BOTTOMRIGHT', input, 'BOTTOMRIGHT', -7, 7)
	send:SetScript('OnClick', function()
		if self.copying then
			self:CancelCopy()
		else
			self:OnSend()
		end
	end)
	self.sendButton = send

	-- controls under the message box
	self.projectChip = flatButton(main, '', 120, 20)
	self.projectChip:SetPoint('BOTTOMLEFT', main, 'BOTTOMLEFT', PAD, 7)
	self.projectChip.label:SetJustifyH('LEFT')
	self.projectChip.tip = 'The folder on your PC a Claude Code chat works in. Click for the next one.'
	self.projectChip:SetScript('OnClick', function()
		local data = self:GetData()
		self.ui.project = cycle(data and data.config and data.config.projects, self.ui.project)
		self:RenderControls()
	end)
	self.backendChip = flatButton(main, '', 120, 20)
	self.backendChip:SetPoint('LEFT', self.projectChip, 'RIGHT', 6, 0)
	self.backendChip.label:SetJustifyH('LEFT')
	self.backendChip.tip = 'What answers: Claude Code, or another endpoint set up in the companion. Click for the next one.'
	self.backendChip:SetScript('OnClick', function()
		local data = self:GetData()
		self.ui.backend = cycle(data and data.config and data.config.backends, self.ui.backend)
		self:RenderControls()
	end)
	self.refreshButton = flatButton(main, 'Refresh', 66, 20)
	self.refreshButton:SetPoint('BOTTOMRIGHT', main, 'BOTTOMRIGHT', -20, 7)
	self.refreshButton:SetScript('OnClick', function()
		self:Refresh()
	end)
	self.stopButton = flatButton(main, 'Stop', 50, 20)
	self.stopButton:SetPoint('RIGHT', self.refreshButton, 'LEFT', -6, 0)
	self.stopButton.tip = 'Ask the companion to end the running turn.'
	self.stopButton:SetScript('OnClick', function()
		self:SendStop()
	end)
	self.stopButton:Hide()

	-- a string with no width limit, used only to measure text
	self.measure = f:CreateFontString(nil, 'BACKGROUND', 'GameFontHighlightSmall')
	self.measure:SetPoint('TOPLEFT', f, 'TOPLEFT', 0, 0)
	self.measure:SetAlpha(0)

	-- resize grip
	local grip = CreateFrame('Button', nil, f)
	grip:SetSize(16, 16)
	grip:SetPoint('BOTTOMRIGHT', f, 'BOTTOMRIGHT', -1, 1)
	grip:SetFrameLevel((f:GetFrameLevel() or 1) + 10)
	grip:SetNormalTexture('Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up')
	grip:SetHighlightTexture('Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight')
	grip:SetPushedTexture('Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down')
	grip:SetScript('OnMouseDown', function()
		f:StartSizing('BOTTOMRIGHT')
	end)
	grip:SetScript('OnMouseUp', function()
		f:StopMovingOrSizing()
		savePlace()
		self:Layout()
	end)

	-- a click on the window's own background gives the keyboard back to the game
	f:SetScript('OnMouseDown', function()
		transcript:ClearFocus()
		prompt:ClearFocus()
	end)
	f:SetScript('OnSizeChanged', function(_, width, height)
		self:Layout(width, height)
	end)
	f:SetScript('OnShow', function()
		self.unread = false
		self.stickBottom = true
		self:Layout()
		self:Render()
		if self:IsWaiting() then
			self:StartPolling()
		end
	end)
	f:SetScript('OnHide', function()
		if self.copying then
			self:CancelCopy()
		end
	end)

	self.built = true
	self:Layout()
	return f
end

--- One chat row in the left column.
function CC:EnsureRows(count)
	for i = #self.rows + 1, count do
		local row = CreateFrame('Button', nil, self.side)
		local y = -(ROWS_TOP + (i - 1) * (ROW_H + ROW_GAP))
		row:SetHeight(ROW_H)
		row:SetPoint('TOPLEFT', self.side, 'TOPLEFT', 6, y)
		row:SetPoint('TOPRIGHT', self.side, 'TOPRIGHT', -7, y)
		row.fill = tex(row, 'BACKGROUND', COLOR.hover, 0)
		row.fill:SetAllPoints(row)
		row.mark = tex(row, 'ARTWORK', COLOR.accent)
		row.mark:SetPoint('TOPLEFT', row, 'TOPLEFT', 0, 0)
		row.mark:SetPoint('BOTTOMLEFT', row, 'BOTTOMLEFT', 0, 0)
		row.mark:SetWidth(2)
		row.mark:Hide()
		row.tag = label(row, 'GameFontHighlightSmall', COLOR.dim, 'RIGHT')
		row.tag:SetPoint('RIGHT', row, 'RIGHT', -6, 0)
		row.title = label(row, 'GameFontHighlightSmall', COLOR.text)
		row.title:SetPoint('LEFT', row, 'LEFT', 10, 0)
		row.title:SetPoint('RIGHT', row.tag, 'LEFT', -4, 0)
		row:SetScript('OnClick', function(button)
			if not button.sessionKey then
				return
			end
			self.ui.session = button.sessionKey
			self.newSession = false
			self.stickBottom = true
			self:Render()
			if self:IsWaiting() then
				self:StartPolling()
			end
		end)
		row:SetScript('OnEnter', function(button)
			button.over = true
			paintRow(button)
			showTip(button, 'ANCHOR_RIGHT')
		end)
		row:SetScript('OnLeave', function(button)
			button.over = false
			paintRow(button)
			GameTooltip:Hide()
		end)
		row:Hide()
		self.rows[i] = row
	end
end

--- Sizes that depend on the window size. Widths are computed from the window,
--- not read back from anchored children, so they are right before the first draw.
function CC:Layout(width, height)
	local f = self.frame
	if not f or not self.built then
		return
	end
	width = width or f:GetWidth() or MIN_W
	height = height or f:GetHeight() or MIN_H
	local mainW = width - 2 - SIDE_W
	local boxW = math.max(120, mainW - 2 * PAD - SEND_W - 22)
	refreshHairlines()
	self.transcriptBox:SetWidth(math.max(160, mainW - 2 * PAD - 12))
	self.promptBox:SetWidth(boxW)
	self.staged:SetWidth(boxW)
	self.chipMax = math.max(70, math.floor((mainW - PAD - 20 - 66 - 6 - 50 - 18) / 2))
	self.rowsVisible = math.max(1, math.floor((height - HEAD_H - 2 - ROWS_TOP - 6) / (ROW_H + ROW_GAP)))
	self:EnsureRows(self.rowsVisible)
	self:RenderSessions()
	self:RenderControls()
	fitHitRect(self.tscroll, self.transcriptBox)
	fitHitRect(self.pscroll, self.promptBox)
	self:UpdateThumb()
end

--- The thin position marker beside the transcript.
function CC:UpdateThumb()
	local scroll, thumb = self.tscroll, self.thumb
	if not scroll or not thumb then
		return
	end
	local range = scroll:GetVerticalScrollRange() or 0
	local view = scroll:GetHeight() or 0
	if range <= 1 or view <= 0 then
		thumb:Hide()
		return
	end
	local size = math.max(24, view * view / (range + view))
	local offset = clamp((scroll:GetVerticalScroll() or 0) / range, 0, 1) * (view - size)
	thumb:ClearAllPoints()
	thumb:SetPoint('TOPLEFT', scroll, 'TOPRIGHT', 5, -offset)
	thumb:SetHeight(size)
	thumb:Show()
end

function CC:ScrollToBottom()
	local scroll = self.tscroll
	local function go()
		if scroll.UpdateScrollChildRect then
			scroll:UpdateScrollChildRect()
		end
		scroll:SetVerticalScroll(math.max(0, scroll:GetVerticalScrollRange() or 0))
		self:UpdateThumb()
	end
	go()
	-- the box gets its new height on the next frame
	if C_Timer and C_Timer.After then
		C_Timer.After(0, go)
	end
end

-- ------------------------------------------------------------------- sending

function CC:OnSend(forceMode)
	if self.copying then
		return
	end
	-- a text box hands back every "|" doubled
	local text = (self.promptBox:GetText() or ''):gsub('||', '|')
	if strtrim(text) == '' then
		return
	end
	self.promptBox:SetText('')
	self.stickBottom = true
	self:SendPrompt(text, forceMode)
	if not self.copying then
		self.promptBox:ClearFocus()
	end
	self:Render()
end

--- Show the request as one selected line; Ctrl+C sends it.
function CC:ShowEnvelope(request)
	if not self.frame:IsShown() then
		self.frame:Show()
	end
	self.copying = request
	self.envelopeText = self:Envelope(request)
	self.pscroll:Hide()
	self.placeholder:Hide()
	self.staged:SetText(request.kind == 'stop' and 'Stop the running turn' or esc(truncate(request.prompt or '', 400)))
	self.staged:Show()
	self.envelopeBox:Show()
	self.envelopeBox:SetText(self.envelopeText)
	self.envelopeBox:SetCursorPosition(0)
	self.envelopeBox:SetFocus()
	self.envelopeBox:HighlightText()
	self:Render()
end

function CC:HideEnvelope()
	self.envelopeBox:ClearFocus()
	self.envelopeBox:SetText('')
	self.envelopeBox:Hide()
	self.staged:Hide()
	self.pscroll:Show()
	self.placeholder:SetShown(self.promptBox:GetText() == '')
end

--- Ctrl+C was pressed on the selected line: the request left the game.
function CC:OnCopied()
	local request = self.copying
	if not request then
		return
	end
	self.copying = nil
	self:HideEnvelope()
	self:MarkSent(request)
	self.stickBottom = true
	self:Render()
end

--- Escape, Cancel, or a click elsewhere: take the request back and restore the text.
function CC:CancelCopy()
	local request = self.copying
	if not request then
		return
	end
	self.copying = nil
	self:Withdraw(request.id)
	self:HideEnvelope()
	if request.kind ~= 'stop' then
		self.promptBox:SetText(esc(request.prompt or ''))
		if request.kind == 'new' then
			self.newSession = true
		end
	end
	self:Render()
end

--- New data arrived (or a check found nothing new).
function CC:OnLiveUpdate(_, finished)
	if finished then
		for _, session in ipairs(finished) do
			self:Announce(session)
		end
	end
	self:Render()
end

--- Tell the player an answer landed when they are not looking at it.
function CC:Announce(session)
	local shown = self.frame and self.frame:IsShown()
	if shown and self.ui.session == session.key and not self.newSession then
		return
	end
	if not shown then
		self.unread = true
	end
	self:Print(string.format('answer ready in "%s". Type /claude or click the minimap button.', truncate(session.title or session.key or '?', 40)))
	if self.db.settings.sound and PlaySound and SOUNDKIT and SOUNDKIT.TELL_MESSAGE then
		PlaySound(SOUNDKIT.TELL_MESSAGE)
	end
end

-- ----------------------------------------------------------------- rendering

local function speaker(session)
	local backend = tostring(session and session.backend or ''):lower()
	if backend == '' or backend:find('claude', 1, true) or backend:find('anthropic', 1, true) then
		return 'Claude'
	end
	return esc(session.backend)
end

--- The transcript as a list of blocks, oldest first.
function CC:TranscriptBlocks(session)
	local blocks = {}
	local function add(text, tight)
		blocks[#blocks + 1] = { text = text, tight = tight }
	end
	if session then
		local hidden = tonumber(session.first) or 0
		if hidden > 0 then
			add(paintText(HEX.faint, string.format('(%d earlier message(s) show after a /reload)', hidden)))
		end
		local lastRole
		for _, entry in ipairs(session.entries or {}) do
			local role = entry.role or 'assistant'
			local text = tail(tostring(entry.text or ''), ENTRY_MAX)
			if role == 'user' then
				add(paintText(HEX.you, 'You') .. '\n' .. esc(text))
			elseif role == 'tool' then
				add(paintText(HEX.dim, '  > ' .. esc(text)), lastRole == 'tool')
			elseif role == 'error' then
				add(paintText(HEX.bad, 'Error') .. '\n' .. paintText(HEX.bad, esc(text)))
			elseif role == 'result' then
				add(paintText(HEX.faint, esc(text)), true)
			else
				add(paintText(HEX.assistant, speaker(session)) .. '\n' .. formatAnswer(text))
			end
			lastRole = role
		end
		if session.status == 'running' then
			local partial = tail(tostring(session.partial or ''), ENTRY_MAX)
			add(paintText(HEX.assistant, speaker(session)) .. '\n' .. (partial ~= '' and (formatAnswer(partial) .. paintText(HEX.dim, ' ...')) or paintText(HEX.dim, '...')))
		end
	end
	-- Requests that have not been picked up yet are echoed so the chat reads
	-- right. The one still waiting for Ctrl+C shows in the message box instead.
	local key = self.ui.session
	for _, request in ipairs(self.db.outbox) do
		if request.kind ~= 'stop' and request ~= self.copying and request.session_key == key and not self.newSession then
			add(paintText(HEX.you, 'You') .. '\n' .. esc(tail(tostring(request.prompt or ''), ENTRY_MAX)) .. '\n'
				.. paintText(HEX.faint, self.sent[request.id] and '(sent)' or '(not sent yet)'))
		end
	end
	return blocks
end

function CC:TranscriptFor(session)
	local blocks = self:TranscriptBlocks(session)
	if #blocks == 0 then
		return paintText(HEX.text, 'Ask Claude anything.') .. '\n' .. paintText(HEX.dim, 'Type in the box below and press Enter.')
	end
	-- keep the newest blocks that fit
	local first, size = #blocks, 0
	for i = #blocks, 1, -1 do
		size = size + #blocks[i].text + 2
		if size > TRANSCRIPT_MAX and i < #blocks then
			break
		end
		first = i
	end
	local parts = {}
	if session then
		parts[1] = paintText(HEX.faint, esc(string.format('%s  -  %s  -  %s', session.backend or '?',
			(session.project and session.project ~= '') and session.project or 'no project', session.status or '?')))
	end
	if first > 1 then
		parts[#parts + 1] = (#parts > 0 and '\n\n' or '') .. paintText(HEX.faint, string.format('(%d older message(s) not drawn)', first - 1))
	end
	for i = first, #blocks do
		if #parts > 0 then
			parts[#parts + 1] = blocks[i].tight and '\n' or '\n\n'
		end
		parts[#parts + 1] = blocks[i].text
	end
	return table.concat(parts)
end

function CC:RenderSessions()
	if not self.built then
		return
	end
	local sessions = self:Sessions()
	local visible = self.rowsVisible or #self.rows
	self.rowOffset = clamp(self.rowOffset or 0, 0, math.max(0, #sessions - visible))
	for i, row in ipairs(self.rows) do
		local s = (i <= visible) and sessions[i + self.rowOffset] or nil
		if s then
			row.sessionKey = s.key
			row.picked = (s.key == self.ui.session) and not self.newSession
			row.title:SetText(esc(s.title or s.key))
			if s.status == 'running' then
				row.tag:SetText('...')
				row.tag:SetTextColor(COLOR.warn[1], COLOR.warn[2], COLOR.warn[3])
			elseif s.status == 'error' then
				row.tag:SetText('!')
				row.tag:SetTextColor(COLOR.bad[1], COLOR.bad[2], COLOR.bad[3])
			else
				row.tag:SetText('')
			end
			row.tip = esc(string.format('%s\n%s  -  %s  -  %s', s.title or s.key, s.backend or '?',
				(s.project and s.project ~= '') and s.project or 'no project', s.status or '?'))
			paintRow(row)
			row:Show()
		else
			row.sessionKey = nil
			row:Hide()
		end
	end
end

function CC:RenderTranscript()
	local session = (not self.newSession) and self:CurrentSession() or nil
	local text = self:TranscriptFor(session)
	if text ~= self.transcriptText then
		self.transcriptText = text
		self.transcriptBox:SetText(text)
		if self.stickBottom then
			self:ScrollToBottom()
		end
	end
	self:UpdateThumb()
end

local function sizeChip(self, chip, name, value)
	local text = paintText(HEX.dim, name .. '  ') .. esc(value)
	chip.label:SetText(text)
	self.measure:SetText(text)
	local width = (self.measure.GetUnboundedStringWidth and self.measure:GetUnboundedStringWidth()) or self.measure:GetStringWidth() or 60
	chip:SetWidth(clamp(width + 16, 70, self.chipMax or 160))
end

--- The guidance line: what is happening, or what the player should do next.
--- Returns the line, its tone ('accent', 'warn' or 'dim') and a longer tooltip.
function CC:Guidance()
	local data = self:GetData()
	if self.copying then
		return (self.copying.kind == 'stop') and 'Press Ctrl+C to send the stop.  Esc cancels.' or 'Press Ctrl+C to send.  Esc cancels.', 'accent',
			'The selected line is your request. Ctrl+C hands it to the companion on your PC through the clipboard. Nothing is pasted back into the game.'
	end
	if not data then
		return 'Start the companion on your PC:  python -m wowclaude run', 'warn',
			'This window shows what the companion writes. It has not written anything the game has read yet.'
	end
	local waiting = 0
	for _, request in ipairs(self.db.outbox) do
		if self.sent[request.id] then
			waiting = waiting + 1
		end
	end
	if waiting > 0 then
		if self:LiveLeft() <= 0 then
			return 'Sent. The answer shows after a reload (the Reload UI button).', 'dim',
				'Answers arrive without a reload once the game knows the live slot folders: type /reload once, or restart the game if that is not enough.'
		end
		if self.poll and (self.poll.quiet or 0) >= 3 then
			return 'Not picked up yet. Is the companion running?', 'warn',
				'The companion must be running with its clipboard path on:\n  python -m wowclaude config set clipboard_watch true\n  python -m wowclaude run\nA /reload sends the request the other way.'
		end
		return 'Sent. Waiting for the companion...', 'dim', nil
	end
	local session = (not self.newSession) and self:CurrentSession() or nil
	if session and session.status == 'running' then
		if self:IsPolling() then
			return 'Working...  The answer appears here by itself.', 'dim', nil
		end
		return 'Working...  Press ' .. (self:LiveLeft() > 0 and 'Refresh' or 'Reload UI') .. ' to check on it.', 'dim', nil
	end
	if self:EffectiveSendMode() == 'reload' then
		return 'Sending reloads the UI. Mouse over this line to stop that.', 'warn',
			'Turn on the clipboard path on your PC, then restart the companion:\n  python -m wowclaude config set clipboard_watch true\nSending is then Enter and Ctrl+C, with no reload.'
	end
	local live = self.live or {}
	if self.db.settings.live and not live.broken and (live.count or 0) == 0 and (tonumber(data.config and data.config.live_slots) or 0) > 0 then
		return 'Type /reload once to turn on live answers.', 'dim',
			'The live slot folders are installed, but the game has not listed them yet. A /reload does it; if this line is still here afterwards, restart the game once.'
	end
	return '', 'dim', nil
end

function CC:RenderControls()
	if not self.built then
		return
	end
	local data = self:GetData()
	local config = data and data.config or {}
	local projects = type(config.projects) == 'table' and config.projects or {}
	local backends = type(config.backends) == 'table' and config.backends or {}
	if not self.ui.project then
		self.ui.project = (config.default_project ~= '' and config.default_project) or projects[1]
	end
	if not self.ui.backend then
		self.ui.backend = (config.default_backend ~= '' and config.default_backend) or backends[1]
	end
	sizeChip(self, self.projectChip, 'Project', self.ui.project or 'none')
	sizeChip(self, self.backendChip, 'Backend', self.ui.backend or 'none')

	local session = (not self.newSession) and self:CurrentSession() or nil
	local running = (session and session.status == 'running') or (config.running or '') ~= ''
	self.stopButton:SetShown(running and true or false)
	self.sendButton.kind = (not self.copying) and 'accent' or nil
	self.sendButton.label:SetText(self.copying and 'Cancel' or 'Send')
	self.sendButton:Paint()

	local left = self:LiveLeft()
	self.refreshButton.label:SetText(left > 0 and 'Refresh' or 'Reload UI')
	self.refreshButton.tip = left > 0 and string.format('Read the latest answer. No reload (%d checks left; a /reload recharges them).', left)
		or 'Reloads the UI to read the latest answer.'

	-- header status
	local live = self.live or {}
	local text, dot
	if not data then
		text, dot = 'companion not seen', COLOR.dim
	elseif live.broken then
		text, dot = 'live answers stopped', COLOR.bad
	elseif left > 20 then
		text, dot = 'live answers', live.ok and COLOR.good or COLOR.dim
	elseif left > 0 then
		text, dot = string.format('live answers, %d checks left', left), COLOR.warn
	elseif (live.count or 0) > 0 and self.db.settings.live then
		text, dot = 'live checks used up, /reload', COLOR.warn
	else
		text, dot = 'answers by reload', COLOR.dim
	end
	if self:IsWaiting() and left > 0 then
		dot = COLOR.warn
	end
	self.statusText:SetText(text)
	setColor(self.statusDot, dot)

	-- guidance line
	local line, tone, tip = self:Guidance()
	self.hint:SetText(line)
	self.statusLine.tip = tip
	if tone == 'accent' then
		setColor(self.statusLine.fill, COLOR.accent, 1)
		self.hint:SetTextColor(COLOR.ink[1], COLOR.ink[2], COLOR.ink[3])
	else
		setColor(self.statusLine.fill, COLOR.accent, 0)
		local c = (tone == 'warn') and COLOR.warn or COLOR.dim
		self.hint:SetTextColor(c[1], c[2], c[3])
	end
	if self.minimapButton and self.minimapButton.dot then
		self.minimapButton.dot:SetShown(self.unread and true or false)
	end
end

function CC:Render()
	if not self.built then
		return
	end
	local sessions = self:Sessions()
	if not self.ui.session and not self.newSession and sessions[1] then
		self.ui.session = sessions[1].key
	end
	self:RenderSessions()
	self:RenderTranscript()
	self:RenderControls()
end

-- ------------------------------------------------------------ minimap button

--- A small round button on the minimap, so the addon is visible without
--- knowing the slash command. A click toggles the window; a drag moves the
--- button anywhere on screen. No library and no OnUpdate: the client's own
--- frame dragging moves it.
function CC:CreateMinimapButton()
	if self.minimapButton or not Minimap then
		return self.minimapButton
	end
	local b = CreateFrame('Button', 'ClaudeCodeMinimapButton', Minimap)
	self.minimapButton = b
	b:SetSize(31, 31)
	b:SetFrameStrata('MEDIUM')
	b:SetFrameLevel(8)
	b:SetMovable(true)
	b:SetClampedToScreen(true)
	b:RegisterForClicks('AnyUp')
	b:RegisterForDrag('LeftButton')
	b:SetHighlightTexture('Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight')
	local background = b:CreateTexture(nil, 'BACKGROUND')
	background:SetTexture('Interface\\Minimap\\UI-Minimap-Background')
	background:SetSize(20, 20)
	background:SetPoint('TOPLEFT', b, 'TOPLEFT', 7, -5)
	local icon = b:CreateTexture(nil, 'ARTWORK')
	icon:SetTexture('Interface\\Icons\\INV_Misc_Note_01')
	icon:SetTexCoord(0.05, 0.95, 0.05, 0.95)
	icon:SetSize(17, 17)
	icon:SetPoint('TOPLEFT', b, 'TOPLEFT', 7, -6)
	local border = b:CreateTexture(nil, 'OVERLAY')
	border:SetTexture('Interface\\Minimap\\MiniMap-TrackingBorder')
	border:SetSize(53, 53)
	border:SetPoint('TOPLEFT', b, 'TOPLEFT', 0, 0)
	-- lit when an answer landed while the window was closed
	b.dot = b:CreateTexture(nil, 'OVERLAY', nil, 2)
	setColor(b.dot, COLOR.accent)
	b.dot:SetSize(8, 8)
	b.dot:SetPoint('TOPRIGHT', b, 'TOPRIGHT', -3, -3)
	b.dot:Hide()
	b:SetScript('OnClick', function()
		self:Toggle()
	end)
	b:SetScript('OnDragStart', b.StartMoving)
	b:SetScript('OnDragStop', function(button)
		button:StopMovingOrSizing()
		local point, _, relPoint, x, y = button:GetPoint()
		self.ui.minimap_point = { point, relPoint, x, y }
	end)
	b:SetScript('OnEnter', function(button)
		GameTooltip:SetOwner(button, 'ANCHOR_LEFT')
		GameTooltip:SetText('Claude Code', 1, 0.82, 0)
		GameTooltip:AddLine(self.unread and 'An answer is waiting. Click to read it.' or 'Click to open or close the window.', 1, 1, 1)
		GameTooltip:AddLine('Drag to move this button. /claude minimap hides it.', 0.6, 0.6, 0.6)
		GameTooltip:Show()
	end)
	b:SetScript('OnLeave', function()
		GameTooltip:Hide()
	end)
	self:PlaceMinimapButton()
	return b
end

--- Put the button where the player left it (or at the left edge of the
--- minimap) and show or hide it by the setting.
function CC:PlaceMinimapButton()
	local b = self.minimapButton
	if not b then
		return
	end
	b:ClearAllPoints()
	local p = self.ui.minimap_point
	if type(p) == 'table' and p[1] then
		b:SetPoint(p[1], UIParent, p[2] or p[1], p[3] or 0, p[4] or 0)
	else
		b:SetPoint('CENTER', Minimap, 'LEFT', -2, -22)
	end
	if self.db.settings.minimap then
		b:Show()
	else
		b:Hide()
	end
end

function CC:Toggle()
	if self.windowBroken then
		self:Print(self.loadError or 'the window failed to build.')
		return
	end
	if not self.frame then
		return
	end
	if self.frame:IsShown() then
		self.frame:Hide()
	else
		self.frame:Show()
	end
end
