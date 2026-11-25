# Serious Recommender Backend API

HTTP API wrapper built around `router.py`, `db.py`, `cache.py`, and `build_db.py` for natural-language manga/manhwa/manhua recommendations.

## Files

- `api.py`: FastAPI endpoints (`/recommend`, `/refilter`, `/feedback`, `/health`), Pydantic schemas, CORS, per-IP rate limiting, and structured JSON error handling.
- `router.py`: Gemini intent extraction with model hedging, taxonomy tag pruning, prompt rules (`similar_to`, `already_read_titles`, `negative_tags`), `lexical_intent` fallback, FTS5 query building, and candidate reranking.
- `db.py`: Thread-safe SQLite catalog access, server-side rating/type/format enforcement, seed title resolution (`title_alternates`), and tag extraction.
- `cache.py`: Disk-backed SQLite store (`intent_cache`, `intent_sessions`, `daily_quota`, `feedback`) and sliding-window per-IP rate limiter.
- `build_db.py`: Builds `catalog_v1.sqlite` and `taxonomy_v1.json` with `content_rating`, `tag_weights`, `title_alternates`, and FTS5 `flags` tokens (`type_*`, `rating_*`, `fmt_*`, `read_*`).
- `test_api.py`: Pytest suite verifying filtering, pornographic exclusion, caching, `/refilter`, seed resolution, daily cap degradation, rate limits, feedback, and health checks.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(unset; falls back to `gemini_key.txt`)* | Gemini API key (never logged or returned) |
| `DB_PATH` | `./catalog_v1.sqlite` or `../catalog_v1.sqlite` | Path to catalog SQLite database |
| `TAXONOMY_PATH` | `./taxonomy_v1.json` or `../taxonomy_v1.json` | Path to taxonomy JSON file |
| `CACHE_DB_PATH` | `./state_v1.sqlite` | Path to persistent SQLite state database (cache, sessions, quota, feedback) |
| `CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |
| `GEMINI_DAILY_CAP` | `500` | Global daily Gemini API call cap before degrading to `lexical_intent` |
| `RATE_LIMIT_PER_MIN` | `20` | Per-IP sliding-window request limit per minute |
| `TRUST_PROXY` | `false` | If `true`, reads client IP from `X-Forwarded-For` |

## Rebuild the Database

From the repository root (where `series_slim.jsonl`, `series_avail.jsonl`, and `series.json` reside):

```bash
python backend/build_db.py
```

Or specify custom input/output paths via `SERIES_SLIM_PATH`, `SERIES_AVAIL_PATH`, `SERIES_JSON_PATH`, `DB_PATH`, and `TAXONOMY_PATH`.

## Run Locally

From the repository root:

```bash
.venv/bin/uvicorn backend.api:app --host 0.0.0.0 --port 8000 --reload
# or equivalently:
.venv/bin/uvicorn api:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

Or from inside `backend/`:

```bash
cd backend
export GEMINI_API_KEY="your-key"
export CORS_ORIGINS="http://localhost:5173,http://localhost:3000"
../.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

## Run Tests

```bash
pytest backend/test_api.py -v
```

## Run with Docker

Mount a `/data` volume containing `catalog_v1.sqlite` and `taxonomy_v1.json`:

```bash
docker build -t serious-recommender-api ./backend
docker run -p 8000:8000 \
  -v /path/to/data:/data \
  -e GEMINI_API_KEY="your-key" \
  -e CORS_ORIGINS="http://localhost:3000" \
  serious-recommender-api
```
