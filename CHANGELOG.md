# Changelog

All notable changes to `faltoobot` will be documented in this file.

## 3.2.3 — 2026-04-25

### Changed
- Default OpenAI model is now `gpt-5.5`; update migrates configs that were still using the previous default.
- `/status` now includes the current session ID and workspace.

### Fixed
- Conversation compaction now starts at the intended 200k token threshold.

## 3.2.2 — 2026-04-25

### Changed
- WhatsApp group prompts now use the primary sender ID in `[from ...]` labels so it lines up better with mention references.

## 3.2.1 — 2026-04-25

### Changed
- Updated locked Python and website dependencies, plus deploy workflow actions.

### Fixed
- Python tool calls now receive configured OpenAI and Gemini API keys, matching shell tool behavior.

## 3.2.0 — 2026-04-25

### Added
- Faltoochat sessions can now be named, resumed from a picker, and are sorted by recent use.
- Review diff navigation now supports jumping back to the previous cursor position with `Ctrl+O`.

### Changed
- The bundled image-generation skill now uses OpenAI image examples.
- WhatsApp group and album turn handling is simpler and more consistent, including addressed slash commands in groups.
- Update now runs a simpler built-in migration step instead of the old versioned migration runner.

### Fixed
- OpenAI OAuth login now URL-encodes authorization parameters more safely.
- Review refresh no longer crashes when an active untracked file is deleted.
- Update now removes obsolete session `last_used` marker files.

## 3.1.3 — 2026-04-20

### Changed
- WhatsApp group chats now keep recent unmentioned messages as context for later mentions and replies.
- Group turns now include the sender name in stored chat context.

## 3.1.2 — 2026-04-20

### Changed
- Fixed slash-command debounce handling.

## 3.1.1 — 2026-04-20

### Changed
- Refined the Python skill docs for `run_in_python_shell`, including the multi-turn image-generation examples.

## 3.1.0 — 2026-04-20

### Added
- `run_in_python_shell` for multi-step Python work and optional integrations.

### Changed
- Python skill examples now use `run_in_python_shell`.
- Shell commands use their normal shell environment again.

## 3.0.5 — 2026-04-20

### Fixed
- Shell `python` commands now reuse Faltoochat's Python environment.

## 3.0.4 — 2026-04-18

### Added
- Added `textual-speedups` to improve Textual rendering performance.

### Changed
- Refreshed locked dependencies.

### Fixed
- Review comments now show real file line numbers without slowing diff loading.

## 3.0.3 — 2026-04-18

### Fixed
- Codex OAuth chats now fall back cleanly when older history includes uploaded files that OAuth cannot fetch.
- Review comments now use real file line numbers instead of diff-row positions.
- WhatsApp turn storage and debounce handling are more reliable when messages arrive close together.

## 3.0.2 — 2026-04-18

### Fixed
- Rebuilt the package from the corrected post-rebase source tree so the slash-command Enter fix is included.
- Keeps the Codex OAuth header fix that restores ChatGPT-backed logins.

## 3.0.1 — 2026-04-18

### Fixed
- Updated the Codex OAuth request headers to restore ChatGPT-backed logins.

## 3.0.0 — 2026-04-17

### Changed
- Exact `faltoochat` slash commands now complete the current turn instead of reopening editable text.
- WhatsApp group replies are now controlled by `allow_group_chats`.
- Staging a whole file in Review now moves to the next file tab.

### Removed
- Removed the old `allow_groups` config flag. `allow_group_chats` is now the single control for group-chat replies.
- Removed the `faltoobot allow-group-chats` CLI shortcut.

### Fixed
- WhatsApp group replies now require the bot to be explicitly addressed in the group.
- Saved slash commands and built-in slash commands now share the same composer flow.

## 2.5.0 — 2026-04-15

### Added
- Faltoochat composer and review inputs now support `@` file mentions for faster file targeting.

### Changed
- Review now uses a simpler `Ctrl+D` edit flow that works better with vi-style editing.
- Faltoochat submit handling is more consistent across composer, review, and queued auto-submit paths.

### Fixed
- Review jumps now allow targeting line zero correctly.
- Empty selected lines now stay included in review selections.

## 2.4.1 — 2026-04-11

### Changed
- Review diffs now start with line highlighting enabled, and browser/tool runtime configuration is read more consistently from current config state instead of baked service-script environment exports.

### Fixed
- Review now expands untracked directories into reviewable files instead of trying to diff the directory itself.

## 2.4.0 — 2026-04-11

### Added
- Faltoochat now includes a keybindings command-palette modal and review-page scroll bindings for easier navigation and discoverability.

### Changed
- System prompts are now cached per session, so prompt edits do not invalidate the active conversation mid-session.
- Browser session reuse is more reliable when attaching Faltoochat automation to an existing logged-in browser profile.
- Review comment quoting and selection behavior are more polished, including scrollable long selections and cleaner quoting in the modal.

### Fixed
- Review diff colors now refresh correctly when the app theme changes.
- Switching tabs while a modal is open now keeps focus in the modal instead of unexpectedly returning focus to the composer.

## 2.3.0 — 2026-04-10

### Added
- Review diff now highlights added, removed, staged, and reviewed lines with softer theme-aware colors, and diff wrapping is enabled by default.

### Changed
- `faltoobot update` and `faltoobot configure` now update the user's crontab `PATH`, so cron jobs can find `faltoochat` and `faltoobot` more reliably.
- Browser setup and login flows are now better aligned with real browser sign-in behavior.

