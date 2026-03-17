# Changelog

All notable changes to `faltoobot` will be documented in this file.

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
