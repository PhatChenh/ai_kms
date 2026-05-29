# VaultConfig: attachment_path property removed; summaries_subdir Field added

`VaultConfig.attachment_path` @property deleted. `attachment_dir: str = "attachment"` kept (used by path helpers). New `summaries_subdir: str = ".summaries"` Field added.

**Status:** accepted (Phase 1.5 OQ-AL2 — Option VC-A)

**Considered Options**

- (VC-B) Repurpose as low-confidence staging area — depends on unresolved OQ-AC3; deferred.
- (VC-C) Deprecated alias — creates confusion about which global folder still exists.

**Consequences**

- `CONFIG.main.vault.attachment_path` no longer exists. Raises `AttributeError` at runtime.
- All callers must use `project_attachment(name)` / `domain_attachment(name)` from `vault/paths.py`.
- Global `Vault/attachment/` no longer exists; keeping the property would imply a folder that is gone.
