from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class RawListing(BaseModel):
    title: str
    price: float
    currency: str = "USD"
    url: HttpUrl
    marketplace_id: str
    image_url: Optional[HttpUrl] = None
    raw_html_snippet: Optional[str] = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_stream_dict(self) -> dict[str, str]:
        """Serializa a dict de strings para Redis Streams (solo acepta str values)."""
        data = self.model_dump(mode="json")
        return {k: str(v) if v is not None else "" for k, v in data.items()}
