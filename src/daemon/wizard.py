"""
daemon/wizard.py

Setup Wizard for the AI-kms daemon.

Provides:
- ``attempt_save()`` — the testable orchestration core
- ``run_wizard()`` — the Tkinter UI surface

Tkinter is stdlib (no new dep).  The orchestration is tested with Phase 1
(secret_vault.store_key) and Phase 2 (connection_check.check_connection)
stubbed; the Tk widgets themselves are not unit-tested.
"""

from __future__ import annotations

import asyncio
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import yaml

from core.result import Failure, Result, Success
from daemon.defaults import DEFAULT_ENDPOINT

# COUPLING: the config YAML shape is dictated by DaemonConfig/load_daemon_config
# (daemon/config.py).  Write the exact keys vault_root and cloud_endpoint.


def _get_config_path() -> Path:
    """Return the default daemon config path.

    Overridden in tests via monkeypatch to use a temporary directory.
    """
    return Path.home() / ".kms-daemon" / "config.yaml"


def attempt_save(
    folder: Path,
    endpoint: str,
    key: str,
    *,
    check: Callable[..., Result[None]],
    store: Callable[[str], Result[None]],
) -> Result[None]:
    """Testable orchestration core for the setup wizard.

    1. Calls ``check(endpoint, key)`` — async functions are run via
       ``asyncio.run()``.  If it returns Failure, NOTHING is written.
    2. On Success: writes the daemon config YAML to
       ``~/.kms-daemon/config.yaml``.
    3. Calls ``store(key)`` to save the key in the OS vault.
    4. Returns ``Success(None)`` or ``Failure``.

    Args:
        folder:  The Obsidian vault / notes folder path.
        endpoint:  Cloud server base URL (user-editable).
        key:  API key (masked in the UI).
        check:  Connection-check callable (may be async).
        store:  Key-storage callable (sync).
    """
    # Step 1 — connection check
    if asyncio.iscoroutinefunction(check):
        check_result = asyncio.run(check(endpoint, key))
    else:
        check_result = check(endpoint, key)

    if check_result.is_failure():
        return check_result  # NOTHING written

    # Step 2 — write config YAML in the shape load_daemon_config reads
    config_path = _get_config_path()
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    config_data: dict[str, str] = {
        "vault_root": str(folder),
        "cloud_endpoint": endpoint,
    }

    try:
        config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")
    except OSError as exc:
        return Failure(
            error=f"Failed to write config file: {exc}",
            recoverable=False,
            context={"config_path": str(config_path)},
        )

    # Step 3 — store key in OS vault
    store_result = store(key)
    if store_result.is_failure():
        return store_result

    return Success(None)


# ── Tkinter UI surface (manual-verify only) ─────────────────────────────


def run_wizard() -> None:
    """Launch the Tkinter setup wizard window.

    Collects:
    - Notes folder (with Browse... button)
    - Cloud address (pre-filled with ``DEFAULT_ENDPOINT``, editable)
    - API key (masked)

    On "Test & Save":
    - Validates all three fields are non-empty
    - Validates the folder exists on disk
    - Runs ``attempt_save()`` with the REAL Phase 1 + Phase 2 functions
    - On success: closes the window
    - On failure: shows the error, window stays open
    """
    # Late imports so the Tkinter dependency is only paid when the
    # wizard actually launches (imports are cheap but explicit).
    from daemon.connection_check import check_connection  # noqa: PLC0415
    from daemon.secret_vault import store_key  # noqa: PLC0415

    root = tk.Tk()
    root.title("AI-kms Setup")
    root.resizable(False, False)

    # ── Form fields ──────────────────────────────────────────────────

    # Row 0 — Notes folder
    tk.Label(root, text="Notes folder:").grid(
        row=0, column=0, sticky="w", padx=10, pady=(15, 2)
    )
    folder_var = tk.StringVar()
    folder_entry = tk.Entry(root, textvariable=folder_var, width=50)
    folder_entry.grid(row=0, column=1, padx=5, pady=(15, 2))

    def _browse_folder() -> None:
        chosen = filedialog.askdirectory(
            title="Select your notes (Obsidian vault) folder"
        )
        if chosen:
            folder_var.set(chosen)

    tk.Button(root, text="Browse...", command=_browse_folder).grid(
        row=0, column=2, padx=5, pady=(15, 2)
    )

    # Row 1 — Cloud address (pre-filled, editable)
    tk.Label(root, text="Cloud address:").grid(
        row=1, column=0, sticky="w", padx=10, pady=5
    )
    endpoint_var = tk.StringVar(value=DEFAULT_ENDPOINT)
    endpoint_entry = tk.Entry(root, textvariable=endpoint_var, width=50)
    endpoint_entry.grid(row=1, column=1, padx=5, pady=5)

    # Row 2 — API key (masked)
    tk.Label(root, text="API key:").grid(
        row=2, column=0, sticky="w", padx=10, pady=5
    )
    key_var = tk.StringVar()
    key_entry = tk.Entry(root, textvariable=key_var, show="*", width=50)
    key_entry.grid(row=2, column=1, padx=5, pady=5)

    # Row 3 — Status/error label
    status_var = tk.StringVar()
    status_label = tk.Label(
        root, textvariable=status_var, fg="red", wraplength=420
    )
    status_label.grid(row=3, column=0, columnspan=3, padx=10, pady=10)

    # Row 4 — Action button
    def _on_test_and_save() -> None:
        folder_str = folder_var.get().strip()
        endpoint_str = endpoint_var.get().strip()
        key_str = key_var.get().strip()

        # Basic validation
        if not folder_str:
            status_var.set("Please select a notes folder.")
            return
        if not endpoint_str:
            status_var.set("Please enter a cloud address.")
            return
        if not key_str:
            status_var.set("Please enter an API key.")
            return

        folder_path = Path(folder_str)
        if not folder_path.exists():
            status_var.set(f"Folder does not exist: {folder_str}")
            return
        if not folder_path.is_dir():
            status_var.set(f"Not a directory: {folder_str}")
            return

        status_var.set("Testing connection...")
        root.update_idletasks()

        result = attempt_save(
            folder=folder_path,
            endpoint=endpoint_str,
            key=key_str,
            check=check_connection,
            store=store_key,
        )

        if result.is_success():
            root.destroy()
        else:
            status_var.set(f"Error: {result.error}")

    tk.Button(
        root, text="Test & Save", command=_on_test_and_save, width=20
    ).grid(row=4, column=0, columnspan=3, pady=(5, 15))

    # ── Centre the window ────────────────────────────────────────────
    root.update_idletasks()
    root.geometry(
        "+%d+%d"
        % (
            root.winfo_screenwidth() // 2 - root.winfo_width() // 2,
            root.winfo_screenheight() // 2 - root.winfo_height() // 2,
        )
    )

    root.mainloop()
