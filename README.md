![cloud — multi-Nextcloud CLI](assets/banner.png)

# cloud — multi-Nextcloud CLI

A thin Python CLI on top of `rclone` that gives you real VFS mounts, multi-account management, and gogcli-style public share URLs for your Nextcloud(s).

## Why this exists

Nextcloud Desktop on Linux is not a serious tool for keeping multiple accounts and large stores usable:

- **No real VFS.** The Linux client only does "suffix placeholders" (`*.nextcloud` files), which break `find`, `grep`, IDE indexers, Obsidian, and basically any tool that walks a tree. The maintainer confirmed there is no plan to add FUSE.
- **No CLI surface.** You can't script it, you can't drive it from an agent, you can't `cloud doctor` your way out of a stuck mount.
- **Multi-account is clunky.** Multiple desktop processes, multiple sync dirs, no unified status, share URLs require GUI clicks.

`rclone` already solves all of this — real FUSE VFS, multi-backend, scriptable. What was missing was a thin opinionated wrapper that:

- Keeps one TOML as the source of truth for all your Nextcloud accounts
- Generates and maintains `rclone.conf` and systemd user units from it
- Adds a `cloud doctor` that catches stale mounts, FUSE missing, cache full
- Exposes `cloud push file remote:path --share` returning the public URL on stdout (same UX as gogcli for Google Drive)
- Stays out of your way: idempotent commands, explicit exit codes, no surprises after the initial `account add`

## 30-second taste

```bash
cloud account add work https://nc.example.com/remote.php/dav/files/me
# prompts for username + Nextcloud app password

cloud mount work --mode vfs --auto
ls ~/clouds/work/                              # browse, no downloads
cat ~/clouds/work/notes.md                     # this file gets cached, others don't

cloud push report.pdf work:reports/q2.pdf --share
# → ✓ Uploaded report.pdf → work:reports/q2.pdf
# → ✓ Public link: https://nc.example.com/s/aBcDeFgH

cloud status   # one-line state per account
cloud doctor   # diagnostics
```

## Install & reference

See [INSTALL.md](INSTALL.md) for prerequisites, the full command reference, the config schema, exit codes, and the systemd auto-mount section.

## What's in, what's coming

See [ROADMAP.md](ROADMAP.md).

## License

[MIT](LICENSE).
