"""wowclaude: the desktop companion for the ClaudeCode World of Warcraft addon.

The addon can only read and write its own SavedVariables. This companion watches
that file, runs each request against a backend (the Claude Code CLI, any
OpenAI-compatible endpoint such as OpenRouter or a local server, or the
Anthropic Messages API) and writes the results into the ClaudeCode_Data addon,
which the game picks up on /reload. It never touches the game process.
"""

__version__ = "0.1.1"
