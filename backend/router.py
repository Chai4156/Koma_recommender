"""Intent extraction, query building, candidate reranking, and recommend/refilter pipelines."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .cache import StateStore, state_store
    from .db import CatalogDB, SchemaCaps, catalog_db
except ImportError:
    from cache import StateStore, state_store
    from db import CatalogDB, SchemaCaps, catalog_db

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

KEY_FILE = "gemini_key.txt"
USE_LLM = True
FORMATS = {"oneshot", "short_series", "series"}

TOTAL_TIMEOUT_S = 12
HEDGE_DELAY_S = 3.5

SYNOPSIS_WEIGHT = 6.0
RATING_WEIGHT = 1.0
CANDIDATE_POOL_LIMIT = 200

MODELS = [
    # Primary high-quota tier (15 RPM / 500 RPD each)
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-4-hydrogen",
]

# (tier, rpm_limit, rpd_limit) based on AI Studio quota limits; skip 0/0 and retired 2.5 models
MODEL_LIMITS: dict[str, tuple[int, int, int]] = {
    "gemini-3.5-flash-lite": (0, 15, 500),
    "gemini-3.1-flash-lite": (0, 15, 500),
    "gemini-3.8-flash": (1, 5, 20),
    "gemini-3.6-flash": (1, 5, 20),
    "gemini-4-hydrogen": (1, 5, 20),
}


def _default_taxonomy_path() -> str:
    env_path = os.environ.get("TAXONOMY_PATH")
    if env_path:
        return env_path
    local_tax = BASE_DIR / "taxonomy_v1.json"
    if local_tax.exists():
        return str(local_tax)
    return str(ROOT_DIR / "taxonomy_v1.json")


def _load_key() -> str | None:
    """Load Gemini API key from GEMINI_API_KEY env var first, falling back to gemini_key.txt locally."""
    env_k = os.environ.get("GEMINI_API_KEY")
    if env_k and env_k.strip():
        return env_k.strip()
    for candidate in (BASE_DIR / KEY_FILE, ROOT_DIR / KEY_FILE):
        if candidate.exists():
            lines = [
                line.strip()
                for line in candidate.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if lines:
                return lines[-1]
    return None


def get_daily_cap() -> int:
    raw = os.environ.get("GEMINI_DAILY_CAP")
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 500


STOPWORDS = set(
    """a an the and or but of to in on at for with by from as is are was were be been
it its this that these those i me my we you your he she they them his her their gets get got like
about something someone anything some any can could would should will just really very more most
so than then there when where who what which while into out up down over also not no yes want
read reading story manga manhwa manhua comes come back make makes made one""".split()
)


def load_taxonomy(taxonomy_path: str | None = None) -> tuple[list[dict[str, Any]], set[str]]:
    path = taxonomy_path or _default_taxonomy_path()
    with open(path, encoding="utf-8") as f:
        tax_data = json.load(f)
    return tax_data, {t["slug"] for t in tax_data}


tax, SLUGS = load_taxonomy()

# ---------- 1. Prompt & Schema ----------
SYSTEM = f"""You convert a reader's request for manga/manhwa/manhua into search filters.
Reply with ONLY a JSON object with these keys:
must_have_tags, boost_tags, keywords, similar_to, format, readable_only, mature_intent, negative_tags, already_read_titles

