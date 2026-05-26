**Step by step setup:**

**1. Move the script to global `~/.claude/`**

**2. Paste your DeepSeek API key into the script**

Open `~/.claude/switch-provider.sh`, find this line:
```
'ANTHROPIC_AUTH_TOKEN': 'PASTE_YOUR_DEEPSEEK_KEY_HERE',
```
Replace `PASTE_YOUR_DEEPSEEK_KEY_HERE` with your actual key.

**3. Make it executable**
```bash
chmod +x ~/.claude/switch-provider.sh
```

**4. Add aliases to `~/.zshrc`**
```bash
echo "alias use-deepseek='~/.claude/switch-provider.sh deepseek'" >> ~/.zshrc
echo "alias use-claude='~/.claude/switch-provider.sh claude'" >> ~/.zshrc
echo "alias provider-status='~/.claude/switch-provider.sh status'" >> ~/.zshrc
```

**5. Reload zsh**
```bash
source ~/.zshrc
```

**6. Test it**
```bash
provider-status   # should say Claude Pro
use-deepseek      # switch to DeepSeek
provider-status   # should say DeepSeek
use-claude        # switch back
```

**Daily usage:** Run the alias in any terminal, then open a new Claude Code session in VS Code. That's it.