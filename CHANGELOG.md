# Changelog

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
