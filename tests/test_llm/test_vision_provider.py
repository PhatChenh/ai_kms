"""
tests/test_llm/test_vision_provider.py

Tests for Phase 3: Vision config + provider extension + prompt.
Follows the project test pattern — imports models directly, never imports CONFIG at module scope.
"""

from unittest.mock import MagicMock

import pytest

from core.config import OpenAICompatConfig, ProvidersConfig
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, get_provider


# ---------------------------------------------------------------------------
# Tracer bullet: vision provider routing + describe_image default
# ---------------------------------------------------------------------------

class TestVisionProviderRouting:
    """Tracer bullet: get_provider('vision', ...) resolves and describe_image
    on a non-vision provider returns Failure."""

    def test_vision_task_routes_to_openai_provider(self):
        """get_provider('vision', config) returns an OpenAIProvider when
        ProvidersConfig.vision == 'openai'."""
        # Build a config that routes vision to openai
        providers = ProvidersConfig(vision="openai")
        openai_cfg = OpenAICompatConfig(api_key_env="FIREWORKS_API_KEY")

        mock_main = MagicMock()
        mock_main.providers = providers
        mock_main.openai_compat = openai_cfg

        # This should not raise — vision task must be in the factory
        from llm.openai_provider import OpenAIProvider
        import os

        # Set a dummy env var so OpenAIProvider.__init__ doesn't fail
        os.environ["FIREWORKS_API_KEY"] = "test-key"
        try:
            provider = get_provider("vision", mock_main)
            assert isinstance(provider, OpenAIProvider)
        finally:
            del os.environ["FIREWORKS_API_KEY"]

    def test_describe_image_default_returns_failure(self):
        """Calling describe_image on a provider that doesn't override it
        returns Failure('vision not supported')."""

        # Create a concrete subclass of LLMProvider that only implements complete()
        class NoVisionProvider(LLMProvider):
            async def complete(self, system: str, user: str) -> Result[LLMResponse]:
                return Success(LLMResponse(content="ok", model="test", usage={}))

        provider = NoVisionProvider()
        # describe_image should NOT be abstract — it should have a default body
        assert hasattr(provider, "describe_image")
        assert callable(provider.describe_image)


# ---------------------------------------------------------------------------
# Leaf A: VisionConfig
# ---------------------------------------------------------------------------

class TestVisionConfig:
    """VisionConfig model defaults and CaptureConfig integration."""

    def test_default_describable_mime_prefixes(self):
        """VisionConfig defaults describable_mime_prefixes to ['image/']."""
        from core.config import VisionConfig
        vc = VisionConfig()
        assert vc.describable_mime_prefixes == ["image/"]

    def test_default_max_vision_bytes(self):
        """VisionConfig defaults max_vision_bytes to 10 MB (10485760)."""
        from core.config import VisionConfig
        vc = VisionConfig()
        assert vc.max_vision_bytes == 10 * 1024 * 1024  # 10485760

    def test_capture_config_has_vision_field(self):
        """CaptureConfig has a vision: VisionConfig field."""
        from core.config import CaptureConfig, VisionConfig
        cc = CaptureConfig()
        assert hasattr(cc, "vision")
        assert isinstance(cc.vision, VisionConfig)

    def test_openai_compat_config_has_vision_model_field(self):
        """OpenAICompatConfig has a vision_model: str field, default empty."""
        cfg = OpenAICompatConfig()
        assert hasattr(cfg, "vision_model")
        assert cfg.vision_model == ""


# ---------------------------------------------------------------------------
# Leaf B: OpenAIProvider describe_image override
# ---------------------------------------------------------------------------

