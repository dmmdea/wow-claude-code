# Bridge protocol

Two files, one direction each. The addon never reads anything but `ClaudeCode_Data`, and the
companion never writes anything but `ClaudeCode_Data.lua` (plus its own state under
`~/.wowclaude`).

## Game -> companion: `ClaudeCodeDB` (SavedVariables)

Path: `<wow_dir>/<flavor>/WTF/Account/<ACCOUNT>/SavedVariables/ClaudeCode.lua`. The client
writes it on `/reload`, logout and exit; nothing else flushes it. The companion watches every
account folder.

```lua
ClaudeCodeDB = {
	["version"] = 1,
	["outbox"] = {
		{
			["id"] = "1762300000-1a2b-1",      -- unique per request: time, random, counter
			["kind"] = "new",                   -- "new" | "prompt" | "stop"
			["session_key"] = "s-1762300000",   -- the addon's session id (stable across turns)
			["project"] = "myproject",          -- a name from the companion config (Claude Code only)
			["backend"] = "claude-code",        -- a backend name from the companion config
			["prompt"] = "line one\nline two",
			["created_at"] = 1762300000,
			["nonce"] = "3f9a1c2e7b4d8e10",   -- the companion's token from the data file, or ""
		}, -- [1]
	},
	["settings"] = { ... },                       -- window settings, companion ignores them
	["ui"] = { ... },                             -- last picks, companion ignores them
}
```

Kinds:

- `new`: start a session with this key and run the prompt.
- `prompt`: continue the session with this key (Claude Code: `--resume <session id>`; chat
  backends: the companion replays the kept history).
- `stop`: end the running turn of this session (the prompt is empty).

The outbox keeps at most 50 entries; the addon removes entries whose id appears in the
companion's `consumed` list on its next load.

## Clipboard envelope (optional)

The same record as one line of JSON after a fixed prefix. The addon's JSON encoder writes `|`
as `|`, so an envelope never contains a pipe character.

```
CLAUDECODE-WOW:1:{"backend":"claude-code","created_at":1762300000,"id":"1762300000-1a2b-1","kind":"new","project":"myproject","prompt":"hello","session_key":"s-1762300000"}
```

The companion dedupes by id, so a request that arrives both ways runs once. An envelope is
accepted only when its `nonce` matches the one the companion published in the data file, so
text that some other process or a web page put on the clipboard is ignored. A request is
marked consumed before it runs: if the companion dies mid-turn, the request is not replayed.

## Companion -> game: `ClaudeCode_Data`

Path: `<wow_dir>/<flavor>/Interface/AddOns/ClaudeCode_Data/ClaudeCode_Data.lua`. Written
atomically (temp file + replace, with retries while the client holds the file during a reload).
The client re-reads it on `/reload`. The folder itself must exist when the game starts, which
is why the repository ships a placeholder file.

```lua
ClaudeCode_Data = {
	["schema"] = 1,
	["generated_at"] = 1762300100,          -- unix time of this write
	["companion_version"] = "0.1.0",
	["config"] = {
		["projects"] = { "myproject", -- [1] },
		["default_project"] = "myproject",
		["backends"] = { "claude-code", -- [1] },
		["default_backend"] = "claude-code",
		["clipboard_watch"] = false,
		["nonce"] = "3f9a1c2e7b4d8e10",       -- echoed by the addon in every request
		["running"] = "",                     -- id of the request being worked, or ""
		["queued"] = 0,
	},
	["consumed"] = { "1762300000-1a2b-1", -- [1] },   -- last 500 request ids taken
	["sessions"] = {
		["s-1762300000"] = {
			["key"] = "s-1762300000",
			["title"] = "hello",
			["project"] = "myproject",
			["backend"] = "claude-code",
			["status"] = "done",              -- idle | running | done | error | stopped
			["cost_usd"] = 0.0123,
			["created_at"] = 1762300000,
			["updated_at"] = 1762300100,
			["partial"] = "",                 -- streamed text while status == "running"
			["entries"] = {
				{ ["role"] = "user", ["text"] = "hello", ["ts"] = 1762300000 },
				{ ["role"] = "tool", ["text"] = "Read: README.md", ["ts"] = 1762300050 },
				{ ["role"] = "assistant", ["text"] = "...", ["ts"] = 1762300099 },
				{ ["role"] = "result", ["text"] = "12.3s, 2 turns, $0.0123", ["ts"] = 1762300100 },
			},
		},
	},
}
```

Entry roles: `user`, `assistant`, `tool`, `result`, `error`. The file is kept under 1 MB: the
oldest entries of the least recently updated sessions go first, then whole sessions beyond the
newest five. At most 20 sessions are exported.

## String escaping

Both sides write strings the way the client does: `\"`, `\\`, `\n`, `\r`, `\t`, `|` as `\124`,
and any other control byte as a three-digit decimal escape. The addon doubles pipes (`||`) when
it displays text so the client renders them literally.
