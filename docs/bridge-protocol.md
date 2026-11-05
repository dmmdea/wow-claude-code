# Bridge protocol

Two directions. A request leaves the game as a record in SavedVariables or as one line the player
copies. An answer comes back in two files the companion writes: the full data file, which the
game reads on a `/reload`, and a small live copy in each slot folder, which the addon loads while
the game runs. The addon never reads anything but those. The companion never writes anything but
`ClaudeCode_Data.lua`, `ClaudeCode_Live.lua`, the `Live.lua` of each of its own slot folders
(plus temporary files beside them) and its own state under `~/.wowclaude`.

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
companion's `consumed` list as soon as it reads one, from the data file or from a live copy.

## Clipboard envelope

When the companion's `clipboard_watch` is true (the data file says so in `config.clipboard_watch`),
the window sends a request as one line instead of reloading the UI. The line is the same record as
one line of JSON after a fixed prefix.

```
CLAUDECODE-WOW:1:{"backend":"claude-code","created_at":1762300000,"id":"1762300000-1a2b-1","kind":"new","nonce":"3f9a1c2e7b4d8e10","project":"myproject","prompt":"hello","session_key":"s-1762300000"}
```

The send goes like this:

1. The player types in the message box and presses Enter (Shift+Enter adds a line). The addon
   appends the record to the outbox and stages it: the message shows in the box, the line is
   selected in a text box that has the keyboard, and the guidance line reads "Press Ctrl+C to
   send. Esc cancels."
2. The player presses Ctrl+C. The client copies the selected line with its own copy. The addon
   marks the request sent and starts checking for the answer.
3. Esc, the Cancel button, or a click away from the line (outside the window, on its background,
   on the transcript or on another chat) withdraws the record from the outbox and puts the text
   back in the message box, unless a draft is already there. Staging a second request withdraws
   the first. A `stop` request is staged and sent the same way.

The addon's JSON encoder writes `|` as `|`, so an envelope never contains a pipe character.
The client hands a text box's pipes back doubled; the addon undoes that for text typed in the
message box, so a `|` is sent once.

The companion dedupes by id, so a request that arrives both ways (a copied line, and the outbox
after a `/reload`) runs once. An envelope is accepted only when its `nonce` matches the one the
companion published in the data file. The addon copies the nonce from the data it has loaded, so
text that some other process or a web page put on the clipboard is ignored, and a request made
before the game has loaded any companion data carries an empty nonce and is ignored too. A request
is marked consumed before it runs: if the companion dies mid-turn, the request is not replayed.

With `clipboard_watch` false, the window does not stage a line. It stores the record and reloads the
UI, so the SavedVariables file carries the request, and it says so above the message box.

## Companion -> game: `ClaudeCode_Data`

Path: `<wow_dir>/<flavor>/Interface/AddOns/ClaudeCode_Data/ClaudeCode_Data.lua`. Written
atomically (temp file + replace, with retries while the client holds the file during a reload).
If the file still cannot be replaced, the companion logs it, publishes the live copy of that write
anyway and writes the data file again a few seconds later; only the first write of a run fails
loudly. The client re-reads the file on `/reload`. The folder itself must exist when the game starts, which
is why the repository ships a placeholder file.

