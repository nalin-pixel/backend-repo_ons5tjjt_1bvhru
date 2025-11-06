from __future__ import annotations
import os
from motor.motor_asyncio import AsyncIOMotorClient

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME")

client = None
_db = None

if DATABASE_URL and DATABASE_NAME:
    try:
        client = AsyncIOMotorClient(DATABASE_URL)
        _db = client[DATABASE_NAME]
    except Exception:  # pragma: no cover
        client = None
        _db = None

# Expose db for imports
if _db is not None:
    db = _db
else:
    db = None

__all__ = ["db"]
