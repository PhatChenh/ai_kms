**Make a copy:**
After running the following command the script will be moved to the machine internal folder

**Install it:**
```bash
chmod +x claude-switch.sh
sudo mv claude-switch.sh /usr/local/bin/claude-switch
```

**One-time setup (do this once per account):**
```bash
# While logged into your personal account:
claude-switch save personal

# Switch in Claude Code:  /login  → pick company org
claude-switch save work
```

**Daily use:**
```bash
claude-switch use work       # → company account
claude-switch use personal   # → your account
claude-switch whoami         # → check who's active
claude-switch list           # → see all profiles
```

**How it works:** profiles are stored as JSON snapshots in `~/.claude-profiles/` (chmod 700, files chmod 600). On macOS it reads/writes the Keychain entry directly using the `security` CLI — no plaintext on disk. On Linux it swaps `~/.claude/.credentials.json`. The `whoami` command matches your active refresh token against saved profiles to tell you which one is loaded.

**One caveat:** tokens expire. If you save a profile and don't use it for a while, the access token may be stale — Claude Code will auto-refresh it on first use, so it's usually fine, but if you hit auth errors just re-login and re-save that profile.