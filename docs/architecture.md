# Architecture

## The constraint that shapes everything

A World of Warcraft addon runs in a Lua sandbox with no network, no file access and no process
access. Its only persistent output is its SavedVariables file, which the client writes on
`/reload`, logout and exit. Its only input from outside the game is the Lua files in
`Interface/AddOns`, which the client reads when an addon loads: at login, on `/reload`, or later
for a load-on-demand addon when something calls `C_AddOns.LoadAddOn`. The client lists the addon
folders only when it starts and on a `/reload`. Every other way to move data in or out of a
running client (keystroke injection, pixel reading, overlays with hooks, DLLs, memory access) is
the kind of thing Blizzard's EULA forbids and bans accounts for.

This project uses file channels and one line the player copies. Requests leave the game as a line
the player copies with Ctrl+C, or through SavedVariables on a `/reload`. Answers come back through
files the addon loads: the live slots while the game runs, or `ClaudeCode_Data` on a `/reload`.
The reload path is the fallback for both directions, and it is the only path when the clipboard
path and the live slots are off.

## Pieces

| piece | where | job |
|---|---|---|
| `ClaudeCode` addon | `addon/ClaudeCode` | the window; stores requests in `ClaudeCodeDB.outbox`; stages the clipboard line; loads live slots; renders `ClaudeCode_Data` and what the slots add |
| `ClaudeCode_Data` addon | `addon/ClaudeCode_Data` | the full data file the companion rewrites, read on a `/reload`; nothing else |
| live slot folders | `Interface/AddOns/ClaudeCode_Live001`, `ClaudeCode_Live002`, ... | load-on-demand addons with a TOC and one file, `Live.lua`, created by `install` (`live_slots`, default 200) |
| the live copy | `ClaudeCode_Data/ClaudeCode_Live.lua` | the master the companion hard-links into every slot as `Live.lua`; the game never loads it from there because the data addon's TOC does not list it |
| `wowclaude.bridge` | companion | owns the state: scans SavedVariables, dedupes ids, runs requests, writes the data file and the live copy |
| `wowclaude.backends` | companion | `ClaudeCodeBackend` (CLI print mode), `OpenAIChatBackend` (any `/chat/completions`), `AnthropicBackend` (Messages API) |
| `wowclaude.luaio` | companion | parses the client's SavedVariables syntax, writes the same style |
| `wowclaude.clipboard` | companion | prefix-gated clipboard watcher, read-only, used when `clipboard_watch` is true |
| `wowclaude.notify` | companion | best-effort desktop notification |
| `wowclaude.installer` | companion | copies the two addon folders, creates and removes the slot folders, refuses any other path |
| `wowclaude.config` | companion | `~/.wowclaude/config.json`; key sources, never keys |
| `wowclaude.cli` | companion | `init`, `install`, `uninstall`, `run`, `doctor`, `selfcheck`, `config` |

## One request, end to end

1. The player types a message and presses Enter. `Core.lua` builds a request record (with the
   nonce from the companion's data) and appends it to the outbox. When the companion's data says
   `clipboard_watch` is on, `UI.lua` stages the message: it selects one line, the envelope
   `CLAUDECODE-WOW:1:{json}`, gives it the keyboard, and shows "Press Ctrl+C to send. Esc cancels."
   Esc, the Cancel button or a click away from the line (outside the window included) withdraws
   the request again and gives the keyboard back to the game.
2. The player presses Ctrl+C. The client copies the line with its own copy. The addon marks the
   request sent and starts its live checks. The companion's watcher sees the clipboard sequence
   number change, reads the text, and accepts it only when it starts with the prefix and carries
   this install's nonce. `Bridge.submit_envelope` queues it.
3. `Bridge.work_once` marks the request consumed, picks the backend named in the request (or the
   default), resolves the project folder for Claude Code, appends the user entry, marks the
   session `running`, writes the data file, and calls `backend.run_turn`.
4. The backend streams events. Text is accumulated into `partial`; tool calls become `tool`
   entries. About every 1.5 seconds the data file is rewritten, and at least every 20 seconds even when the
   backend is silent, so the game can tell a slow turn from a companion that is not running. The
   result closes the turn with cost and duration.
5. Every data write also writes the live copy: a small snapshot of the newest sessions with the
   same `seq`. The companion hard-links it into every slot folder as `Live.lua` (it copies where
   the file system has no hard links, and retries a slot the game holds open).
6. While an answer is pending, the addon's one timer asks the client to load the next unused slot
   with `C_AddOns.LoadAddOn`, on a slowing schedule. The client reads `Live.lua` from disk at that
   moment. The addon merges the snapshot only when its `seq` is higher than the last one it took,
   prunes consumed ids from its outbox, and redraws the window. A slot loads once per UI session,
   so every check spends one slot.
7. The request id joins `consumed`, the data file is rewritten, a desktop notification fires. When
   the addon sees the turn finish while the window is closed, it prints a chat line, plays a soft
   sound and lights the minimap dot. The timer stops when nothing is pending.

The reload path does the same work with different transport. When the clipboard path is off, the
addon calls `ReloadUI()` after it stores the request. The client writes `ClaudeCode.lua`; the
companion's poll loop notices the new mtime, parses the file and queues every id it has not
consumed. The answer is read on the next `/reload`, when the client loads `ClaudeCode_Data`. When
the live checks run out, the Refresh button becomes Reload UI and reads the answer the same way.

## Why a pool of slots

A load-on-demand addon is parsed when it loads, so its file can change while the game runs, but
it loads only once per UI session. A new answer therefore needs an addon that has not loaded yet.
The addon cannot know which slot it will use next, so the companion writes the same snapshot into
all of them. The folders must exist before the client lists addons (at start or on a `/reload`),
which is why `install` creates them up front. A `/reload` unloads every slot, so all of them are
usable again.

## Sessions

- Claude Code: the companion keeps the CLI's session id per addon session and passes
  `--resume` on later turns. Stop terminates the child process; the session stays resumable.
- Chat backends: the companion keeps the message history per session (capped by
  `max_history_messages`) and replays it with each turn.

## State

`~/.wowclaude/state.json` holds consumed ids (last 500) and every session with its backend
state. It is the single owner of that information; the data file and the live copy are
projections of it.

## Why Python and the standard library

Anyone who can install the Claude Code CLI can run Python 3. No packages means `python -m wowclaude`
works from a bare download with nothing to resolve, audit or update. The Claude Code
integration drives the user's own installed CLI, so it follows the user's version and login
instead of pinning an SDK.
