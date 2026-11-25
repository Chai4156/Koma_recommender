"""FastAPI HTTP service around the manga/manhwa recommender."""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from . import router
    from .cache import rate_limiter, state_store
    from .db import catalog_db
except ImportError:
    import router
    from cache import rate_limiter, state_store
    from db import catalog_db


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# ---------- Pydantic Request & Response Models ----------
class FiltersIn(BaseModel):
    type: Literal["all", "manga", "manhwa", "manhua"] = "all"
    include_suggestive: bool = False
    include_mature: bool = False
    readable_only: bool = False
    format: Literal["any", "oneshot", "short_series", "series"] = "any"


class RecommendRequest(BaseModel):
    text: str = Field(default="", max_length=2000)
    filters: FiltersIn = Field(default_factory=FiltersIn)
    seed_titles: Optional[list[str]] = Field(default=None, max_length=15)
    limit: int = Field(default=13, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=1000)

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def require_text_or_seeds(self) -> "RecommendRequest":
        has_seeds = bool([s for s in (self.seed_titles or []) if s and s.strip()])
        if not self.text and not has_seeds:
            raise ValueError("Provide either non-empty text or at least one seed title")
        return self


class RefilterRequest(BaseModel):
    intent_id: str = Field(..., min_length=1, max_length=64)
    filters: FiltersIn = Field(default_factory=FiltersIn)
    limit: int = Field(default=13, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=1000)


class FeedbackRequest(BaseModel):
    intent_id: str = Field(..., min_length=1, max_length=64)
    series_id: int = Field(..., ge=1)
    vote: Literal[1, -1]
    note: Optional[str] = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        return stripped if stripped else None


class IntentOut(BaseModel):
    must: list[str]
    boosts: list[str]
    keywords: list[str]
    similar_to: list[str]
    format: Optional[str] = None
    readable_only: bool


class SeriesResultOut(BaseModel):
    id: int
    title: str
    type: Literal["manga", "manhwa", "manhua"]
    year: Optional[int] = None
    status: Optional[str] = None
    rating: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    cover: Optional[str] = None
    synopsis: str = Field(..., max_length=300)
    tags: list[str] = Field(..., max_length=6)
    matched_tags: list[str]
    content_rating: Literal["safe", "suggestive", "erotica"]
    readable: bool
    format: Literal["oneshot", "short_series", "series", "unknown"]


class LatencyMsOut(BaseModel):
    llm: int
    db: int


class RecommendResponse(BaseModel):
    intent_id: str
    intent: IntentOut
    results: list[SeriesResultOut]
    total: int
    latency_ms: LatencyMsOut
    degraded: bool
    not_found_titles: list[str]
    skipped_titles: list[str]


class FeedbackResponse(BaseModel):
    ok: bool
    id: int


class HealthResponse(BaseModel):
    ok: bool
    db_records: int
    taxonomy_tags: int
    cache_size: int


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------- App & Middleware Setup ----------
def _parse_cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "*")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


def create_app() -> FastAPI:
    application = FastAPI(
        title="Serious Recommender API",
        version="0.5.0",
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )

    origins = _parse_cors_origins()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=("*" not in origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(APIError)
    async def api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "Invalid input")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "; ".join(parts)}},
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}},
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}},
        )

    def _client_ip(request: Request) -> str:
        if os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
            xff = request.headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def enforce_rate_limit(request: Request) -> None:
        ip = _client_ip(request)
        if not rate_limiter.allow(ip):
            raise APIError(
                status_code=429,
                code="RATE_LIMITED",
                message="Per-IP request limit exceeded. Please try again later.",
            )

    @application.post(
        "/recommend",
        response_model=RecommendResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def post_recommend(body: RecommendRequest) -> RecommendResponse:
        payload = router.recommend(
            text=body.text,
            filters=body.filters.model_dump(),
            seed_titles=body.seed_titles,
            limit=body.limit,
            offset=body.offset,
            store=state_store,
            db=catalog_db,
        )
        return RecommendResponse(**payload)

    @application.post(
        "/refilter",
        response_model=RecommendResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def post_refilter(body: RefilterRequest) -> RecommendResponse:
        payload = router.refilter(
            intent_id=body.intent_id,
            filters=body.filters.model_dump(),
            limit=body.limit,
            offset=body.offset,
            store=state_store,
            db=catalog_db,
        )
        if payload is None:
            raise APIError(
                status_code=404,
                code="INTENT_NOT_FOUND",
                message=f"Unknown intent_id: {body.intent_id}",
            )
        return RecommendResponse(**payload)

    @application.post(
        "/feedback",
        response_model=FeedbackResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    def post_feedback(body: FeedbackRequest) -> FeedbackResponse:
        row_id = state_store.append_feedback(
            intent_id=body.intent_id,
            series_id=body.series_id,
            vote=body.vote,
            note=body.note,
        )
        return FeedbackResponse(ok=True, id=row_id)

    @application.get("/health", response_model=HealthResponse)
    def get_health() -> JSONResponse:
        try:
            db_records = catalog_db.get_record_count()
            cache_size = state_store.get_cache_size()
            ok = True
            status_code = 200
        except Exception:
            db_records = 0
            cache_size = 0
            ok = False
            status_code = 503
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": ok,
                "db_records": db_records,
                "taxonomy_tags": len(router.SLUGS),
                "cache_size": cache_size,
            },
        )

    return application


app = create_app()
