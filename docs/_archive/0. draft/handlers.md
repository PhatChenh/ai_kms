# Discussion Log: Handlers

## Phase 0 — Planning & Core Concepts
_Completed: 2026-05-20_

### Exchanges

**Q:** What does "dispatch mechanism" mean in the context of HandlerRegistry?

**A:** Dispatch = routing a request to the right handler based on matching criteria. In the context of HandlerRegistry, it's the system that checks a file's extension and returns the correct handler instance. Without hardcoding a big if/elif chain, the registry iterates through registered handlers, calls `can_handle()` on each, and returns the first match. This is extensible — add a new handler, it auto-registers, and the dispatch automatically knows about it without modifying central code.

```python
# Example dispatch
def resolve(self, path: Path) -> Result[BaseHandler]:
    for handler in self._handlers:
        if handler.can_handle(path):
            return Success(handler)
    return Failure(...)
```

_Key concept: Runtime routing via iteration and matching_

---

**Q:** What does "register" mean in the handler registration pattern?

**A:** Register = declaration at startup time. Each handler says "I exist and I'm ready" by running the `@register` decorator when imported. This adds the handler instance to `HandlerRegistry._handlers` list. Happens once at import time. Later, at runtime, when a file drops, `resolve()` dispatches to the correct handler by iterating that pre-populated list. The two are separate — registration populates the list, dispatch queries it.

```python
# Registration happens once, at import time
@HandlerRegistry.register
class MarkdownHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".md"
```

_Key concept: Import-time self-registration into a central list_

---

**Q:** Why use a registry pattern instead of directly calling handlers based on file extension?

**A:** The registry decouples extension-checking from handler selection. Direct call would be:
```python
if path.suffix == ".md": handler = MarkdownHandler()
elif path.suffix == ".pdf": handler = PdfHandler()
# Adding YouTube handler? Modify this file.
```

Registry keeps handler logic in each handler's own file:
```python
# New handler, new file, add @register, done. No central dispatch to touch.
for handler in self._handlers:
    if handler.can_handle(path):
        return handler
```

Extensibility: add YouTube handler later without touching existing code.

_Key concept: Decouple handler matching from handler existence_

---

**Q:** What does "bootstrap" mean in programming?

**A:** Bootstrap = starting a system by pulling itself up by its own bootstraps. Self-starting. In the handlers system, it means `handlers/__init__.py` imports all three handlers at startup:
```python
from handlers.markdown_handler import MarkdownHandler
from handlers.pdf_handler import PdfHandler
from handlers.docx_handler import DocxHandler
```

Each import triggers `@register`, filling `HandlerRegistry._handlers` list. System is now bootstrapped — registry is pre-loaded and ready to dispatch file drops. Without bootstrap, registry is empty.

_Key concept: Sequential startup initialization filling pre-requisites_

---

**Q:** What does `__all__` do in a Python module?

**A:** `__all__` = explicit list of what's public when someone imports from this module.

```python
# handlers/base.py
__all__ = ["RawContent", "BaseHandler"]
```

This tells Python: "Only these are the public API. Everything else is internal." Effect:
```python
from handlers.base import *  # imports ONLY RawContent and BaseHandler
```

Without `__all__`, wildcard imports would get everything. Why? Clarity — readers see exactly what the module exports. Stability — can add private helpers later without breaking wildcard imports elsewhere. Documentation — IDEs show what's available.

_Key concept: Public API declaration via list_

---

**Q:** What does `type[BaseHandler]` mean in a type annotation?

**A:** `type[BaseHandler]` = "the class itself, not an instance of it."

```python
# Instance — you can call methods on it
handler = MarkdownHandler()
handler.can_handle(path)

# Class — you can instantiate it, but not call methods directly
handler_class = MarkdownHandler
instance = handler_class()  # ✓ works
handler_class.can_handle(path)  # ✗ fails — needs self
```

In a function signature:
```python
def register(cls, handler_class: type[BaseHandler]) -> type[BaseHandler]:
    instance = handler_class()  # instantiate the class
    _handlers.append(instance)
    return handler_class  # return the class (for decorator chaining)
```

`type[BaseHandler]` means "I accept any class that is BaseHandler or a subclass of it" — not an instance, the class itself. Used as decorator:
```python
@HandlerRegistry.register  # passes MarkdownHandler (the class)
class MarkdownHandler(BaseHandler):
    ...
```

_Key concept: Class as value vs. instance as value_

---

**Q:** Why is `resolve` a `@classmethod` when it doesn't seem to instantiate anything?

**A:** Correct observation. `resolve` doesn't instantiate anything — both `register` and `resolve` are `@classmethod` because HandlerRegistry is **never instantiated**. The system is designed as a class-level singleton: no instances, only the class itself with mutable state (`_handlers` list). This pattern keeps dispatch and registration shared across the whole application. Treating HandlerRegistry as a class-level utility (all methods are classmethods) means any module that imports it sees the same populated `_handlers` list.

Alternative (not used): make them instance methods, but then you'd need to pass a HandlerRegistry instance around everywhere, which adds coupling.

_Key concept: Class-level utility pattern — no instances needed_

---

## Phase 6 — handlers/__init__.py: bootstrap + integration
_Completed: 2026-05-20_

### Exchanges

**Q:** The integration test checks `len(HandlerRegistry._handlers) == 3`. Is that brittle — could it break if a unit test didn't clean up properly?

**A:** The check is safe under the `clean_registry` discipline: every unit test that registers a dummy handler must use `clean_registry`, which saves `_handlers[:]` before the test and restores it after. As long as that rule holds, dummy handlers never leak into the count. The integration test also does `import handlers` at module level, which ensures the three real handlers are registered before the check runs.

The risk would be a test that calls `HandlerRegistry.register` WITHOUT `clean_registry`. That would silently inflate the count. It's a discipline violation rather than an architectural flaw — enforced by the requirement documented in the plan.

_Key concept: Test isolation via fixture save/restore_

---

**Q:** Won't `len == 3` break when a 4th handler is added later?

**A:** Yes — `3` is a magic number that needs a manual update every time a new handler is registered. The better approach is type membership checks:

```python
handler_types = {type(h) for h in HandlerRegistry._handlers}
assert MarkdownHandler in handler_types
assert PdfHandler in handler_types
assert DocxHandler in handler_types
```

Adding a 4th handler doesn't break these assertions. You just add a new `assert NewHandler in handler_types` when you add the handler. The old assertions still pass untouched.

_Key concept: Extensible assertions vs. fixed-count assertions_

---
