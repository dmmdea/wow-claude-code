# Safety: staying on the right side of the game's rules

This project exists because its author wants to use it on an account that matters. Every
design choice below starts from "give Blizzard nothing to look at", and this page shows the
reasoning so you can judge it yourself. Nothing here is legal advice.

## What the companion does and does not do

Does:

- Read the SavedVariables file the game writes for the `ClaudeCode` addon
  (`WTF/Account/<account>/SavedVariables/ClaudeCode.lua`).
- Write one Lua file into its own addon folder (`Interface/AddOns/ClaudeCode_Data/`).
- Run the Claude Code CLI, or an HTTP request to the endpoint you configured, on your PC.
- Optionally (off by default) read the OS clipboard after you press Ctrl+C in the game's own
  text box, acting only on text that starts with `CLAUDECODE-WOW:1:`.
- Show a desktop notification.

Does not, ever:

- Open a handle to the game process, inject code, hook anything, or read its memory.
- Send keystrokes, mouse input or clipboard pastes into the game.
- Capture the screen or read pixels.
- Draw an overlay over the game window.
- Place any file beside the game executable or anywhere outside the two addon folders.
- Read, display or act on combat data. The window shows text you typed and text a model
  answered; nothing about the game state.

## What the rules say

The Blizzard End User License Agreement (revised 2024-03-21) is the controlling text.

- Section 1.C.ii prohibits "cheats", "bots" (software that automates control) and "hacks"
  ("accessing or modifying the software of the Platform in any manner not expressly authorized").
  Keystroke injection, DLL injection and memory reading fall here. This project has none of them.
- Section 1.C.vi prohibits "any unauthorized process or software that intercepts, collects,
  reads, or 'mines' information generated or stored by the Platform", while allowing that
  Blizzard "may, at its sole and absolute discretion, allow the use of certain third-party user
  interfaces". Read literally, a desktop app that reads a SavedVariables file is "software that
  reads information stored by the Platform". In practice this is exactly what the WeakAuras
  Companion, the TradeSkillMaster desktop app and the Raider.IO client have done for years, for
  millions of players, with no enforcement action found against any of them.
- Section 4 says the game may monitor memory "for unauthorized third party programs running
  either concurrently with a game or out of process". Warden, the anti-cheat, looks for known
  cheat modules, API hooks and drivers. A Python process that reads and writes files has none of
  those signatures.
- Blizzard's 2025-08-05 forum post ("Prohibitions on Third-party Software") restates that
  "third-party software that modifies the World of Warcraft game client" is prohibited and can
  lead to permanent closure. This project modifies nothing in the client.
- Input broadcasting and mirroring (2020-11, 2021-05 posts) are prohibited. This project never
  sends input to the game, which is why the clipboard path stops at Ctrl+C: the game writes the
  clipboard, the companion reads it, and nothing is ever pasted back.
- The UI Add-On Development Policy (2009) asks add-ons to be free, with visible code, with no
  in-game advertising or donation requests, and not to harm performance. This addon is Apache-2.0,
  readable, and runs no timers.
- Midnight's addon changes (secret values for combat data) also apply to Forever. The addon
  touches no combat API.

So: the in-game half is an ordinary add-on under the add-on policy; the desktop half uses the
two file channels the established companion apps use. Honest rating: not "blessed by Blizzard"
(no Blizzard text says any companion app is), but the lowest-risk pattern that exists, with a
long track record and no known ban.

## What we deliberately left out

- **Pixel or screen reading** to get data out of the game in real time. It is the classic bot
  input channel and falls under 1.C.vi.
- **Keystroke or paste injection** to get data into the game without a reload. It is input
  automation, no matter how benign the text.
- **An overlay window.** Probably tolerated, but it is a window over the game and this project
  does not need it.
- **Automatic reloads.** A reload only happens when you click Send or Refresh (the client itself
  requires a hardware event for `C_UI.Reload`).

## Your account, your keys

- API keys never enter the game, the addon folders, logs, or the data file. The config stores
  the *name* of an environment variable or a file path, never the key.
- `doctor` and `config show` never print key material.
- With an API key, the Claude Code backend passes a per-turn budget cap (`max_budget_usd`,
  default 5 USD). With a CLI login the cap is not passed, because the CLI's cost figure is
  nominal and the cap would only cut turns short.
- Any addon you install in the game can write to any other addon's SavedVariables, including
  this addon's request queue. The companion prints every request before it runs it. Keep the
  permission mode you are comfortable with (`acceptEdits` by default; `bypassPermissions` is
  your explicit choice), and keep only trusted addons installed, as always.

## The clipboard watcher, specifically

Off by default. When on, the watcher polls the clipboard sequence number (no clipboard access
at all while nothing changes), reads the text only on a change, discards anything that does not
start with the envelope prefix without keeping a copy, a hash or a length, and never writes the
clipboard. An envelope also has to carry the per-install nonce the companion published in the
data file, so a web page or another program cannot start a run by filling your clipboard. It is
a convenience for people who do not want a reload per send; turn it on only if that trade is
worth it to you.

## Other hardening worth knowing

- The prompt is written to the Claude Code CLI's standard input, never placed on its command
  line, so prompt text can never be parsed as an option or meet a shell.
- HTTP backends refuse redirects (a redirect would otherwise carry the key to another host), and
  key material is scrubbed from error text before it reaches the log, the data file or a toast.
- A request is marked consumed before it runs, so a crash never replays an agentic session.
- `config set` refuses an `api_key_env` value that looks like a key instead of a variable name,
  and `config show` masks header values whose names look secret.
