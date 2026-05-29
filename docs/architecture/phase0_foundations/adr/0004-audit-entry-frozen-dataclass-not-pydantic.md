# AuditEntry is @dataclass(frozen=True), not Pydantic BaseModel

`AuditEntry` is an internal DTO between storage layers. DB schema + triggers provide the validation that matters. Pydantic is reserved for user-configurable values.

**Status:** accepted

**Consequences**

- The codebase rule: `Field` = human-configurable values; `@property` = code-computed values; `@dataclass` = internal DTOs.
- Do not use Pydantic for storage-layer data objects.
