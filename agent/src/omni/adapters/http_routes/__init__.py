"""Feature routers for the authenticated V2 API."""

from pydantic import BaseModel, ConfigDict


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
