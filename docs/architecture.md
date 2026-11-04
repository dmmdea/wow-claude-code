# Architecture

## The constraint that shapes everything

A World of Warcraft addon runs in a Lua sandbox with no network, no file access and no process
access. Its only persistent output is its SavedVariables file, which the client writes on
`/reload`, logout and exit; its only input from outside the game is the Lua files in
`Interface/AddOns`, re-read on `/reload`. Every other way to move data in or out of a running
client (keystroke injection, pixel reading, overlays with hooks, DLLs, memory access) is the
kind of thing Blizzard's EULA forbids and bans accounts for. This project uses only the two
file channels the established companion apps use, so the user experience is "one reload per
exchange" by design.

## Pieces

| piece | where | job |
|---|---|---|
| `ClaudeCode` addon | `addon/ClaudeCode` | the window; stores requests in `ClaudeCodeDB.outbox`; renders `ClaudeCode_Data` |
| `ClaudeCode_Data` addon | `addon/ClaudeCode_Data` | one Lua file the companion rewrites; nothing else |
| `wowclaude.bridge` | companion | owns the state: scans SavedVariables, dedupes ids, runs requests, writes the data file |
| `wowclaude.backends` | companion | `ClaudeCodeBackend` (CLI print mode), `OpenAIChatBackend` (any `/chat/completions`), `AnthropicBackend` (Messages API) |
| `wowclaude.luaio` | companion | parses the client's SavedVariables syntax, writes the same style |
| `wowclaude.clipboard` | companion | opt-in, prefix-gated clipboard watcher |
| `wowclaude.notify` | companion | best-effort desktop notification |
| `wowclaude.installer` | companion | copies the two addon folders into `Interface/AddOns`, refuses any other path |
| `wowclaude.config` | companion | `~/.wowclaude/config.json`; key sources, never keys |
| `wowclaude.cli` | companion | `init`, `install`, `run`, `doctor`, `selfcheck`, `config` |

## One request, end to end

1. The player types a prompt and presses Send. `Core.lua` appends a request record to the
   outbox and calls `ReloadUI()` (or shows the clipboard envelope when the watcher is on).
2. The client writes `ClaudeCode.lua`. The companion's poll loop notices the new mtime, parses
   the file, and queues every id it has not consumed.
3. `Bridge.work_once` picks the backend named in the request (or the default), resolves the
   project folder for Claude Code, appends the user entry, marks the session `running`, writes
   the data file, and calls `backend.run_turn`.
4. The backend streams events. Text is accumulated into `partial` and flushed to the data file
   every two seconds; tool calls become `tool` entries; the result closes the turn with cost and
   duration.
5. The request id joins `consumed`, the data file is rewritten, a desktop notification fires.
6. The player presses Refresh. The addon loads `ClaudeCode_Data`, prunes consumed ids from the
   outbox, and renders the session.

## Sessions

- Claude Code: the companion keeps the CLI's session id per addon session and passes
  `--resume` on later turns. Stop terminates the child process; the session stays resumable.
- Chat backends: the companion keeps the message history per session (capped by
  `max_history_messages`) and replays it with each turn.

## State

`~/.wowclaude/state.json` holds consumed ids (last 500) and every session with its backend
state. It is the single owner of that information; the data file is a projection of it.

## Why Python and the standard library

Anyone who can install the Claude Code CLI can run Python 3. No packages means `python -m wowclaude`
works from a bare download with nothing to resolve, audit or update. The Claude Code
integration drives the user's own installed CLI, so it follows the user's version and login
instead of pinning an SDK.
