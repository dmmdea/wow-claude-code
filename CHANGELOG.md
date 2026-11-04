# Changelog

## 0.1.0 (2026-11-04)

First release.

- `ClaudeCode` addon: in-game window with sessions, transcript, prompt, project and backend
  pickers, Send / Copy / Stop / Refresh / New, `/claude` and `/cc` slash commands, a keybinding
  and an addon compartment entry. Interface 16001 (Forever), 120107 (retail), 11509 (Classic Era).
- `ClaudeCode_Data` addon: the bridge file the companion writes.
- `wowclaude` companion (Python 3.10+, standard library only): `init`, `install`, `run`,
  `doctor`, `selfcheck`, `config`; backends for the Claude Code CLI, any OpenAI-compatible chat
  endpoint and the Anthropic Messages API; opt-in clipboard send path; desktop notifications.
