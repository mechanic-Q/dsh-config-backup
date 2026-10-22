# DSH backup - desktop profile

Created 2026-10-22T12:39:56.709Z on DESKTOP-NVPJALJ (win32-x64), dsh 0.2.0-rc.2.
Credentials: skipped - sign in / re-enter API keys on the new machine, or re-run with --with-credentials
Sessions: not included (re-run with --with-history)
Usage ledger: not included (re-run with --with-usage)

## What is in here

- core-config: 16 files, 0.02 MB  - DSH top-level state: AGENTS.md, pet, skin, remote devices, undo/telemetry/market
- profiles: 29 files, 1.52 MB  - profile plugin graph: package.json, pnpm-lock.yaml, pnpm-workspace.yaml, cordis*.yml
- storages: 4 files, 0.42 MB  - namespaced stores: MCP connectors, workspaces, schedules, grants
- plugin-data: 21 files, 0.35 MB  - per-plugin runtime state (task board, IM bridge, channel pack, ...)
- skills: 95 files, 0.25 MB  - skills under DSH_HOME/skills
- agents-skills: 3483 files, 11.71 MB  - project-level skills (~/.agents/skills) - FULL trees, not SKILL.md only
- knowledge: 29 files, 0.16 MB  - agent-procedures/, procedures/ (distilled lessons), patches/
- tools: 2 files, 0.00 MB  - DSH_HOME/bin scripts + ~/.gitconfig commit identity
- autostart: 1 files, 0.00 MB  - Windows scheduled tasks + Startup shortcuts that keep the setup running
- credentials: NOT PRESENT on the source machine  - .credentials.yaml (encrypted when --with-credentials)
- sessions: NOT PRESENT on the source machine  - conversation history (opt-in via --with-history)
- history: NOT PRESENT on the source machine  - cost ledger, attachments (opt-in via --with-usage / --with-history)

## Restore on another machine

Requirements: Node 18+ and tar. Nothing else - this backup carries its own
restore tool (dsh-migrate.mjs) and the pnpm install is optional.

Fastest path (from the pushed GitHub repo):
  node dsh-migrate.mjs restore --repo <owner>/<repo> --install

From this archive:
  node dsh-migrate.mjs restore <this-archive> --install

Verify without touching anything:
  node dsh-migrate.mjs verify <this-archive>

Steps:
1. QUIT DeepSeek Harness first - the desktop profile is locked while it runs.
2. Run one of the commands above. --install runs pnpm install --frozen-lockfile;
   skip it if pnpm is missing, then just start DSH once and its plugin manager
   installs from the restored package.json + pnpm-lock.yaml (needs network).
3. Everything user-facing lives in profiles/<profile>/cordis.patch.yml: providers,
   models, baseURLs, UI settings, plugin config. Check that file first if the UI
   looks different after restore.
4. Skills under _agents/ restore to ~/.agents/skills (override with DSH_AGENTS_SKILLS).
5. Credentials: this backup says "skipped - sign in / re-enter API keys on the new machine, or re-run with --with-credentials". If encrypted, export
   DSH_BACKUP_PASSPHRASE before restoring. On a fresh machine you can also just
   swap ONE key in .credentials.yaml (or in Settings) and keep everything else.
6. Workspace paths in storages/workspace.json point at the OLD machine - delete or
   re-add workspaces whose folders do not exist.
7. Restoring never overwrites silently: previous files are copied to
   <DSH_HOME>/backups/pre-restore-<timestamp>/ first.
8. Rollback if it goes wrong: copy that pre-restore folder back over <DSH_HOME>.
9. _autostart/ holds the Windows scheduled tasks (import with: schtasks /create
   /xml "<this archive>/_autostart/tasks/DSH-TempClean.xml" /tn DSH-TempClean) and
   any Startup shortcuts. They are NOT installed automatically - a task that
   deletes files must be a deliberate decision on the new machine.
10. _tools/gitconfig is the commit identity (~/.gitconfig) used to push this
   backup; copy it over if git on the new machine has none.

## Credentials deliberately left out

These files hold a secret as their whole content and were NOT staged:
- openai-gateway/api-key
- dsh-pocket/token
- dsh-pocket/token-lan

Re-enter them on the target machine (Settings / the owning plugin).
