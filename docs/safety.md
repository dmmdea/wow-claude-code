# Safety: staying on the right side of the game's rules

This project exists because its author wants to use it on an account that matters. Every
design choice below starts from "give Blizzard nothing to look at", and this page shows the
reasoning so you can judge it yourself. One part, the live slots, goes further than the older
companion apps do, and the section on it says what we know and what we do not. Nothing here is
legal advice.

## What the companion does and does not do

Does:

- Read the SavedVariables file the game writes for the `ClaudeCode` addon
  (`WTF/Account/<account>/SavedVariables/ClaudeCode.lua`).
- Write Lua files into its own addon folders under `Interface/AddOns`: `ClaudeCode_Data.lua` and
  `ClaudeCode_Live.lua` in `ClaudeCode_Data/`, and, while the game runs, one file, `Live.lua`,
  inside each of its live slot folders (`ClaudeCode_Live001`, `ClaudeCode_Live002`, ...).
- Run the Claude Code CLI, or an HTTP request to the endpoint you configured, on your PC.
- When `clipboard_watch` is true (`init` asks; yes is the default on a first run), read the OS clipboard after you press
  Ctrl+C in the game's own text box, acting only on text that starts with `CLAUDECODE-WOW:1:` and
  carries this install's nonce.
- Show a desktop notification.

Does not, ever:

- Open a handle to the game process, inject code, hook anything, or read its memory.
- Send keystrokes, mouse input or clipboard pastes into the game, or write to the clipboard.
- Capture the screen or read pixels.
- Draw an overlay over the game window.
- Place any file beside the game executable or anywhere outside the addon folders it installs: the
  two addon folders and the live slot folders.
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
  in-game advertising or donation requests, and not to harm performance. This addon is Apache-2.0
  and readable. Its only repeating timer runs while an answer is pending and only looks at the
  addon's own live slots. The few one-shot timers finish what a key press or a redraw started:
  letting go of the keyboard after Ctrl+C, scrolling to the newest line. While the window is open
  it also listens for the client's mouse-press event, only to give the keyboard back to the game
  when you click outside the window.
- Midnight's addon changes (secret values for combat data) also apply to Forever. The addon
  touches no combat API.

So: the in-game half is an ordinary add-on under the add-on policy; the desktop half reads
SavedVariables and writes addon files, the two file channels the established companion apps use.
The live slots add one thing those apps are not known for, which is writing an addon file while
the game runs and having the addon load it; the next section covers that. Honest rating: not
"blessed by Blizzard" (no Blizzard text says any companion app is), but a low-risk pattern with a
long track record and no known ban for the parts that match the established apps.

## The live slots: what they do, and what we do not know

What they do. `install` creates folders `ClaudeCode_Live001`, `ClaudeCode_Live002`, ... (200 by
default), each a load-on-demand addon with one file. While the game runs, the companion rewrites
one file inside each of its own slot addon folders. The addon loads a slot through
`C_AddOns.LoadAddOn`, a public addon API, and the client reads the file from disk at that moment.
Nothing touches the game process, its memory, its window or its input, and nothing is automated:
the repeating timer only looks at the addon's own local slots while an answer is pending, the
addon never reloads the UI by itself and never sends input.