```lua
ClaudeCode_Data = {
	["schema"] = 1,
	["seq"] = 1762300100123,                -- milliseconds; never repeats, never goes back
	["generated_at"] = 1762300100,          -- unix time of this write
	["companion_version"] = "0.2.0",
	["config"] = {
		["projects"] = { "myproject", -- [1] },
		["default_project"] = "myproject",
		["backends"] = { "claude-code", -- [1] },
		["default_backend"] = "claude-code",
		["clipboard_watch"] = true,
		["nonce"] = "3f9a1c2e7b4d8e10",       -- echoed by the addon in every request
		["running"] = "",                     -- id of the request being worked, or ""
		["queued"] = 0,
		["live_slots"] = 200,                 -- slot folders the companion found; 0 when none
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

`seq` is new in 0.2.0. Each write takes `max(previous seq + 1, the current time in milliseconds)`,
so a later write always carries a higher number, and the live copy of that write carries the same
one. `config.live_slots` is the number of slot folders the companion found on disk (the companion
looks again every 30 seconds while it idles); the window uses it to tell "installed but not listed
by the game yet" from "not installed". `schema` is still 1.

Entry roles: `user`, `assistant`, `tool`, `result`, `error`. The file is kept under 1 MB: the
oldest entries of the least recently updated sessions go first, then whole sessions beyond the
newest five. At most 20 sessions are exported.

## Companion -> game: the live copy

The live copy is how an answer reaches a running game without a reload. On every data write the
companion also writes it.

**Locations.** The companion writes one master file, `ClaudeCode_Data/ClaudeCode_Live.lua`, beside
the data file. It is not in the data addon's TOC, so the game never loads it from there. For each
slot folder `ClaudeCode_Live001`, `ClaudeCode_Live002`, ... the companion hard-links the master to
`Live.lua.new` inside the slot and renames that over `Live.lua`. Where the file system has no hard
links it copies the master instead (one log line says so). A slot the game holds open is skipped
and retried on the next write and in the idle loop.

**The global.** The file sets one global, `ClaudeCode_Live`. The addon clears it, asks the client
to load a slot with `C_AddOns.LoadAddOn`, reads the global, and clears it again. A slot with no
data holds `ClaudeCode_Live = nil`, and the addon treats that as nothing new.

```lua
ClaudeCode_Live = {
	["schema"] = 1,
	["seq"] = 1762300100123,
	["generated_at"] = 1762300100,
	["companion_version"] = "0.2.0",
	["config"] = { ... },                   -- the same table as in the data file
	["consumed"] = { "1762300000-1a2b-1", -- [1] },   -- the last 40 ids only
	["sessions"] = {
		["s-1762300000"] = {
			["key"] = "s-1762300000",
			["title"] = "hello",
			-- project, backend, status, cost_usd, created_at, updated_at, partial: as above
			["first"] = 0,                    -- entries left out at the front
			["total"] = 4,                    -- entries the session has in full
			["entries"] = { ... },            -- entries first+1 .. total
		},
	},
}
```

Fields:

| field | meaning |
|---|---|
| `schema` | 1, the same as the data file; the addon ignores a copy with another value |
| `seq` | the `seq` of the data write this copy belongs to |
| `generated_at`, `companion_version`, `config` | as in the data file |
| `consumed` | the last 40 consumed request ids (the data file has 500) |
| `sessions` | the three most recently updated sessions only, by `updated_at` |
| `sessions.*.first` | how many leading entries were left out because of the size limit |
| `sessions.*.total` | how many entries the session has in full |

Each session is trimmed from the front, always keeping its last two entries, until it serializes
to at most 60,000 bytes, because the client parses the whole file at load time. Every other
session field is the same as in the data file.

**How the addon merges a copy.**

- Only a higher `seq` is merged. The addon starts with the `seq` of the data file it loaded (0 if
  it loaded none). A copy with an equal or lower `seq`, or one that is empty or has another
  `schema`, changes nothing, but its slot is spent.
- A merged copy replaces `generated_at`, `seq`, `companion_version`, `config` and `consumed`, then
  the addon drops outbox entries whose ids are now in `consumed`.
- A session in the copy replaces the addon's session with the same key. With `first` above 0 the
  addon keeps the entries it already holds: when its own `first` is not above the copy's and its
  entries reach up to the copy's `first`, the result is its own entries before that point
  followed by the copy's entries, and its own `first` stays. Otherwise the copy's entries stand
  alone, and the window shows how many earlier messages appear after a `/reload`.
- Sessions that are not in the copy stay as they were.
- A session that was `running` and now is not counts as finished: if the window is closed or
  shows another chat, the addon prints a chat line, plays the sound and lights the minimap dot.

**Slot folders.** `install` creates `live_slots` folders (0 to 400). Each holds `Live.lua` and a
TOC named after the folder. The TOC copies the `## Interface:` line of `ClaudeCode.toc`:

```
## Interface: 16001, 120107, 11509
## Title: Claude Code live slot 001
## LoadOnDemand: 1
## Dependencies: ClaudeCode
## Group: ClaudeCode
## X-ClaudeCode-Live: 1

Live.lua
```

(`Notes`, `Author` and `IconTexture` lines are left out here.) The `## X-ClaudeCode-Live: 1`
line is the marker. `install` removes surplus slots and `uninstall` removes all of them, and both
act only on a folder named `ClaudeCode_Live` plus three digits whose TOC carries the marker. A
folder with such a name and no marker is left alone, and `install` refuses to reuse it when it
holds files.

## String escaping

Both sides write strings the way the client does: `\"`, `\\`, `\n`, `\r`, `\t`, `|` as `\124`,
and any other control byte as a three-digit decimal escape. The addon doubles pipes (`||`) when
it displays text so the client renders them literally.
