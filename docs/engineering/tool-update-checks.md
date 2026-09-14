# Daily local tool update reminders

The daemon inventories installed Codex, Claude Code, Pi, OpenCode and LingTai
kernel, with one persisted check per 24 hours. It probes version metadata only:
no updater, package manager install, model call, or credential probe is run.
Each CLI subprocess gets ten seconds plus bounded process-tree cleanup; remote
requests have a fifteen-second deadline and a 1 MiB response cap. Missing tools
and failed providers do not prevent other tools being checked.

`tool_updates.json` in the daemon home stores the last check, available updates,
per-operator notified versions, and pending encrypted envelopes. Pending sends
retry hourly. An envelope is persisted before transmission and reused on retry
(including after a restart), with fresh HTTP auth headers. Only a server commit
acknowledgement marks its versions notified. Corrupt state is preserved and
logged, rather than silently repeating previous notifications. One recipient's
failure does not prevent another paired recipient receiving their message.

Transport is machine-authenticated `POST /v2/machines/me/messages` with
`{operator_slug, envelope}`. `200 {ok:true}` means persisted or already present;
404/403/timeouts/errors keep the notification pending. The server reuses the
existing machine-message transaction (including active pairing checks),
envelope-ID deduplication, fanout and offline history. A running work Agent is
not required. The encrypted payload is exactly
`{type: "tool.update_available", text: string}`. Web must project both live and
backfilled payloads into System notifications, keyed by envelope ID. Agent,
Server and Web changes must be deployed together before enabling this feature.

## Release sources

- [Codex](https://github.com/openai/codex): `@openai/codex` npm dist-tags.
- [Claude Code](https://code.claude.com/docs/en/setup):
  `@anthropic-ai/claude-code`, user `autoUpdatesChannel` stable/latest.
- [Pi](https://github.com/earendil-works/pi/tree/main/packages/coding-agent):
  `@earendil-works/pi-coding-agent`; recognizable older
  `@mariozechner/pi-coding-agent` installations stay on their original package.
- [OpenCode](https://opencode.ai/docs/cli/#upgrade): `opencode-ai` dist-tags.
- [LingTai kernel](https://github.com/Lingtai-AI/lingtai-kernel/blob/main/src/lingtai/kernel/nudge/kernel_version.py):
  installed launcher venv's `lingtai` distribution metadata; the exact
  `lingtai-kernel-release-manifest.json` asset in GitHub/Gitee latest releases.
  Two available manifests must match byte-for-byte; one available mirror can
  establish the version. Kernel and TUI version numbers are independent.
- Recognized Homebrew Caskroom/Cellar paths use that cask/formula's public JSON
  version rather than npm's potentially earlier publication.

Stable installations never follow a prerelease/platform/snapshot dist-tag.
Numeric alpha/beta/rc versions use their matching tag; unsupported dev/build
versions are skipped. Pi versions at or below the legacy-node20 tag are skipped: npm does not
retain the installed dist-tag, so their legacy/latest channel is ambiguous. Unknown installation methods get the official documentation link,
not a guessed upgrade command. Only recognized Homebrew paths get a concrete
`brew upgrade` command. App-bundled CLIs and editable LingTai source installs
are skipped because their update authority is not the standalone package.

## Validation boundaries

Deterministic tests cover lost ACK → restart → same envelope retry, successful
notification dedup, daily cadence after offline checks, newer versions,
unlink during recipient lookup, stopping the idle loop, cancellation cleanup,
channel comparisons, missing/broken tool isolation, and LingTai provenance /
manifest disagreement. These do not establish every native installer layout
or Windows uv/Python 3.14 execution. No running daemon is upgraded by the tests.
