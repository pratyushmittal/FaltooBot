# Changelog

All notable changes to `faltoobot` will be documented in this file.

## 1.4.0 — 2026-04-02

### Added
- `faltoobot login` now starts the OpenAI Codex OAuth flow in your browser, saves the returned auth file, and wires `config.toml` to use it automatically.
- `faltoochat` can now use a ChatGPT/Codex subscription through an explicit `openai.oauth` auth file, so terminal coding sessions no longer require an API key.

### Changed
- OpenAI client setup is now explicit and config-driven: when both `openai.oauth` and `openai.api_key` are set, OAuth takes priority.
- Image attachments keep working in OAuth-backed coding sessions by sending inline image data instead of relying on file uploads.

## 1.3.0 — 2026-04-01

### Added
- The project now includes an Astro-powered website and docs site, with a new Mittals AI landing page and GitHub Pages deployment workflow.
- Queue items in `faltoochat` can now be pulled back into the composer with `Enter`, making it easier to edit and resend queued prompts.
- Review mode can now stage the full current file with `S`, in addition to line-level staging.

### Changed
- Agent prompts are now hard-coded in Python modules and selected automatically by session type, so coding sessions and WhatsApp chats use separate built-in instructions without prompt fields in `config.toml`.
- Review submission snippets now preserve both deleted and added lines with `-` and `+` prefixes.
- WhatsApp auth keeps the important success logs while hiding noisy reconnect and socket-close chatter.

## 1.2.1 — 2026-03-27

### Changed
- Project search in `faltoochat` is much more responsive on large repos, with ripgrep results streamed and capped so broad queries don’t peg the CPU or hang the TUI.
- Review diff navigation is smoother: `[` and `]` now jump between edit blocks, `V` supports linewise selection with `j`/`k`, and `Esc` cleanly exits selection mode.
- Review search bindings in the footer now only show when a search is active, making the footer easier to scan.

## 1.2.0 — 2026-03-27

### Added
- `faltoobot --version` and `faltoochat --version` now print the installed package version directly from the CLI.
- `faltoobot` and `faltoochat` can now search local skill bundles from `~/.faltoobot/skills` and project-local `.faltoobot/skills` folders.
- `faltoochat` now shows slash-command suggestions as you type, with keyboard navigation for quick selection.

### Changed
- Review mode in `faltoochat` is more polished: project search works even when review starts empty, `R` can close tabs that are no longer modified, and review snippets now mark deleted lines with `-`.
- Project search now falls back cleanly when `ripgrep` is not installed, while still using `rg` when available.
- Shell tool calls are easier to skim, with shorter summaries and clearer command rendering.

## 1.1.0 — 2026-03-25

### Added
- `faltoochat` now has a dedicated **Review** workspace for local git changes, with changed-file tabs, syntax-highlighted diffs, file/code search, inline review comments, and one-shot submission of review feedback back into chat.
- Review mode can stage or unstage selected diff lines directly from the terminal, making it easier to curate work before sending follow-up prompts.

### Changed
- `faltoochat` now remembers the selected light or dark theme across launches.
- Stream rendering in terminal chat is more reliable during richer replies, including web-search-heavy responses.

## 1.0.0 — 2026-03-23

### Changed
- `faltoochat` is now the new minimal terminal chat app, with a simpler and faster UI, persistent sessions, shell-tool rendering, and cleaner startup behavior.
- Terminal chat now supports queued follow-up prompts while a reply is still running, including keyboard-driven queue management.
- Image workflows in terminal chat are streamlined, with clipboard paste and local image attachments handled directly in the composer.
- WhatsApp chats now support incoming image messages, including captioned images, image-only prompts, and multi-image albums grouped into one model turn.
- Thinking blocks now show only the visible summary instead of full reasoning detail.

### Added
- Startup now loads recent chat history first and offers a quick `load all` link for older messages.
- Terminal chat has better focus styling, multiline input with `Shift+Enter`, and clearer summaries for common `sed` and `rg` shell calls.
- `faltoobot makemigrations` and a built-in migration runner for future releases.

