-- Json.lua: a small JSON encoder (no decoder) used to build the clipboard
-- envelope. Pipes are written as | so the envelope never contains a "|",
-- which the client would otherwise treat as an escape sequence.

ClaudeCodeJson = {}
local Json = ClaudeCodeJson

local ESCAPES = {
	['"'] = '\\"',
	['\\'] = '\\\\',
	['\n'] = '\\n',
	['\r'] = '\\r',
	['\t'] = '\\t',
	['|'] = '\\u007c',
}

local function escapeChar(c)
	return ESCAPES[c] or string.format('\\u%04x', string.byte(c))
end

local function encodeString(s)
	return '"' .. s:gsub('[%c"\\|]', escapeChar) .. '"'
end

local function isArray(t)
	local n = 0
	for _ in pairs(t) do
		n = n + 1
	end
	return n == #t
end

local function encodeValue(value, depth)
	local kind = type(value)
	if kind == 'nil' then
		return 'null'
	elseif kind == 'boolean' then
		return value and 'true' or 'false'
	elseif kind == 'number' then
		if value ~= value or value == math.huge or value == -math.huge then
			return 'null'
		elseif math.floor(value) == value and math.abs(value) < 1e15 then
			return string.format('%d', value)
		end
		return string.format('%.14g', value)
	elseif kind == 'string' then
		return encodeString(value)
	elseif kind == 'table' then
		if depth > 32 then
			error('ClaudeCodeJson: table nested too deeply')
		end
		local parts = {}
		if isArray(value) then
			for i = 1, #value do
				parts[i] = encodeValue(value[i], depth + 1)
			end
			return '[' .. table.concat(parts, ',') .. ']'
		end
		local keys = {}
		for k in pairs(value) do
			keys[#keys + 1] = tostring(k)
		end
		table.sort(keys)
		for i, k in ipairs(keys) do
			local v = value[k]
			if v == nil then
				v = value[tonumber(k)]
			end
			parts[i] = encodeString(k) .. ':' .. encodeValue(v, depth + 1)
		end
		return '{' .. table.concat(parts, ',') .. '}'
	end
	error('ClaudeCodeJson: cannot encode a ' .. kind)
end

--- Encode a Lua value as a single-line JSON string.
function Json.encode(value)
	return encodeValue(value, 0)
end