Prior art. The slot pool idea is not ours. Other public projects for this client use it; we credit
[chelinho139/wow-ai](https://github.com/chelinho139/wow-ai), which documents it in
`docs/ARCHITECTURE.md` under "Inbound: load-on-demand slots" and ships a pool of 200 slots. At
least one fork of it does the same. A 2014 forum post on WoWInterface also describes the idea as a
suggestion. Other parts of that project, such as reading the screen to get data out of the game, are
not used here.

The open question. In a 2014 WoWInterface forum thread about this idea
(<https://www.wowinterface.com/forums/showthread.php?t=49982>), two long-time forum members said that
an external program changing addon data while the game runs, to get around the addon sandbox, is
against the Terms of Use, and contrasted it with addon updaters, which are fine. That is forum
opinion from 2014, not a Blizzard statement, and we found no Blizzard statement either way. So this
project cannot promise the technique is allowed. The slot files change while the game is running,
and an external program changes them. Which side of that line this falls on is not something we
can tell you, so decide for yourself whether it is a risk you accept.

The off switch.

- `python -m wowclaude config set live_slots 0`, then `python -m wowclaude install`, removes the
  slot folders. The companion then writes no live copy.
- `/claude live off` in the game stops the addon from loading them.

Answers are then read on a `/reload` only, exactly like 0.1 and like any addon updater that changes
files between reloads. Sending still works without a reload through the clipboard path, or with one
when that path is off.

## What we deliberately left out

- **Pixel or screen reading** to get data out of the game in real time. It is the classic bot
  input channel and falls under 1.C.vi.
- **Keystroke or paste injection** to get data into the game. It is input automation, no matter
  how benign the text. Answers come in through the addon loader instead, and requests go out
  through a line you copy yourself.
- **An overlay window.** Probably tolerated, but it is a window over the game and this project
  does not need it.
- **Automatic reloads.** A reload only happens when you act: a click on Reload UI, a send while the
  clipboard path is off, or `/claude reload` (the client itself requires a hardware event for
  `C_UI.Reload`). No timer reloads the UI.

## Your account, your keys

- API keys never enter the game, the addon folders, logs, or the data file. The config stores
  the *name* of an environment variable or a file path, never the key.
- `doctor` and `config show` never print key material.
- With an API key, the Claude Code backend passes a per-turn budget cap (`max_budget_usd`,
  default 5 USD). With a CLI login the cap is not passed, because the CLI's cost figure is
  nominal and the cap would only cut turns short.
- A backend's `env` option (environment variables for the CLI process, used to point it at a login
  kept outside the default folder) refuses names that look like credentials and values that carry
  one (a password inside a URL, an authorization header), so a key cannot be saved there by
  mistake. The refusal never repeats the value, and `config show` and `config get` mask such an
  entry in a hand-edited file. Keys go in `api_key_env` or `api_key_file` only.
- Any addon you install in the game can write to any other addon's SavedVariables, including
  this addon's request queue. The companion prints every request before it runs it. Keep the
  permission mode you are comfortable with (`acceptEdits` by default; `bypassPermissions` is
  your explicit choice), and keep only trusted addons installed, as always.

## The clipboard watcher, specifically

On when `clipboard_watch` is true; `init` asks, and on a first run the default answer is yes (a
later `init` keeps what the config says). You press Ctrl+C yourself on the line the window selected. The watcher polls the clipboard sequence number (no clipboard
access at all while nothing changes), reads the text only on a change, discards anything that does
not start with the envelope prefix without keeping a copy, a hash or a length, and never writes the
clipboard or pastes into the game. An envelope also has to carry the per-install nonce the
companion published in the data file, so a web page or another program cannot start a run by
filling your clipboard. It is a convenience for people who do not want a reload per send. If you
would rather the companion never read your clipboard, run
`python -m wowclaude config set clipboard_watch false` and restart the companion; a send then
reloads the UI, as in 0.1.

## Other hardening worth knowing

- The prompt is written to the Claude Code CLI's standard input, never placed on its command
  line, so prompt text can never be parsed as an option or meet a shell.
- HTTP backends refuse redirects (a redirect would otherwise carry the key to another host), and
  key material is scrubbed from error text before it reaches the log, the data file or a toast.
- A request is marked consumed before it runs, so a crash never replays an agentic session.
- `config set` refuses an `api_key_env` value that looks like a key instead of a variable name,
  and `config show` masks header values whose names look secret.
- `uninstall` removes the two addon folders and only the live slot folders `install` created, which
  it recognizes by their name and a marker line in their TOC file.
- `doctor --live` runs its one real turn in your default project folder, or in an empty temporary
  folder when there is none, never in whatever folder the terminal happens to be in.
- If the data file cannot be replaced for a moment (another program holds it open), the companion
  keeps running: it logs the failure, still publishes the live copy and writes the file again a
  few seconds later.