### Removed
- Legacy terminal chat implementation, `faltoomac`, and older unused agent/store codepaths.

## 0.5.0 — 2026-03-19

### Added
- Native macOS desktop chat app, available via `faltoomac`.

### Changed
- Terminal chat queue and slash-command panels are clearer, with titled bordered sections and more room for command names.
- Queue reordering in terminal chat is now keyboard-only via `Shift+↑` and `Shift+↓`.

## 0.4.0 — 2026-03-17

### Added
- Terminal chat now supports saved prompt slash commands from `~/.faltoobot/prompts/`, plus `Tab` completion for slash command suggestions.

### Changed
- Shell tool calls for common `sed` and `rg` commands are summarized in a more readable way, including commands prefixed with `cd ... &&`.
- README now documents background installs, status checks, and log viewing for the WhatsApp bot service.

### Fixed
- Pasted screenshot paths with shell-escaped spaces are recognized as images again.
- Transcript auto-scroll behavior was reworked so streaming replies follow more reliably without forcing the view down after you scroll up.

## 0.3.0 — 2026-03-17

### Added
- `faltoobot install` now supports both macOS and Linux, and starts the WhatsApp bot in the background.

### Changed
- `faltoobot logs` now colorizes log output so errors and warnings are easier to spot.
- Background installs now run the packaged `faltoobot` entrypoint directly instead of depending on `uv run` from the repo checkout.

## 0.2.4 — 2026-03-17

### Changed
- Thinking blocks now show only bold summary text when the model includes a highlighted summary.

### Fixed
- Shell tool output no longer crashes the chat when a command prints non-UTF-8 bytes.

## 0.2.3 — 2026-03-16

### Fixed
- WhatsApp allowlisting now ignores numeric device suffixes in sender IDs, so follow-up messages from the same contact keep matching `allowed_chats`.

## 0.2.2 — 2026-03-16

### Added
- WhatsApp now shows the bot as typing while it is processing a reply.

## 0.2.1 — 2026-03-16

### Changed
- CLI output now uses Rich for cleaner configuration, status, and path displays.

### Fixed
- WhatsApp allowlisting now also matches phone numbers when WhatsApp includes the country code but your `allowed_chats` entry does not.

## 0.2.0 — 2026-03-16

### Added
- Slash-command autocomplete in terminal chat, with `/` showing available commands and `Esc` dismissing the menu.

### Changed
- Terminal chat now shows your submitted message immediately instead of waiting for the reply stream to begin.
- Default configuration now uses `gpt-5.4` with `thinking = "high"`.
- Auth output now makes successful WhatsApp pairing clearer and points users to the next step.

### Fixed
- WhatsApp allowlisting now matches alternate sender IDs, so allowed phone numbers still work when WhatsApp delivers messages through `@lid` identities.

## 0.1.1 — 2026-03-16

### Added
- OpenAI fast mode in `config.toml`, mapped to priority requests.
- `(fast)` indicator in the chat footer when fast mode is enabled.
- README install and quick-start updates for `uv tool install faltoobot`.

### Changed
- Streamed assistant markdown now commits into normal markdown blocks after the reply finishes.
- Chat UI redraws less often, making queue navigation and general interaction feel more immediate.
- Queue keyboard flow now uses `Tab` to move between the composer and queued messages.
- Queued messages are rendered more compactly with tighter spacing.
- README now explains the WhatsApp account setup and safer `allowed_chats` usage.

### Fixed
- Live replies no longer stay in the unformatted streaming style after completion.
- Auto-scroll now follows new submissions without yanking the user back down after they scroll up.
- Text paste no longer duplicates on `Cmd+V`.
- Very long pasted text no longer crashes path detection with `OSError: [Errno 63] File name too long`.
- `Up` works again for multiline composer editing unless the queue is explicitly selected.

## 0.1.0 — 2026-03-16

### Added
- Initial PyPI release of `faltoobot`.
- WhatsApp bot mode with authentication, run, update, logs, and macOS service commands.
- Terminal chat mode via `faltoobot chat` and `faltoochat`.
- Local session history, queued prompts, tool output rendering, and image paste support.
