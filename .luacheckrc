-- luacheck configuration for the ClaudeCode addon.
-- Every WoW API global the addon touches is listed by name on purpose: the list
-- doubles as the inventory that is checked against addons already running on
-- the Forever client (see docs/safety.md, "What the addon uses").
std = "lua51"
max_line_length = false
codes = true
ignore = {
	"212", -- unused argument (frame handlers receive self, event, ... by contract)
	"631", -- line too long
}

-- Globals the addon defines (shared between its files and read by the client).
globals = {
	"ClaudeCode",
	"ClaudeCodeDB",
	"ClaudeCodeJson",
	"ClaudeCodeFrame",
	"ClaudeCode_Data",
	"ClaudeCode_OnAddonCompartmentClick",
	"ClaudeCode_ToggleBinding",
	"BINDING_HEADER_CLAUDECODE",
	"BINDING_NAME_CLAUDECODE_TOGGLE",
	"SLASH_CLAUDECODE1",
	"SLASH_CLAUDECODE2",
	"SlashCmdList",
	"StaticPopupDialogs",
}

-- WoW client globals the addon reads.
read_globals = {
	-- frames and templates
	"CreateFrame",
	"UIParent",
	"UISpecialFrames",
	"GameFontNormal",
	"GameFontNormalSmall",
	"GameFontHighlight",
	"GameFontHighlightSmall",
	"ChatFontNormal",
	"GameTooltip",
	-- client functions
	"ReloadUI",
	"InCombatLockdown",
	"GetTime",
	"GetServerTime",
	"PlaySound",
	"StaticPopup_Show",
	"StaticPopup_Hide",
	"GetLocale",
	-- namespaces
	"C_AddOns",
	"C_Timer",
	"C_UI",
	"SOUNDKIT",
	-- WoW Lua extensions
	"strtrim",
	"strsplit",
	"strjoin",
	"tinsert",
	"tremove",
	"wipe",
	"format",
	"date",
	"time",
	"print",
	"DEFAULT_CHAT_FRAME",
	"geterrorhandler",
	-- globals the client fills from the TOC / data addon
	"ClaudeCode_Data",
}