class TestOpenAIProviderDescribeImage:
    """OpenAIProvider.describe_image builds image_url content blocks."""

    @pytest.mark.asyncio
    async def test_describe_image_returns_success_with_mocked_api(self):
        """describe_image base64-encodes image, sends image_url block,
        and returns Success(LLMResponse) on valid response."""
        import base64
        import os
        from unittest.mock import AsyncMock
        from core.config import OpenAICompatConfig
        from llm.openai_provider import OpenAIProvider

        os.environ["FIREWORKS_API_KEY"] = "test-key"
        try:
            cfg = OpenAICompatConfig(api_key_env="FIREWORKS_API_KEY", vision_model="gpt-4-vision")
            provider = OpenAIProvider(cfg, task="capture")

            # Mock the underlying OpenAI client
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "A beautiful sunset over a mountain range."
            mock_resp.model = "gpt-4-vision"
            mock_resp.usage = MagicMock()
            mock_resp.usage.model_dump.return_value = {"prompt_tokens": 100, "completion_tokens": 20}

            provider._client = MagicMock()
            provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

            fake_image = b"\x89PNG\r\n\x1a\n"  # minimal valid bytes
            result = await provider.describe_image(
                system="Describe images.",
                user="What is in this image?",
                image_bytes=fake_image,
                mime_type="image/png",
            )

            assert isinstance(result, Success)
            assert result.value.content == "A beautiful sunset over a mountain range."
            assert result.value.model == "gpt-4-vision"

            # Verify the image was base64-encoded and sent correctly
            call_args = provider._client.chat.completions.create.call_args
            assert call_args is not None
            kwargs = call_args.kwargs
            assert kwargs["model"] == "gpt-4-vision"  # must use _vision_model, not _model
            messages = kwargs["messages"]
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == "Describe images."
            # user message should be a list with image_url + text blocks
            user_content = messages[1]["content"]
            assert isinstance(user_content, list)
            assert user_content[0]["type"] == "image_url"
            expected_b64 = base64.b64encode(fake_image).decode("ascii")
            assert user_content[0]["image_url"]["url"] == f"data:image/png;base64,{expected_b64}"
            assert user_content[1]["type"] == "text"
            assert user_content[1]["text"] == "What is in this image?"
        finally:
            del os.environ["FIREWORKS_API_KEY"]

    @pytest.mark.asyncio
    async def test_describe_image_returns_failure_when_vision_model_empty(self):
        """describe_image returns Failure when vision_model is empty string."""
        import os
        from core.config import OpenAICompatConfig
        from llm.openai_provider import OpenAIProvider

        os.environ["FIREWORKS_API_KEY"] = "test-key"
        try:
            cfg = OpenAICompatConfig(api_key_env="FIREWORKS_API_KEY", vision_model="")
            provider = OpenAIProvider(cfg, task="vision")

            result = await provider.describe_image(
                system="Describe.",
                user="What is this?",
                image_bytes=b"fake",
                mime_type="image/png",
            )
            assert isinstance(result, Failure)
            assert "no vision_model configured" in result.error.lower()
        finally:
            del os.environ["FIREWORKS_API_KEY"]

    @pytest.mark.asyncio
    async def test_describe_image_handles_api_error(self):
        """describe_image returns Failure on APIError."""
        import os
        from unittest.mock import AsyncMock, MagicMock
        from core.config import OpenAICompatConfig
        from llm.openai_provider import OpenAIProvider

        os.environ["FIREWORKS_API_KEY"] = "test-key"
        try:
            cfg = OpenAICompatConfig(api_key_env="FIREWORKS_API_KEY", vision_model="gpt-4-vision")
            provider = OpenAIProvider(cfg, task="vision")

            # Use a real Exception subclass — MagicMock(spec=Exception) won't work
            # because asyncio/mock machinery checks issubclass
            class FakeAPIError(Exception):
                pass

            provider._client = MagicMock()
            provider._client.chat.completions.create = AsyncMock(
                side_effect=FakeAPIError("service unavailable")
            )

            result = await provider.describe_image(
                system="x", user="y", image_bytes=b"fake", mime_type="image/png"
            )
            assert isinstance(result, Failure)
            assert "service unavailable" in result.error
        finally:
            del os.environ["FIREWORKS_API_KEY"]

    @pytest.mark.asyncio
    async def test_describe_image_with_real_provider_returns_failure(self):
        """Calling describe_image on a non-overriding provider returns Failure."""
        class NoVisionProvider(LLMProvider):
            async def complete(self, system: str, user: str) -> Result[LLMResponse]:
                return Success(LLMResponse(content="ok", model="test", usage={}))

        provider = NoVisionProvider()
        result = await provider.describe_image(
            system="x", user="y", image_bytes=b"fake", mime_type="image/png"
        )
        assert isinstance(result, Failure)
        assert "vision not supported" in result.error


# ---------------------------------------------------------------------------
# Leaf C: Vision prompt
# ---------------------------------------------------------------------------

class TestVisionPrompt:
    """describe_image.yaml prompt loads and renders correctly."""

    def test_prompt_exists(self):
        """PROMPTS dict contains 'describe_image' key."""
        from llm.prompt_loader import PROMPTS
        assert "describe_image" in PROMPTS

    def test_prompt_renders(self):
        """describe_image prompt renders with mime_type variable."""
        from llm.prompt_loader import PROMPTS
        prompt = PROMPTS["describe_image"]
        system, user = prompt.render(mime_type="image/png")
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 0
        assert len(user) > 0

    def test_prompt_system_contains_visual_instructions(self):
        """System prompt contains instructions about describing visual content."""
        from llm.prompt_loader import PROMPTS
        prompt = PROMPTS["describe_image"]
        system, _ = prompt.render(mime_type="image/png")
        sys_lower = system.lower()
        assert "visual" in sys_lower or "describe" in sys_lower
        assert "image" in sys_lower or "chart" in sys_lower or "text" in sys_lower