Rules:
- Tags MUST come from the allowed list below. Never invent a tag.
- Never output or recommend series titles. You only extract filters and copy titles the user explicitly typed.
- similar_to: list of seed series titles the reader says they liked or want recommendations LIKE / SIMILAR TO (for example "like ORV but darker", "similar to Solo Leveling"). Copy ONLY titles the user actually typed; never invent, guess, or suggest titles.
- must_have_tags: usually exactly 1 tag, the single most defining and SPECIFIC idea in the request. Never use a broad genre (romance, action, fantasy, comedy, drama) as the must-have tag when a more specific tag fits; put broad genres in boost_tags instead. Use 2 must tags only when the request names two clearly separate elements (for example "school" and "game").
- Near-synonym or neighboring tags (for example apocalypse / post_apocalyptic / dystopian) go in boost_tags. Never use more than one of them as a must-have tag.
- boost_tags: 2-5 supporting tags. Prefer SPECIFIC trope or situation tags over generic ones. Do NOT use generic boosts such as male_lead, female_lead, comedy, drama, school, romance, action or fantasy unless the request is about nothing more specific than that.
- When the request says WHO falls in love (for example "a male lead falling for ..."), use the direction-of-love tags (male_lead_falls_in_love_first, female_lead_falls_in_love_first) if they are in the allowed list. For "who falls in love" requests, the direction-of-love tag is the must-have tag, and the description of the other person (unpopular_protagonist, outcasts, bullied_male_lead, bullied_female_lead, loner_protagonist) goes in boost_tags. Pick the gendered variant that matches the person being fallen for.
- Describe-the-character requests: if the request says unpopular, outcast, loner, bullied or nerdy, use the matching tags from the allowed list (for example unpopular_protagonist, outcasts, loner_protagonist, bullied_male_lead, bullied_female_lead) when they exist.
- Character role vs relationship: if the request says what kind of person the lead IS (a bully, a scammer, a villain) and how they change or what they do, choose tags that describe that character and their arc (for example bullying plus a growth or redemption tag). Choose tags about a romantic dynamic (for example a character who bullies the one they love) ONLY when the request says the character targets someone they like or love.
- Tone and mood words (gritty, dark, bleak, grim, wholesome, lighthearted): map them to the closest tone or atmosphere tag in the allowed list and put it in boost_tags. Art style cannot be searched; do not put art words in keywords.
- Gender: when the request specifies the gender of a character (male lead, female lead, FL, ML, boy, girl), use only the tag variant for that gender. Never use the opposite-gender variant as a substitute. If no tag exists for the requested type of character, do not pick a different one: use the closest broader tag that is in the allowed list and put the distinctive word from the request in keywords.
- keywords: 0-3 rare, concrete nouns that would appear in a matching synopsis (for example "dungeon", "tavern", "chef"). A keyword may be a short phrase of up to 3 words when it is a named concept in the request (for example a title of rank or a faction name). Add a keyword ONLY if the request itself implies it. Never use common words like life, love, heart, bond, world, story. If no such word fits, return an empty list.
- format: set "oneshot", "short_series" or "series" ONLY if the reader explicitly asks for that length. Otherwise "none".
- readable_only: true only if the reader asks for something they can read officially in English.
- mature_intent: true only if the reader explicitly asks for smut, explicit, sexual or 18+ content. Otherwise false.
- negative_tags: 0-3 tags from the allowed list to strictly avoid if the reader asks to exclude certain tropes or genres (for example "no harem", "no BL", "without romance"). Never put negative constraints in must_have_tags or boost_tags.
- already_read_titles: list of series titles mentioned in the request that the reader explicitly asked to exclude or already finished without wanting recommendations based on them. Copy ONLY titles the user typed.
- Community terms: "regression", "regressor" or "returner" means the hero goes back in time with their memories. Use time_rewind as the must-have tag, and put any allowed tag with "regress" in its name (regressor_protagonist, regressed_male_lead, regressed_female_lead) in boost_tags. NEVER use age_regression for this: it means a character becoming younger in body, which is a different trope.
- "Isekai" means transported to another world. "MC" means main character. "Dungeon", "hunter" and "system" are usually game-style power stories.
- Never use generic business or setting words like company, school, city, family.
- If the request names a distinctive setting, profession or social position (for example tycoon, chef, doctor, idol, noble), find the closest matching tags in the allowed list and put them in boost_tags. If the request has two clearly separate ideas, one of them that setting, use it as a second must-have tag. Do not fill boost_tags with generic companions of the main trope (for example revenge or betrayal for a regression-revenge request).

