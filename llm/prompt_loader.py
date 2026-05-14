from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Shared Jinja2 engine. Constructing Environment is expensive; one instance suffices.
_JINJA_ENV = Environment(undefined=StrictUndefined)


class Prompt(BaseModel):
    name: str
    system: str
    user: str
    variables: list[str]

    def render(self, **vars: object) -> tuple[str, str]:
        """Render system and user templates with Jinja2 StrictUndefined.

        Args:
            **vars: Template variables matching the prompt's ``variables`` list.

        Returns:
            Tuple of (rendered_system, rendered_user).

        Raises:
            jinja2.UndefinedError: If a required template variable is missing.
        """
        return (
            _JINJA_ENV.from_string(self.system).render(**vars),
            _JINJA_ENV.from_string(self.user).render(**vars),
        )


def load_prompts(directory: Path) -> dict[str, Prompt]:
    """Load all *.yaml prompt files from directory into a name-keyed dict.

    Args:
        directory: Path to scan for ``*.yaml`` files.

    Returns:
        Dict keyed by ``prompt.name``. Empty dict if directory has no yaml files.
    """
    prompts: dict[str, Prompt] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        prompt = Prompt.model_validate(data)
        prompts[prompt.name] = prompt
    return prompts


PROMPTS: dict[str, Prompt] = load_prompts(_PROMPTS_DIR)