import os
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

from database import db, create_document

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ingredients: List[str] = Field(..., description="List of ingredient lines, e.g. '1 cup rice'")


class AnalyzeResponse(BaseModel):
    cache: str
    ingredients_hash: str
    data: Dict[str, Any]


class ApiValidateRequest(BaseModel):
    baseUrl: HttpUrl = Field(..., description="Base URL of the API to validate")
    path: Optional[str] = Field(None, description="Optional path to append when validating, e.g. '/health'")
    method: Optional[str] = Field("GET", description="HTTP method to use for validation")


class ApiValidateResponse(BaseModel):
    ok: bool
    status: Optional[int]
    time_ms: Optional[int]
    final_url: Optional[str]
    error: Optional[str] = None


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        # Verify database connection
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


@app.post("/api/validate", response_model=ApiValidateResponse)
def validate_api(req: ApiValidateRequest):
    """
    Validate that a given API base URL is reachable. Optionally append a path and use a custom method.
    """
    url = str(req.baseUrl).rstrip("/")
    if req.path:
        p = req.path if req.path.startswith("/") else f"/{req.path}"
        url = f"{url}{p}"

    method = (req.method or "GET").upper()

    try:
        start = datetime.now()
        r = requests.request(method, url, timeout=5)
        delta = datetime.now() - start
        return ApiValidateResponse(
            ok=r.status_code < 400,
            status=r.status_code,
            time_ms=int(delta.total_seconds() * 1000),
            final_url=str(r.url),
            error=None if r.status_code < 400 else r.text[:300]
        )
    except requests.RequestException as e:
        return ApiValidateResponse(ok=False, status=None, time_ms=None, final_url=url, error=str(e))


@app.post("/api/nutrition/analyze", response_model=AnalyzeResponse)
def analyze_nutrition(payload: AnalyzeRequest):
    """
    Analyze a list of ingredients using Edamam Nutrition Analysis API.
    Caches results in MongoDB using a deterministic hash of the ingredients list.
    """
    app_id = os.getenv("EDAMAM_APP_ID")
    app_key = os.getenv("EDAMAM_APP_KEY")
    if not app_id or not app_key:
        raise HTTPException(status_code=500, detail="Edamam credentials are not configured.")

    ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

    # Normalize and hash ingredients for cache key
    normalized = [i.strip().lower() for i in payload.ingredients if i and i.strip()]
    if not normalized:
        raise HTTPException(status_code=400, detail="Please provide at least one ingredient.")

    joined = "\n".join(normalized)
    ingredients_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()

    # Try cache lookup
    now = datetime.now(timezone.utc)
    ttl_cutoff = now - timedelta(seconds=ttl_seconds)
    cached_doc: Optional[dict] = None
    if db is not None:
        cached_doc = db["analyzercache"].find_one({
            "ingredients_hash": ingredients_hash,
            "created_at": {"$gte": ttl_cutoff}
        })

    if cached_doc:
        return AnalyzeResponse(cache="hit", ingredients_hash=ingredients_hash, data=cached_doc.get("result", {}))

    # Call Edamam API
    url = "https://api.edamam.com/api/nutrition-details"
    params = {"app_id": app_id, "app_key": app_key}
    body = {"ingr": normalized}

    try:
        r = requests.post(url, params=params, json=body, timeout=20)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error contacting nutrition service: {str(e)}")

    if r.status_code >= 400:
        # Try to forward error from Edamam
        try:
            err = r.json()
        except Exception:
            err = {"message": r.text}
        raise HTTPException(status_code=r.status_code, detail=err)

    data = r.json()

    # Save to cache
    if db is not None:
        doc = {
            "ingredients": normalized,
            "ingredients_hash": ingredients_hash,
            "result": data,
            "created_at": now,
            "updated_at": now,
        }
        db["analyzercache"].insert_one(doc)
    else:
        # If DB not configured, still return live result
        pass

    return AnalyzeResponse(cache="miss", ingredients_hash=ingredients_hash, data=data)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