### Fixed
- Non-WhatsApp CLI commands no longer fail on fresh macOS installs when `libmagic` is missing.
- WhatsApp replies now include quoted-reply context more cleanly, truncate very long quoted messages, and keep voice-note transcripts in a single pass.

## 2.2.1 — 2026-04-08

### Changed
- Code and WhatsApp prompts now direct long-term memory requests into `AGENTS.md`, and new chat workspaces automatically create an empty `AGENTS.md` file.

### Fixed
- Reverted the experimental memory-skill integration, keeping only the lighter `AGENTS.md`-based memory approach.

## 2.2.0 — 2026-04-08

### Added
- Gemini image-generation support can now be configured from `faltoobot configure`, including a bundled `image-generation` skill with examples for text-to-image, image editing, multi-turn updates, and live-data prompts.

### Changed
- The image-generation skill now uses Gemini `interactions.create(...)`, prints reusable response IDs, and documents how to return the final expanded prompt back to the user.

## 2.1.3 — 2026-04-08

### Fixed
- `faltoobot update` now re-execs itself after `uv tool upgrade`, so a single update run continues with the newly installed version and properly refreshes background services.

## 2.1.2 — 2026-04-08

### Fixed
- WhatsApp media replies now quote the full incoming event when sending images or documents, which fixes media replies breaking with newer `neonize` releases.

## 2.1.1 — 2026-04-08

### Changed
- Refreshed Python and pre-commit dependencies, including newer `ruff`, `ty`, `pytest`, `textual`, `pillow`, and `neonize` releases.

### Fixed
- `faltoobot update` once again prompts for newly required config sections like `[browser]` when they are missing from an older `config.toml`.

## 2.1.0 — 2026-04-08

### Added
- `faltoobot browser` launches a persistent Playwright/Chromium session with a reusable profile, and `faltoobot configure` can install and save a browser binary for it.
- A bundled `browser-use` skill and a `load_image` tool make it easier to automate JS-heavy sites and inspect saved screenshots in the same chat.

### Changed
- WhatsApp replies can now send local images and documents when the assistant emits one-media-per-line markup like `![caption](path/to/file.png)`.

### Fixed
- Codex OAuth replies now preserve raw response output items, which fixes WhatsApp replies and other assistant-text extraction when `response.completed.output` is empty.

## 2.0.3 — 2026-04-07

### Changed
- WhatsApp prompt guidance now asks the bot to share plain links like `[https://example.com]` or `https://example.com`, instead of markdown links that render poorly in chat.

## 2.0.2 — 2026-04-07

### Fixed
- OpenAI Codex OAuth login now works better on remote servers: after finishing the browser flow on your local machine, you can paste the final callback URL back into Faltoobot to complete login.

## 2.0.1 — 2026-04-07

### Changed
- Simplified the bundled `notification-listener` skill frontmatter.

## 2.0.0 — 2026-04-07

### Added
- `faltoobot notify` can now queue notifications into an ongoing chat, and the bundled `notification-listener` skill shows practical cron, sub-agent, and scripting patterns.

### Changed
- Notification queue items now carry a `source`, and WhatsApp notification turns can suppress user-facing replies with `[noreply]`.

### Removed
- `faltoochat --notify-chat-key` and the bundled `scheduled-subagents` skill were removed. Pipe `faltoochat` output into `faltoobot notify` instead.

## 1.6.1 — 2026-04-07

### Fixed
- `faltoochat` now persists and resumes streamed tool calls correctly in ChatGPT/Codex OAuth sessions, so shell-tool turns no longer stop after rendering the tool call in the transcript.

## 1.6.0 — 2026-04-06

### Added
- `faltoochat` now supports one-shot sub-agent runs with `--notify-chat-key`, plus a bundled `scheduled-subagents` skill for background jobs, cron tasks, and delayed follow-ups.
- A lightweight notify queue now lets sub-agents send results back into terminal chat and WhatsApp sessions.

### Changed
- Coding, sub-agent, and WhatsApp built-in prompts are now selected from session type, and prompt templates support a configurable `bot_name`.
- `faltoobot update` and `faltoobot configure` now refresh bundled skills alongside the rest of the local install.

### Fixed
- WhatsApp and terminal chat now share the updated turn/session flow, including notification polling, normalized per-chat locking, and one-shot output handling.

## 1.5.2 — 2026-04-04

### Fixed
- Review now closes files after you stage the whole file.
- Improved input token cache hits when using Codex OAuth. Provide skill names sorted alphabetically. Added `session_id` to requests.

### Changed
- `faltoochat` has a refreshed set of rotating chat placeholders.

## 1.5.1 — 2026-04-02

### Fixed
- Local skill tool tests are now deterministic across environments, and the `load_skill` tool docstring is formatted correctly for tool schema parsing.

## 1.5.0 — 2026-04-02

### Added
- Review mode now has an `add` view you can toggle with `m`, so you can inspect the current file without deleted lines getting in the way.
- Local skills are now loaded from `~/.faltoobot/skills`, `~/.agents/skills`, and `<workspace>/.skills`, and `faltoochat` only exposes the `load_skill` tool when skills are available.

### Changed
- WhatsApp login code now lives in a dedicated login module, which makes the auth flow easier to maintain and test.
- The README now publishes test and coverage badges, and pre-commit enforces the repo coverage threshold.

## 1.4.1 — 2026-04-02

### Fixed
- Chat requests now omit `service_tier` unless fast mode is enabled, which fixes OpenAI Codex OAuth sessions failing against the ChatGPT backend with `Unsupported service_tier: default`.

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
