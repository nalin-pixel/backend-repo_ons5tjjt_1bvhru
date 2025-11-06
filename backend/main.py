from __future__ import annotations
import hashlib
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import AnalyzeRequest, AnalyzeResponse

# Optional MongoDB
try:
    from database import db
except Exception:  # pragma: no cover
    db = None  # type: ignore

PORT = int(os.getenv("PORT", "8000"))
EDAMAM_APP_ID = os.getenv("EDAMAM_APP_ID")
EDAMAM_APP_KEY = os.getenv("EDAMAM_APP_KEY")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

app = FastAPI(title="Nutrition Analyzer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def norm_ingredients(ingredients: list[str]) -> list[str]:
    return [" ".join(s.strip().lower().split()) for s in ingredients if s and s.strip()]


def make_hash(ingredients: list[str]) -> str:
    joined = "\n".join(norm_ingredients(ingredients))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def fetch_from_cache(ingredients_hash: str) -> Optional[Dict[str, Any]]:
    try:
        if db is None:
            return None
        coll = db["analyzercache"]
        doc = await coll.find_one({"ingredients_hash": ingredients_hash})
        if not doc:
            return None
        # TTL check
        expires_at = doc.get("expires_at")
        if expires_at and isinstance(expires_at, datetime) and expires_at < datetime.utcnow():
            return None
        return doc.get("data")
    except Exception:
        return None


async def write_cache(ingredients_hash: str, data: Dict[str, Any]) -> None:
    try:
        if db is None:
            return
        coll = db["analyzercache"]
        now = datetime.utcnow()
        await coll.update_one(
            {"ingredients_hash": ingredients_hash},
            {
                "$set": {
                    "data": data,
                    "updated_at": now,
                    "expires_at": now + timedelta(seconds=CACHE_TTL_SECONDS),
                },
                "$setOnInsert": {"created_at": now, "ingredients_hash": ingredients_hash},
            },
            upsert=True,
        )
    except Exception:
        return


async def call_edamam(ingredients: list[str]) -> Dict[str, Any]:
    if not EDAMAM_APP_ID or not EDAMAM_APP_KEY:
        raise HTTPException(status_code=500, detail="Edamam credentials not configured")

    url = "https://api.edamam.com/api/nutrition-details"
    params = {"app_id": EDAMAM_APP_ID, "app_key": EDAMAM_APP_KEY}
    payload = {"ingr": norm_ingredients(ingredients)}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, params=params, json=payload)
        if r.status_code >= 400:
            # Try to surface Edamam message
            try:
                err = r.json()
                message = err.get("message") or err.get("error") or r.text
            except Exception:
                message = r.text
            raise HTTPException(status_code=r.status_code, detail=f"Edamam error: {message}")
        return r.json()


@app.get("/test")
async def test():
    return {"ok": True, "db": bool(db)}


@app.post("/api/nutrition/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    if not req.ingredients or len(norm_ingredients(req.ingredients)) == 0:
        raise HTTPException(status_code=400, detail="Provide at least one ingredient line")

    ingredients_hash = make_hash(req.ingredients)

    # Try cache first
    cached = await fetch_from_cache(ingredients_hash)
    if cached is not None:
        return AnalyzeResponse(cache="hit", ingredients_hash=ingredients_hash, data=cached)

    # Fallback to live Edamam
    data = await call_edamam(req.ingredients)

    # Write to cache (best effort)
    await write_cache(ingredients_hash, data)

    return AnalyzeResponse(cache="miss", ingredients_hash=ingredients_hash, data=data)


# Run with: uvicorn main:app --host 0.0.0.0 --port PORT
