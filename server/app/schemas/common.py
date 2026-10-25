from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class APIModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True, use_enum_values=True
    )


class Meta(APIModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    page: int | None = None
    limit: int | None = None
    total: int | None = None


class Envelope[T](APIModel):
    success: bool = True
    data: T
    meta: Meta = Field(default_factory=Meta)
