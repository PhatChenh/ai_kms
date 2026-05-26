"""
llm/claude_cli_provider.py

Claude CLI subprocess implementation of LLMProvider.

Calls the `claude` binary as a subprocess — no API key required. The binary
must be installed and authenticated on the machine running this code.

Call flow:
    complete(system, user)
        → asyncio.create_subprocess_exec("claude", "-p", ...)
        → write user content to stdin
        → parse JSON stdout
        → return Success(LLMResponse) or Failure(...)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil

from core.config import ClaudeCliConfig, Task
from core.exceptions import ConfigError
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, SYNTHESIS_TASKS

# Matches markdown code fences: ```[lang]\n...\n```
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)\n```\s*$", re.DOTALL)


class ClaudeCliProvider(LLMProvider):
    """Calls the Claude CLI binary as an async subprocess.

    No ANTHROPIC_API_KEY required — authentication is delegated to the
    `claude` binary (which uses its own credentials from `claude auth login`).
    """

    def __init__(self, config: ClaudeCliConfig, task: Task = "capture") -> None:
        """
        Initialise the provider.

        Args:
            config: The validated ClaudeCliConfig from MainConfig.
            task:   Pipeline task — selects model (haiku vs sonnet).

        Raises:
            ConfigError: if the `claude` binary is not found on PATH.
        """
        resolved = shutil.which(config.cli_path)
        if resolved is None:
            raise ConfigError(
                f"Claude CLI binary not found: '{config.cli_path}'. "
                "Install it with: npm install -g @anthropic-ai/claude-code"
            )
        self._cli_path: str = resolved
        self._model: str = (
            config.synthesis_model if task in SYNTHESIS_TASKS else config.model
        )
        self._timeout: int = config.timeout

    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair via the Claude CLI subprocess.

        Args:
            system: Behavioural instructions for the model.
            user:   Content to process (sent via stdin).

        Returns:
            Success(LLMResponse) on a clean response.
            Failure(recoverable=True) on timeout, bad JSON, or non-zero exit.
            Failure(recoverable=False) on OSError (binary vanished after init).
        """
        env = {**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"}

        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, "-p",
                "--system-prompt", system,
                "--model", self._model,
                "--output-format", "json",
                "--max-turns", "1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            # Binary disappeared between __init__ check and this call.
            return Failure(
                error=f"Failed to spawn claude binary: {exc}",
                recoverable=False,
                context={"provider": "claude_cli"},
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=user.encode()),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return Failure(
                error=f"Claude CLI timed out after {self._timeout}s",
                recoverable=True,
                context={"provider": "claude_cli"},
            )

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return Failure(
                error="Claude CLI returned non-JSON output",
                recoverable=True,
                context={"provider": "claude_cli", "stderr": stderr[:200]},
            )

        if data.get("is_error") or proc.returncode != 0:
            return Failure(
                error=data.get("result", "Claude CLI reported an error"),
                recoverable=True,
                context={"provider": "claude_cli", "stderr": stderr[:200]},
            )

        raw_content: str = data.get("result", "")
        content = _strip_fence(raw_content)

        usage: dict = {
            "cost_usd": data.get("cost_usd", 0.0),
            "duration_ms": data.get("duration_ms", 0),
        }

        return Success(
            LLMResponse(content=content, model=self._model, usage=usage)
        )


def _strip_fence(text: str) -> str:
    """Remove a single outer markdown code fence if present, else return text unchanged."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text