Allowed tags:
{", ".join(sorted(SLUGS))}
"""

PROMPT_VERSION = hashlib.md5(SYSTEM.encode("utf-8")).hexdigest()[:12]

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "must_have_tags": {"type": "array", "items": {"type": "string"}},
        "boost_tags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "similar_to": {"type": "array", "items": {"type": "string"}},
        "format": {"type": "string", "enum": ["oneshot", "short_series", "series", "none"]},
        "readable_only": {"type": "boolean"},
        "mature_intent": {"type": "boolean"},
        "negative_tags": {"type": "array", "items": {"type": "string"}},
        "already_read_titles": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "must_have_tags",
        "boost_tags",
        "keywords",
        "similar_to",
        "format",
        "readable_only",
        "mature_intent",
        "negative_tags",
        "already_read_titles",
    ],
}

# ---------- 2. Model calls ----------
_client = None
_client_key = None


def _call_gemini(model: str, user_text: str) -> str | None:
    global _client, _client_key
    from google import genai
    from google.genai import types

    key = _load_key()
    if not key:
        raise RuntimeError("Missing Gemini API key")
    if _client is None or _client_key != key:
        _client = genai.Client(api_key=key)
        _client_key = key

    resp = _client.models.generate_content(
        model=model,
        contents=SYSTEM + "\n\nReader request: " + user_text,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=INTENT_SCHEMA,
            temperature=0.0,
        ),
    )
    return resp.text


class ModelLoadBalancer:
    """Thread-safe least-loaded model selector with per-model RPM/RPD tracking and cooldowns."""

    COOLDOWN_429_S = 45.0
    COOLDOWN_503_S = 15.0
    COOLDOWN_404_S = 3600.0
    COOLDOWN_ERR_S = 10.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rpm_hits: dict[str, deque[float]] = defaultdict(deque)
        self._rpd_hits: dict[str, int] = defaultdict(int)
        self._rpd_day: str = self._today_utc()
        self._cooldown_until: dict[str, float] = {}
        self._rr_cursor: int = 0

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _prune_locked(self, now: float) -> None:
        today = self._today_utc()
        if today != self._rpd_day:
            self._rpd_hits.clear()
            self._rpd_day = today
        cutoff = now - 60.0
        for dq in self._rpm_hits.values():
            while dq and dq[0] <= cutoff:
                dq.popleft()

    def select_order(self, models: list[str] | None = None) -> list[str]:
        """Return candidate models ordered by health, tier, and lowest current RPM/RPD load."""
        candidates = [m for m in (models or MODELS) if m]
        if not candidates:
            return []
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            cursor = self._rr_cursor
            self._rr_cursor = (self._rr_cursor + 1) % len(candidates)

            healthy: list[tuple[tuple[Any, ...], str]] = []
            cooling: list[tuple[tuple[Any, ...], str]] = []

            for idx, m in enumerate(candidates):
                tier, rpm_cap, rpd_cap = MODEL_LIMITS.get(m, (1, 5, 20))
                rpm_used = len(self._rpm_hits[m])
                rpd_used = self._rpd_hits.get(m, 0)
                rpm_sat = 1 if rpm_used >= rpm_cap else 0
                rpd_sat = 1 if rpd_used >= rpd_cap else 0
                cd_until = self._cooldown_until.get(m, 0.0)
                rr_tie = (idx - cursor) % len(candidates)

                if cd_until > now:
                    cooling.append(((cd_until, tier, rpd_sat, rpm_sat, rr_tie), m))
                else:
                    key = (
                        rpd_sat,
                        tier,
                        rpm_sat,
                        rpm_used / max(1, rpm_cap),
                        rpd_used / max(1, rpd_cap),
                        rr_tie,
                    )
                    healthy.append((key, m))

            healthy.sort(key=lambda x: x[0])
            cooling.sort(key=lambda x: x[0])
            # Prefer healthy models first; append cooling models at the tail only as last resort
            return [m for _, m in healthy] + [m for _, m in cooling]

    def record_dispatch(self, model: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            self._rpm_hits[model].append(now)
            self._rpd_hits[model] += 1

    def record_success(self, model: str) -> None:
        with self._lock:
            self._cooldown_until.pop(model, None)

    def record_error(self, model: str, exc: Exception | None = None) -> None:
        now = time.monotonic()
        msg = f"{type(exc).__name__}: {exc}".lower() if exc else ""
        if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
            cd = self.COOLDOWN_429_S
        elif "404" in msg or "not_found" in msg:
            cd = self.COOLDOWN_404_S
        elif "503" in msg or "500" in msg or "unavailable" in msg or "high demand" in msg:
            cd = self.COOLDOWN_503_S
        else:
            cd = self.COOLDOWN_ERR_S
        with self._lock:
            self._cooldown_until[model] = max(self._cooldown_until.get(model, 0.0), now + cd)

    def reset(self) -> None:
        with self._lock:
            self._rpm_hits.clear()
            self._rpd_hits.clear()
            self._cooldown_until.clear()
            self._rr_cursor = 0


model_balancer = ModelLoadBalancer()


def call_llm(user_text: str) -> str | None:
    """Hedged Gemini request ordered by least current load & health; returns JSON text or None."""
    queue = model_balancer.select_order(MODELS)
    if not queue:
        return None
    ex = ThreadPoolExecutor(max_workers=len(queue))
    pending: dict[Any, str] = {}
    t0 = time.time()

    def launch() -> None:
        if queue:
            m = queue.pop(0)
            model_balancer.record_dispatch(m)
            pending[ex.submit(_call_gemini, m, user_text)] = m

    launch()
    while pending and time.time() - t0 < TOTAL_TIMEOUT_S:
        remaining = TOTAL_TIMEOUT_S - (time.time() - t0)
        done, _ = wait(
            list(pending),
            timeout=min(HEDGE_DELAY_S, remaining),
            return_when=FIRST_COMPLETED,
        )
        if not done:
            launch()
            continue
        for fut in done:
            model = pending.pop(fut)
            try:
                res = fut.result()
                if res and res.strip():
                    model_balancer.record_success(model)
                    ex.shutdown(wait=False, cancel_futures=True)
                    return res
                model_balancer.record_error(model, RuntimeError("Empty LLM response"))
                launch()
            except Exception as exc:
                model_balancer.record_error(model, exc)
                launch()
    ex.shutdown(wait=False, cancel_futures=True)
    return None


# ---------- 3. Intent parsing & fallback ----------
def _normalize_text(user_text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (user_text or "").lower()))


def parse_intent(raw_text: str | None, user_text: str = "") -> dict[str, Any] | None:
    """Parse LLM JSON while strictly dropping any tag slug not in taxonomy SLUGS."""
    if not raw_text:
        return None
    try:
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return None
    except Exception:
        return None

    def clean_tags(tags: Any) -> list[str]:
        if not isinstance(tags, list):
            return []
        seen: set[str] = set()
        out: list[str] = []
        for t in tags:
            if isinstance(t, str) and t in SLUGS and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    must = clean_tags(data.get("must_have_tags", []))
    boost = clean_tags(data.get("boost_tags", []))
    neg = clean_tags(data.get("negative_tags", []))

    if user_text:
        if re.search(r"\bno\s+bl\b|\bwithout\s+bl\b|\bnot?\s+bl\b", user_text, re.I):
            for t in ("boys_love", "boys_love_subtext"):
                if t in SLUGS and t not in neg:
                    neg.append(t)
        if re.search(r"\bno\s+harem\b|\bnon[- ]harem\b|\bwithout\s+harem\b", user_text, re.I):
            for t in ("harem", "female_harem", "male_harem", "reverse_harem"):
                if t in SLUGS and t not in neg:
                    neg.append(t)

    def clean_strings(raw_list: Any, max_items: int) -> list[str]:
        if not isinstance(raw_list, list):
            return []
        seen: set[str] = set()
        out: list[str] = []
        for item in raw_list:
            s = str(item).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
            if len(out) >= max_items:
                break
        return out

    similar_to = clean_strings(data.get("similar_to", []), max_items=10)
    already_read = clean_strings(data.get("already_read_titles", []), max_items=15)

    fmt = data.get("format")
    fmt = fmt if fmt in FORMATS else None

    keywords_raw = data.get("keywords", [])
    keywords = (
        [str(k).lower().strip() for k in keywords_raw if str(k).strip()][:3]
        if isinstance(keywords_raw, list)
        else []
    )

    return {
        "must": must[:2],
        "boost": boost[:5],
        "keywords": keywords,
        "similar_to": similar_to,
        "format": fmt,
        "readable_only": bool(data.get("readable_only", False)),
        "mature_intent": bool(data.get("mature_intent", False)),
        "negative_tags": neg[:3],
        "already_read_titles": already_read,
    }


def lexical_intent(user_text: str) -> dict[str, Any]:
    """No-LLM fallback: stopword-filtered words plus deterministic constraint extraction."""
    words = [w for w in re.findall(r"[a-z]{4,}", (user_text or "").lower()) if w not in STOPWORDS]
    neg: list[str] = []
    if re.search(r"\bno\s+bl\b|\bwithout\s+bl\b|\bnot?\s+bl\b", user_text or "", re.I):
        for t in ("boys_love", "boys_love_subtext"):
            if t in SLUGS:
                neg.append(t)
    if re.search(r"\bno\s+harem\b|\bnon[- ]harem\b|\bwithout\s+harem\b", user_text or "", re.I):
        for t in ("harem", "female_harem", "male_harem", "reverse_harem"):
            if t in SLUGS:
                neg.append(t)

    # Deduplicate words preserving order
    kws: list[str] = []
    for w in words:
        if w not in kws:
            kws.append(w)
        if len(kws) >= 5:
            break

    return {
        "must": [],
        "boost": [],
        "keywords": kws,
        "similar_to": [],
        "format": None,
        "readable_only": False,
        "mature_intent": False,
        "negative_tags": neg[:3],
        "already_read_titles": [],
    }


def get_or_extract_intent(
    user_text: str,
    store: StateStore = state_store,
    llm_caller: Callable[[str], str | None] | None = None,
) -> tuple[dict[str, Any], bool, int]:
    """Return (base_intent, degraded, llm_latency_ms) using disk cache and daily quota."""
    norm = _normalize_text(user_text)
    if not norm:
        # Pure seed-title search with no text needs no LLM call and is not degraded.
        return lexical_intent(""), False, 0

    cached = store.get_cached_intent(norm, PROMPT_VERSION)
    if cached is not None:
        # Enforce taxonomy pruning on cached payloads too for safety
        cached["must"] = [t for t in cached.get("must", []) if t in SLUGS]
        cached["boost"] = [t for t in cached.get("boost", []) if t in SLUGS]
        cached["negative_tags"] = [t for t in cached.get("negative_tags", []) if t in SLUGS]
        cached.setdefault("similar_to", [])
        cached.setdefault("already_read_titles", [])
        cached.setdefault("keywords", [])
        cached.setdefault("format", None)
        cached.setdefault("readable_only", False)
        cached.setdefault("mature_intent", False)
        return cached, False, 0

    if not USE_LLM:
        return lexical_intent(user_text), True, 0

    if not store.try_consume_gemini_quota(get_daily_cap()):
        return lexical_intent(user_text), True, 0

    t0 = time.time()
    caller = llm_caller or call_llm
    raw = caller(user_text)
    llm_ms = int(round((time.time() - t0) * 1000))

    parsed = parse_intent(raw, user_text=user_text)
    if parsed is None:
        # Never cache LLM failures
        return lexical_intent(user_text), True, llm_ms

    store.save_cached_intent(norm, PROMPT_VERSION, parsed)
    return parsed, False, llm_ms


# ---------- 4. Build the FTS5 query ----------
def _sanitize_kw(kw: str) -> str:
    return re.sub(r'["\*\(\)]', " ", kw).strip()


def build_query(
    it: dict[str, Any],
    loosen: int = 0,
    filters: dict[str, Any] | None = None,
    caps: SchemaCaps | None = None,
) -> str | None:
    """Build FTS5 MATCH string with tag/keyword terms and server-side flag filters.

    loosen 0 = strict must tags AND (boosts OR keywords),
    loosen 1 = soft default (must tags join OR group),
    loosen 2 = keywords only.
    """
    must = list(it.get("must", []))
    boost = list(it.get("boost", []))
    kws = [_sanitize_kw(k) for k in it.get("keywords", []) if _sanitize_kw(k)]

    if loosen >= 1:
        boost = must + boost
        must = []
    if loosen >= 2:
        boost = []

    ors = [f'tags:"{t}"' for t in boost] + [f'synopsis:"{k}"' for k in kws]
    musts = [f'tags:"{t}"' for t in must]

    if musts and ors:
        q = " AND ".join(musts) + " AND (" + " OR ".join(ors) + ")"
    elif musts:
        q = " AND ".join(musts)
    elif ors:
        q = " OR ".join(ors)
    else:
        return None

    q = f"({q})"

    f = filters or {}
    allowed_ratings = CatalogDB.allowed_ratings(f)

    if caps is None or caps.has_rating_flags:
        if len(allowed_ratings) == 1:
            q += f' AND flags:"rating_{allowed_ratings[0]}"'
        else:
            rating_ors = " OR ".join(f'flags:"rating_{r}"' for r in allowed_ratings)
            q += f" AND ({rating_ors})"

    ftype = f.get("type", "all")
    if ftype and ftype != "all" and (caps is None or caps.has_type_flags):
        q += f' AND flags:"type_{ftype}"'

    ffmt = f.get("format", "any")
    eff_fmt = ffmt if (ffmt and ffmt != "any") else it.get("format")
    if eff_fmt and eff_fmt != "none":
        q += f' AND flags:"fmt_{eff_fmt}"'

    if f.get("readable_only") or it.get("readable_only"):
        q += ' AND flags:"read_yes"'

    for nt in it.get("negative_tags", []):
        q += f' NOT tags:"{nt}"'

    return q


# ---------- 5. Reranking & Result Formatting ----------
def rerank_candidates(
    candidates: list[tuple[Any, ...]],
    user_text: str | None = None,
    it: dict[str, Any] | None = None,
    k: int = 13,
) -> list[tuple[Any, ...]]:
    if not candidates:
        return []
    if len(candidates) <= 1:
        return candidates[:k]

    user_text = user_text or ""
    it = it or {}

    wants_manhwa = bool(re.search(r"\b(?:manhwas?|mahnwas?|webtoons?)\b", user_text, re.I))
    wants_manga = bool(re.search(r"\b(?:mangas?|jap(?:an|anese))\b", user_text, re.I))
    wants_manhua = bool(re.search(r"\b(?:manhuas?|chinese)\b", user_text, re.I))
    wants_completed = bool(re.search(r"\bcompleted\b", user_text, re.I))

    no_rom = bool(re.search(r"\b(?:no|without|little\s+or\s+no|not?)\s+romance\b", user_text, re.I))
    wants_romance = bool(re.search(r"\b(?:romance|romantic|fluff)\b", user_text, re.I)) and not no_rom

    boost_slugs = set(it.get("boost", []))
    must_slugs = set(it.get("must", []))

    bm25_vals = [c[11] for c in candidates]
    min_b = min(bm25_vals)
    max_b = max(bm25_vals)
    span_b = (max_b - min_b) if (max_b - min_b) > 1e-6 else 1.0

    w_bm25 = 12.0
    w_type = 5.0
    w_rating = 5.0
    w_pop = 1.5
    w_must = 8.0
    w_boost = 3.0
    w_status = 3.0

    scored: list[tuple[float, tuple[Any, ...]]] = []
    for c in candidates:
        (
            _cid,
            _title,
            ctype,
            cstatus,
            _cyear,
            _cformat,
            _cread,
            crating,
            cpop,
            ctags_json,
            _cdesc,
            cbm25,
        ) = c[:12]

        norm_b = (max_b - cbm25) / span_b
        score = norm_b * w_bm25

        if wants_manhwa and not wants_manga:
            score += w_type if ctype == "manhwa" else -w_type * 0.5
        elif wants_manga and not wants_manhwa:
            score += w_type if ctype == "manga" else -w_type * 0.5
        elif wants_manhua and not wants_manhwa:
            score += w_type if ctype == "manhua" else -w_type * 0.5

        if wants_completed:
            if cstatus == "completed":
                score += w_status
            elif cstatus in ("hiatus", "cancelled"):
                score -= w_status

        norm_r = max(0.0, min(1.0, ((crating or 65.0) - 55.0) / 35.0))
        score += norm_r * w_rating

        if cpop and cpop > 0:
            norm_pop = max(0.0, min(1.0, (5.0 - math.log10(cpop)) / 4.0))
            score += norm_pop * w_pop

        cand_tags = [t.lower() for t in json.loads(ctags_json)] if ctags_json else []
        cand_slugs = {re.sub(r"[^a-z0-9]+", "_", t).strip("_") for t in cand_tags}
        score += len(cand_slugs & must_slugs) * w_must + len(cand_slugs & boost_slugs) * w_boost

        if wants_romance:
            has_rom = any("romance" in t or "romantic" in t for t in cand_tags)
            score += 3.0 if has_rom else -3.0
        elif no_rom:
            has_rom = any("romance" in t or "romantic" in t for t in cand_tags)
            score += -3.0 if has_rom else 1.0

        scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    return [x[1] for x in scored[:k]]


def _format_synopsis(desc: str | None, max_len: int = 300) -> str:
    text = re.sub(r"\s+", " ", (desc or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _format_row(row: tuple[Any, ...], query_slugs: list[str]) -> dict[str, Any]:
    (
        cid,
        title,
        ctype,
        cstatus,
        cyear,
        cformat,
        _creadability,
        _crating_filled,
        _cpop,
        ctags_json,
        cdesc,
        _cbm25,
        raw_rating,
        cover,
        readable_int,
        content_rating,
    ) = row

    try:
        tags_list = json.loads(ctags_json) if ctags_json else []
    except (json.JSONDecodeError, TypeError):
        tags_list = []

    cand_slugs = {re.sub(r"[^a-z0-9]+", "_", str(t).lower()).strip("_") for t in tags_list}
    matched_tags = [s for s in query_slugs if s in cand_slugs]

    return {
        "id": int(cid),
        "title": str(title),
        "type": str(ctype),
        "year": int(cyear) if cyear is not None else None,
        "status": str(cstatus) if cstatus is not None else None,
        "rating": round(float(raw_rating) / 10.0, 1) if raw_rating is not None else None,
        "cover": str(cover) if cover else None,
        "synopsis": _format_synopsis(cdesc, max_len=300),
        "tags": [str(t) for t in tags_list[:6]],
        "matched_tags": matched_tags,
        "content_rating": str(content_rating or "safe"),
        "readable": bool(readable_int),
        "format": str(cformat or "unknown"),
    }


def _execute_pipeline(
    intent_id: str,
    base_intent: dict[str, Any],
    user_text: str,
    filters: dict[str, Any] | None,
    extra_seed_titles: list[str],
    degraded: bool,
    llm_ms: int,
    limit: int,
    offset: int,
    db: CatalogDB = catalog_db,
) -> dict[str, Any]:
    t_db0 = time.time()
    f = dict(filters or {})
    allowed_ratings = db.allowed_ratings(f)

    # Combine explicit seed_titles with LLM-extracted similar_to (preserving order, deduped)
    combined_seeds: list[str] = []
    seen_seed_lower: set[str] = set()
    for s in list(extra_seed_titles) + list(base_intent.get("similar_to", [])):
        cs = (s or "").strip()
        if cs and cs.lower() not in seen_seed_lower:
            seen_seed_lower.add(cs.lower())
            combined_seeds.append(cs)

    taxonomy_counts = {t["slug"]: int(t.get("records", 0)) for t in tax}
    seed_res = db.resolve_seed_titles(
        names=combined_seeds,
        allowed_ratings=allowed_ratings,
        taxonomy_slugs=SLUGS,
        max_tags_per_seed=8,
        taxonomy_counts=taxonomy_counts,
    )

    exclude_ids = set(seed_res["exclude_ids"])

    # Also resolve explicitly excluded already_read_titles (no boost expansion)
    already_read = [
        t for t in base_intent.get("already_read_titles", [])
        if t and t.strip().lower() not in seen_seed_lower
    ]
    if already_read:
        excl_res = db.resolve_seed_titles(
            names=already_read,
            allowed_ratings=["safe", "suggestive", "erotica"],
            taxonomy_slugs=SLUGS,
            max_tags_per_seed=0,
        )
        exclude_ids.update(excl_res["exclude_ids"])

    eff_must = list(base_intent.get("must", []))
    eff_boosts: list[str] = []
    for t in list(base_intent.get("boost", [])) + list(seed_res["boost_tags"]):
        if t in SLUGS and t not in eff_must and t not in eff_boosts:
            eff_boosts.append(t)

    eff_intent = dict(base_intent)
    eff_intent["must"] = eff_must
    eff_intent["boost"] = eff_boosts

    caps = db.get_caps()
    pool_limit = min(1000, max(CANDIDATE_POOL_LIMIT, offset + limit))

    candidates: list[tuple[Any, ...]] = []
    total = 0
    for loosen in (1, 2):
        fq = build_query(eff_intent, loosen=loosen, filters=f, caps=caps)
        if not fq:
            continue
        rows, total = db.fetch_candidates(
            fq,
            filters=f,
            intent=eff_intent,
            limit=pool_limit,
            synopsis_weight=SYNOPSIS_WEIGHT,
            rating_weight=RATING_WEIGHT,
            exclude_ids=exclude_ids,
        )
        if rows:
            candidates = rows
            break

    reranked = rerank_candidates(
        candidates,
        user_text=user_text,
        it=eff_intent,
        k=offset + limit,
    )
    page_rows = reranked[offset : offset + limit]

    query_slugs: list[str] = []
    for s in eff_must + eff_boosts:
        if s not in query_slugs:
            query_slugs.append(s)

    formatted_results = [_format_row(r, query_slugs) for r in page_rows]
    db_ms = int(round((time.time() - t_db0) * 1000))

    ffmt = f.get("format", "any")
    out_fmt = ffmt if (ffmt and ffmt != "any") else base_intent.get("format")
    out_readable = bool(f.get("readable_only") or base_intent.get("readable_only"))

    return {
        "intent_id": intent_id,
        "intent": {
            "must": eff_must,
            "boosts": eff_boosts,
            "keywords": list(base_intent.get("keywords", [])),
            "similar_to": combined_seeds,
            "format": out_fmt,
            "readable_only": out_readable,
        },
        "results": formatted_results,
        "total": total,
        "latency_ms": {"llm": llm_ms, "db": db_ms},
        "degraded": degraded,
        "not_found_titles": seed_res["not_found_titles"],
        "skipped_titles": seed_res["skipped_titles"],
    }


def recommend(
    text: str,
    filters: dict[str, Any] | None = None,
    seed_titles: list[str] | None = None,
    limit: int = 13,
    offset: int = 0,
    store: StateStore = state_store,
    db: CatalogDB = catalog_db,
    llm_caller: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Recommend manga/manhwa/manhua from natural-language text, filters, and optional seed titles."""
    extra_seeds = [s.strip() for s in (seed_titles or []) if s and s.strip()]
    base_intent, degraded, llm_ms = get_or_extract_intent(
        text,
        store=store,
        llm_caller=llm_caller,
    )

    norm = _normalize_text(text)
    intent_id = store.save_session(
        norm_text=norm,
        prompt_version=PROMPT_VERSION,
        base_intent=base_intent,
        seed_titles=extra_seeds,
        degraded=degraded,
    )

    return _execute_pipeline(
        intent_id=intent_id,
        base_intent=base_intent,
        user_text=text,
        filters=filters,
        extra_seed_titles=extra_seeds,
        degraded=degraded,
        llm_ms=llm_ms,
        limit=limit,
        offset=offset,
        db=db,
    )


def refilter(
    intent_id: str,
    filters: dict[str, Any] | None = None,
    limit: int = 13,
    offset: int = 0,
    store: StateStore = state_store,
    db: CatalogDB = catalog_db,
) -> dict[str, Any] | None:
    """Re-run search for a stored intent_id with new filters/offset without calling Gemini."""
    session = store.get_session(intent_id)
    if session is None:
        return None

    return _execute_pipeline(
        intent_id=intent_id,
        base_intent=session["base_intent"],
        user_text=session["norm_text"],
        filters=filters,
        extra_seed_titles=session["seed_titles"],
        degraded=session["degraded"],
        llm_ms=0,
        limit=limit,
        offset=offset,
        db=db,
    )


if __name__ == "__main__":
    while True:
        q = input("\nWhat do you want to read? (Enter to quit) > ").strip()
        if not q:
            break
        out = recommend(q)
        for row in out["results"]:
            print(
                f"  {row['title']} | {row['type']} | {row['content_rating']} "
                f"| {row['year']} | {row['format']} | readable={row['readable']}"
            )
