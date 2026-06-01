#!/usr/bin/env bash
# claude-switch — quickly swap Claude Code accounts
# Supports macOS (Keychain) and Linux (~/.claude/.credentials.json)
#
# SETUP (one-time per account):
#   1. Log into the account you want to save via `claude /login`
#   2. Run:  claude-switch save personal
#   3. Switch to your other account via `claude /login`
#   4. Run:  claude-switch save work
#
# USAGE:
#   claude-switch list           — show saved profiles
#   claude-switch save <name>    — snapshot current credentials as <name>
#   claude-switch use <name>     — switch to <name>
#   claude-switch whoami         — show which profile is active (best-effort)
#   claude-switch delete <name>  — remove a saved profile

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
STORE_DIR="${HOME}/.claude-profiles"
KEYCHAIN_SERVICE="Claude Code-credentials"   # macOS keychain service name
CREDS_FILE="${HOME}/.claude/.credentials.json"

# ── Helpers ───────────────────────────────────────────────────────────────────
os_type() {
  case "$(uname -s)" in
    Darwin) echo "macos" ;;
    Linux)  echo "linux" ;;
    *)      echo "unsupported" ;;
  esac
}

die()  { echo "❌ $*" >&2; exit 1; }
ok()   { echo "✅ $*"; }
info() { echo "   $*"; }

mkdir -p "$STORE_DIR"
chmod 700 "$STORE_DIR"

# ── Read current credentials (raw JSON) ───────────────────────────────────────
read_current_creds() {
  case "$(os_type)" in
    macos)
      security find-generic-password -s "$KEYCHAIN_SERVICE" -w 2>/dev/null \
        || die "No Claude Code credentials found in Keychain. Are you logged in?"
      ;;
    linux)
      [[ -f "$CREDS_FILE" ]] \
        || die "No credentials file at $CREDS_FILE. Are you logged in?"
      cat "$CREDS_FILE"
      ;;
    *)
      die "Unsupported OS: $(uname -s)"
      ;;
  esac
}

# ── Write credentials (raw JSON) into the active slot ────────────────────────
write_creds() {
  local json="$1"
  case "$(os_type)" in
    macos)
      # Delete old entry first, then add new one
      security delete-generic-password -s "$KEYCHAIN_SERVICE" 2>/dev/null || true
      security add-generic-password \
        -s "$KEYCHAIN_SERVICE" \
        -a "${USER}" \
        -w "$json" \
        -U
      ;;
    linux)
      mkdir -p "$(dirname "$CREDS_FILE")"
      echo "$json" > "$CREDS_FILE"
      chmod 600 "$CREDS_FILE"
      ;;
    *)
      die "Unsupported OS: $(uname -s)"
      ;;
  esac
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_save() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "Usage: claude-switch save <name>"

  local profile_file="${STORE_DIR}/${name}.json"
  local creds
  creds="$(read_current_creds)"

  echo "$creds" > "$profile_file"
  chmod 600 "$profile_file"

  # Try to extract a hint about the account (email or org) from the token
  local hint=""
  if command -v python3 &>/dev/null; then
    hint=$(echo "$creds" | python3 -c "
import sys, json, base64, re
try:
    d = json.load(sys.stdin)
    token = d.get('claudeAiOauth', {}).get('accessToken', '')
    # JWT middle segment
    parts = token.split('.')
    if len(parts) == 3:
        padded = parts[1] + '=='
        payload = json.loads(base64.urlsafe_b64decode(padded))
        print(payload.get('email', payload.get('sub', '')))
except Exception:
    pass
" 2>/dev/null || true)
  fi

  ok "Saved profile '${name}'${hint:+ (${hint})}"
}

cmd_use() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "Usage: claude-switch use <name>"

  local profile_file="${STORE_DIR}/${name}.json"
  [[ -f "$profile_file" ]] || die "Profile '${name}' not found. Run: claude-switch list"

  local creds
  creds="$(cat "$profile_file")"
  write_creds "$creds"

  ok "Switched to profile '${name}'"
  info "Run 'claude' to start a session with this account."
}

