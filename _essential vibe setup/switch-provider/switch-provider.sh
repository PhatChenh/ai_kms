#!/bin/bash

# ============================================================
# Claude Code Provider Switcher
# Place this file at: ~/.claude/switch-provider.sh
# ============================================================

SETTINGS="$HOME/.claude/settings.json"

# --- Ensure settings.json exists ---
if [ ! -f "$SETTINGS" ]; then
  echo "{}" > "$SETTINGS"
fi

use_deepseek() {
  python3 -c "
import json
with open('$SETTINGS') as f:
    s = json.load(f)
s.setdefault('env', {}).update({
    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
    'ANTHROPIC_AUTH_TOKEN': 'PASTE_YOUR_DEEPSEEK_KEY_HERE',
    'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro[1m]',
    'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-pro[1m]',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-pro[1m]',
    'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-v4-pro[1m]'
})
with open('$SETTINGS', 'w') as f:
    json.dump(s, f, indent=2)
print('→ Switched to DeepSeek')
"
}

use_claude() {
  python3 -c "
import json
with open('$SETTINGS') as f:
    s = json.load(f)
for key in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL']:
    s.get('env', {}).pop(key, None)
with open('$SETTINGS', 'w') as f:
    json.dump(s, f, indent=2)
print('→ Switched to Claude Pro')
"
}

status() {
  python3 -c "
import json
with open('$SETTINGS') as f:
    s = json.load(f)
url = s.get('env', {}).get('ANTHROPIC_BASE_URL', '')
if 'deepseek' in url:
    print('Current provider: DeepSeek')
else:
    print('Current provider: Claude Pro (subscription)')
"
}

case "$1" in
  deepseek) use_deepseek ;;
  claude)   use_claude ;;
  status)   status ;;
  *)
    echo "Usage: switch-provider deepseek | claude | status"
    echo ""
    echo "  deepseek  → route Claude Code to DeepSeek API"
    echo "  claude    → restore Claude Pro subscription"
    echo "  status    → show current active provider"
    ;;
esac
