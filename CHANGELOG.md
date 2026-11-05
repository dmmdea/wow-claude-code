# Changelog

## 0.2.2 (2026-11-04)

- A Claude Code turn is finished when the CLI prints its result. The CLI process can keep running
  after that (end-of-session hooks such as a memory sync; 38 seconds on the machine where this was
  measured), and until now the answer stayed "running" for all of that time. The process is left
  to finish on its own, the next turn waits for it, and so does the companion's exit.
- Docs: `--bare` in `extra_args` works only with an API key, because the CLI then reads no login.
  A troubleshooting note on speed and usage.

## 0.2.1 (2026-11-04)

- One companion at a time. `run` takes a lock in the config folder and a second `run` stops with
  "another companion is already running". Two companions would each run every request, which
  doubles the usage and puts two sessions to work in the same project folder. The lock ends with
  its process, so nothing has to be cleaned up after a crash.

## 0.2.0 (2026-11-04)

- Answers arrive without a reload. `install` now also creates live slot folders beside the two
  addon folders (`ClaudeCode_Live001`, `ClaudeCode_Live002`, ...; the config key `live_slots`
  sets how many, 200 by default, 0 turns the feature off). Type `/reload` in the game once after
  installing, and restart the game if the window still says "Type /reload once to turn on live
  answers." Every live check uses one slot until the next `/reload`; when none is left the Refresh
  button becomes Reload UI. `/claude live off` stops the addon from using them.
- Sending without a reload: type a message and press Enter, then Ctrl+C on the selected line. Esc,
  Cancel or a click away from the line (outside the window included) takes the message back and
  gives the keyboard back to the game. This needs `clipboard_watch` true; `init` now asks about it
  (yes by default on a first run; a later `init` keeps what the config says) and has `--clipboard`
  and `--no-clipboard`. With it off, a send reloads the UI as before.
- A new window: a flat dark panel with a chat list and **+ New chat**, a transcript with You and
  Claude labels and light markdown, a guidance line, Project and Backend chips, Stop, Refresh, a
  resize corner and a status in the header. The transcript text can be selected and copied. The
  Copy button is gone: Enter followed by Ctrl+C replaces it, and `/claude copy` still works.
- When an answer lands while the window is closed you get a chat line, a soft sound
  (`/claude sound` toggles it) and a dot on the minimap button. New slash commands: `/claude live`,
  `/claude mode`, `/claude sound` and `/claude refresh`.
- Backend option `env` for `claude-code` backends: environment variables set for the CLI process,
  for a Claude Code login kept outside the default folder (`CLAUDE_CONFIG_DIR`,
  `CLAUDE_SECURESTORAGE_CONFIG_DIR`). `init` saves them when its shell has them, and `doctor` lists
  them and prints one `config set` command per variable when the shell has one the config lacks.
  Names that look like credentials, and values that carry one (a password inside a URL, an
  authorization header), are refused without repeating the value, and `config show` and
  `config get` mask them.
- `doctor --live` runs one tiny real turn through each `claude-code` backend and fails when it
  does not answer. The turn runs in the default project folder, or in an empty temporary folder
  when there is none. `doctor` also reports the slot folders.
- While a turn runs the companion rewrites its data at least every 20 seconds, so the window can
  tell a slow turn from a companion that is not running. A data file that cannot be replaced for a
  moment no longer stops the companion: it logs, still publishes the live copy and writes again.
- `config set backends.<name>` refuses a value that is not a JSON object instead of dropping the
  backend silently (some shells remove the quotes of a JSON object). The README now sets a backend
  one field per command.
- Fixed: a failed turn shows its error once instead of three times; a `|` typed in the message box
  is no longer sent doubled; the desktop notification no longer tells you to press Refresh; a
  title cut for display no longer ends inside a multi-byte character.
- `uninstall` removes the two addon folders and only the slot folders `install` created (they
  carry a marker line in their `.toc` file).
- The data file gains `seq` and `config.live_slots`, and the companion writes a small live copy
  of the newest sessions into every slot. See `docs/bridge-protocol.md`.
- `docs/safety.md` now covers the live slots, their prior art and the open Terms of Use question.
- Development: `tools/lua_smoke.py` walks the real paths (type, Enter, Ctrl+C, live answer, the
  ways a send is taken back, running out of checks, reload fallback). CI runs luacheck and `selfcheck`, which now also checks
  the slots.

## 0.1.1 (2026-11-04)

- The addon is visible without knowing the slash command: a chat line at login (and the first
  time it ever runs) says how to open the window, and a minimap button toggles it. Drag the
  button to move it; `/claude minimap` hides it and `/claude minimap reset` puts it back.
- A part of the window that fails to build is reported in chat and by `/claude status` instead
  of failing silently.
- A click anywhere in the prompt area focuses the prompt.

## 0.1.0 (2026-11-04)

First release.

- `ClaudeCode` addon: in-game window with sessions, transcript, prompt, project and backend
  pickers, Send / Copy / Stop / Refresh / New, `/claude` and `/cc` slash commands, a keybinding
  and an addon compartment entry. Interface 16001 (Forever), 120107 (retail), 11509 (Classic Era).
- `ClaudeCode_Data` addon: the bridge file the companion writes.
- `wowclaude` companion (Python 3.10+, standard library only): `init`, `install`, `run`,
  `doctor`, `selfcheck`, `config`; backends for the Claude Code CLI, any OpenAI-compatible chat
  endpoint and the Anthropic Messages API; opt-in clipboard send path; desktop notifications.
- The clipboard envelope nonce is written to `state.json` at the first start, so it is per
  install rather than per run.