cmd_list() {
  local profiles=("${STORE_DIR}"/*.json)

  if [[ ! -e "${profiles[0]}" ]]; then
    info "No profiles saved yet."
    info "Log in with 'claude /login', then run: claude-switch save <name>"
    exit 0
  fi

  echo "Saved profiles:"
  for f in "${profiles[@]}"; do
    local name
    name="$(basename "$f" .json)"
    local hint=""
    if command -v python3 &>/dev/null; then
      hint=$(python3 -c "
import sys, json, base64
try:
    d = json.load(open('$f'))
    token = d.get('claudeAiOauth', {}).get('accessToken', '')
    parts = token.split('.')
    if len(parts) == 3:
        padded = parts[1] + '=='
        payload = json.loads(base64.urlsafe_b64decode(padded))
        print(payload.get('email', payload.get('sub', '')))
except Exception:
    pass
" 2>/dev/null || true)
    fi
    echo "  • ${name}${hint:+  →  ${hint}}"
  done
}

cmd_whoami() {
  local creds
  creds="$(read_current_creds)"

  local hint=""
  if command -v python3 &>/dev/null; then
    hint=$(echo "$creds" | python3 -c "
import sys, json, base64
try:
    d = json.load(sys.stdin)
    token = d.get('claudeAiOauth', {}).get('accessToken', '')
    parts = token.split('.')
    if len(parts) == 3:
        padded = parts[1] + '=='
        payload = json.loads(base64.urlsafe_b64decode(padded))
        email = payload.get('email', '')
        sub   = payload.get('sub', '')
        org   = payload.get('org', payload.get('organization', ''))
        out   = email or sub
        if org: out += f'  (org: {org})'
        print(out)
except Exception:
    pass
" 2>/dev/null || true)
  fi

  if [[ -n "$hint" ]]; then
    echo "Current account: ${hint}"
  else
    echo "Current account: (could not decode token — you are logged in, but account details aren't readable)"
  fi

  # Best-effort: check which saved profile matches
  local profiles=("${STORE_DIR}"/*.json)
  if [[ -e "${profiles[0]}" ]]; then
    local current_token
    current_token=$(echo "$creds" | python3 -c "
import sys,json; d=json.load(sys.stdin); print(d.get('claudeAiOauth',{}).get('refreshToken',''))
" 2>/dev/null || true)

    for f in "${profiles[@]}"; do
      local saved_token
      saved_token=$(python3 -c "
import json; d=json.load(open('$f')); print(d.get('claudeAiOauth',{}).get('refreshToken',''))
" 2>/dev/null || true)
      if [[ -n "$current_token" && "$current_token" == "$saved_token" ]]; then
        echo "Matches saved profile: $(basename "$f" .json)"
        break
      fi
    done
  fi
}

cmd_delete() {
  local name="${1:-}"
  [[ -n "$name" ]] || die "Usage: claude-switch delete <name>"

  local profile_file="${STORE_DIR}/${name}.json"
  [[ -f "$profile_file" ]] || die "Profile '${name}' not found."

  rm "$profile_file"
  ok "Deleted profile '${name}'"
}

cmd_help() {
  cat <<EOF
claude-switch — Claude Code account switcher

SETUP (one-time per account):
  Log in via \`claude /login\`, then:
    claude-switch save personal    # snapshot as "personal"
  Switch accounts via \`claude /login\`, then:
    claude-switch save work        # snapshot as "work"

COMMANDS:
  claude-switch list              List saved profiles
  claude-switch save <name>       Save current credentials as <name>
  claude-switch use <name>        Switch to saved profile <name>
  claude-switch whoami            Show current active account
  claude-switch delete <name>     Remove a saved profile
  claude-switch help              Show this help

PROFILES stored in: ${STORE_DIR}/
EOF
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
CMD="${1:-help}"
shift || true

case "$CMD" in
  save)   cmd_save "$@" ;;
  use)    cmd_use "$@" ;;
  list)   cmd_list ;;
  whoami) cmd_whoami ;;
  delete) cmd_delete "$@" ;;
  help|--help|-h) cmd_help ;;
  *)      die "Unknown command: ${CMD}. Run: claude-switch help" ;;
esac
