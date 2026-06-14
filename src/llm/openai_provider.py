"""
llm/openai_provider.py

Async LLMProvider for any OpenAI-compatible endpoint (Fireworks, Together, etc.).
API key is read from the env var named by config.api_key_env at __init__ time.
"""

import base64
import os

import openai

from core.config import OpenAICompatConfig, Task
from core.exceptions import ConfigError
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, SYNTHESIS_TASKS


class OpenAIProvider(LLMProvider):
    """LLMProvider backed by any OpenAI-compatible REST endpoint."""

    def __init__(self, config: OpenAICompatConfig, task: Task = "capture") -> None:
        """
        Initialise the provider.

        Args:
            config: OpenAICompatConfig from core.config.
            task:   Pipeline task — used to select model (default vs synthesis vs vision).

        Raises:
            ConfigError: if the env var named by config.api_key_env is unset or empty.
        """
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(
                f"{config.api_key_env} not set. Add it to .env or export it."
            )
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        if task == "vision":
            self._model = config.vision_model
        elif task in SYNTHESIS_TASKS:
            self._model = config.synthesis_model
        else:
            self._model = config.model
        self._max_tokens = config.max_tokens
        self._embedding_model = config.embedding_model
        self._vision_model = config.vision_model

    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair to the configured model.

        Args:
            system: Behavioural instructions.
            user:   Content to process.

        Returns:
            Success(LLMResponse) on a valid response.
            Failure(recoverable=True) on any APIError.
        """
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            usage = resp.usage.model_dump() if resp.usage else {}
            return Success(
                LLMResponse(
                    content=resp.choices[0].message.content or "",
                    model=resp.model,
                    usage=usage,
                )
            )
        except openai.APIError as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )
        except Exception as exc:
            # httpx / asyncio errors not wrapped by the openai SDK
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )

    async def describe_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> Result[LLMResponse]:
        """Describe an image using a vision-capable model.

        Base64-encodes the image bytes and sends an image_url content block
        alongside the user text. Requires vision_model to be configured.

        Args:
            system:      Behavioural instructions.
            user:        User prompt text describing what to look for.
            image_bytes: Raw image bytes (PNG, JPEG, etc.).
            mime_type:   MIME type string, e.g. "image/png".  Must match
                         ``^[a-z]+/[a-z0-9.+-]+$`` (lowercase type/subtype).

        Returns:
            Success(LLMResponse) on a valid description,
            Failure if vision_model is empty, mime_type is invalid,
            or on any API/network error.
        """
        import re

        if not self._vision_model:
            return Failure(
                "no vision_model configured for OpenAI compat provider",
                recoverable=False,
                context={},
            )
        if not mime_type or not re.match(r"^[a-z]+/[a-z0-9.+-]+$", mime_type):
            return Failure(
                error="invalid mime_type",
                recoverable=False,
                context={"mime_type": mime_type},
            )
        b64 = base64.b64encode(image_bytes).decode("ascii")
        image_block = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
        }
        text_block = {"type": "text", "text": user}
        try:
            resp = await self._client.chat.completions.create(
                model=self._vision_model,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": [image_block, text_block]},
                ],
            )
            usage = resp.usage.model_dump() if resp.usage else {}
            return Success(
                LLMResponse(
                    content=resp.choices[0].message.content or "",
                    model=resp.model,
                    usage=usage,
                )
            )
        except openai.APIError as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )
        except Exception as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )
