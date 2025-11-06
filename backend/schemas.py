from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, List, Literal, Optional


class AnalyzerCache(BaseModel):
    ingredients_hash: str
    data: dict
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None


class AnalyzeRequest(BaseModel):
    ingredients: List[str]


class AnalyzeResponse(BaseModel):
    cache: Literal["hit", "miss"]
    ingredients_hash: str
    data: dict
