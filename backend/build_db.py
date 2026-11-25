"""Build catalog_v1.sqlite and taxonomy_v1.json from raw series exports."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

DEFAULT_SRC = os.environ.get("SERIES_SLIM_PATH") or str(ROOT_DIR / "series_slim.jsonl")
DEFAULT_AVAIL = os.environ.get("SERIES_AVAIL_PATH") or str(ROOT_DIR / "series_avail.jsonl")
DEFAULT_RAW_JSON = os.environ.get("SERIES_JSON_PATH") or str(ROOT_DIR / "series.json")
DEFAULT_DB = os.environ.get("DB_PATH") or str(ROOT_DIR / "catalog_v1.sqlite")
DEFAULT_TAXONOMY = os.environ.get("TAXONOMY_PATH") or str(ROOT_DIR / "taxonomy_v1.json")

MIN_TAG_RECORDS = 100
KEEP_TYPES = {"manga", "manhwa", "manhua"}
# Keep safe, suggestive, and erotica in the catalog; never index pornographic records.
KEEP_RATINGS = {"safe", "suggestive", "erotica"}

WEIGHT_PRIORITY = {
    "core": 0,
    "defining": 1,
    "recurrent": 2,
    "unweighted": 3,
    "incidental": 4,
}

WATCH = [
    "unpopular_protagonist", "outcasts", "outcast_protagonist", "bully",
    "bullied_male_lead", "bullied_female_lead", "deliberate_loner",
    "regressor_protagonist", "regressed_male_lead", "regressed_female_lead",
    "male_lead_falls_in_love_first", "female_lead_falls_in_love_first",
    "time_rewind", "age_regression", "revenge",
]

SYNONYMS = {
    "Yaoi": "Boys Love",
    "Shounen Ai": "Boys Love",
    "Yuri": "Girls Love",
    "Non-human": "Non-Human Protagonist",
    "School Life": "School",
}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def canon(name: str) -> str:
    return SYNONYMS.get(name, name)


def norm_alias(s: str | None) -> str:
    folded = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def is_format_tag(name_path: str | None) -> bool:
    return (name_path or "").startswith(("Work Info", "Audience Demographics"))


def in_scope(r: dict[str, Any]) -> bool:
    if r.get("merged_with"):
        return False
    if r.get("type") not in KEEP_TYPES:
        return False
    if r.get("content_rating") not in KEEP_RATINGS:
        return False
    if not r.get("title"):
        return False
    if not r.get("tags2") and not r.get("description"):
        return False
    return True


def record_tags(r: dict[str, Any]) -> dict[str, str]:
    """Canonical tag names mapped to their highest weight priority seen."""
    out: dict[str, str] = {}
    for entry in r.get("tags2") or []:
        name, weight, _level, path = entry
        if is_format_tag(path):
            continue
        cname = canon(name)
        if cname not in out or WEIGHT_PRIORITY.get(weight, 9) < WEIGHT_PRIORITY.get(out[cname], 9):
            out[cname] = weight
    return out


def format_class(a: dict[str, Any]) -> str:
    """Classify into oneshot / short_series / series / unknown."""
    ch, fv, status = a.get("total_chapters"), a.get("final_volume"), a.get("status")
    if a.get("oneshot_tag") and (ch is None or ch < 5):
        return "oneshot"
    if ch is not None:
        if ch <= 1:
            return "oneshot" if status == "completed" else "unknown"
        return "short_series" if ch <= 10 else "series"
    if fv is not None:
        return "short_series" if fv <= 1 else "series"
    return "unknown"


def readability(a: dict[str, Any]) -> str:
    """Evidence of an English release inside this dataset only."""
    if (a.get("n_english_publishers") or 0) > 0 or a.get("is_licensed"):
        return "official_english"
    if "en" in (a.get("webplatform_langs") or []):
        return "english_online"
    if a.get("has_en_title"):
        return "english_title_only"
    return "untranslated_likely"


def extract_title_alternates_from_record(rec: dict[str, Any]) -> list[str]:
    """Collect unique alternate title strings from raw 'titles' and 'secondary_titles'."""
    primary = (rec.get("title") or "").strip()
    primary_norm = norm_alias(primary)
    seen_norms: set[str] = {primary_norm} if primary_norm else set()
    alts: list[str] = []

    def add_candidate(raw_val: str | None) -> None:
        if not raw_val:
            return
        cleaned = raw_val.strip()
        n = norm_alias(cleaned)
        if cleaned and (not n or n not in seen_norms):
            if n:
                seen_norms.add(n)
            alts.append(cleaned)

    for item in rec.get("titles") or []:
        if isinstance(item, dict):
            add_candidate(item.get("title"))
        elif isinstance(item, str):
            add_candidate(item)

    sec = rec.get("secondary_titles") or {}
    if isinstance(sec, dict):
        for tlist in sec.values():
            if isinstance(tlist, list):
                for st in tlist:
                    if isinstance(st, dict):
                        add_candidate(st.get("title"))
                    elif isinstance(st, str):
                        add_candidate(st)
    return alts


def _load_alternates_map(raw_json_path: str | None, scope_ids: set[int]) -> dict[int, list[str]]:
    """Stream raw series.json if present to collect alternate titles for in-scope IDs."""
    alt_map: dict[int, list[str]] = {}
    if not raw_json_path or not os.path.exists(raw_json_path) or not scope_ids:
        return alt_map
    try:
        import ijson
    except ImportError:
        return alt_map

    with open(raw_json_path, "rb") as f:
        for item in ijson.items(f, "item"):
            sid = item.get("id")
            if sid in scope_ids:
                alts = extract_title_alternates_from_record(item)
                if alts:
                    alt_map[sid] = alts
    return alt_map


def _rows(src_path: str, avail_path: str) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    with open(src_path, encoding="utf-8") as fs, open(avail_path, encoding="utf-8") as fa:
        for ls, la in zip(fs, fa):
            r, a = json.loads(ls), json.loads(la)
            if r["id"] != a["id"]:
                raise SystemExit(f"Files out of order at id {r['id']} vs {a['id']}")
            yield r, a


def build_database(
    src_path: str = DEFAULT_SRC,
    avail_path: str = DEFAULT_AVAIL,
    raw_json_path: str | None = DEFAULT_RAW_JSON,
    db_path: str = DEFAULT_DB,
    taxonomy_out: str = DEFAULT_TAXONOMY,
    min_tag_records: int = MIN_TAG_RECORDS,
    verbose: bool = True,
) -> dict[str, Any]:
    """Build SQLite catalog with FTS5 indexes, content_rating, and title_alternates."""
    counts: Counter[str] = Counter()
    ratings: list[float] = []
    scope_ids: set[int] = set()

    for r, _a in _rows(src_path, avail_path):
        if not in_scope(r):
            continue
        scope_ids.add(int(r["id"]))
        counts.update(record_tags(r).keys())
        if r.get("rating") is not None:
            ratings.append(float(r["rating"]))

    keep = {t for t, c in counts.items() if c >= min_tag_records}
    median_rating = sorted(ratings)[len(ratings) // 2] if ratings else 70.0

    taxonomy = [
        {"name": t, "slug": slug(t), "records": counts[t]}
        for t in sorted(keep, key=lambda t: (-counts[t], t))
    ]
    os.makedirs(os.path.dirname(os.path.abspath(taxonomy_out)), exist_ok=True)
    with open(taxonomy_out, "w", encoding="utf-8") as f:
        json.dump(taxonomy, f, ensure_ascii=False, indent=1)

    if verbose:
        print(f"In scope: {len(scope_ids)}")
        print(f"Tags kept: {len(keep)} of {len(counts)} (MIN_TAG_RECORDS = {min_tag_records})")
        print(f"Median rating (used for unrated): {median_rating}")

    # Load title alternates from raw series.json if present (slim exports lack raw titles)
    alt_map = _load_alternates_map(raw_json_path, scope_ids)

    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE manga_metadata (
            id INTEGER PRIMARY KEY,
            title TEXT,
            type TEXT,
            content_rating TEXT,
            status TEXT,
            year INTEGER,
            rating REAL,
            rating_filled REAL,
            cover TEXT,
            description TEXT,
            tags TEXT,
            tag_weights TEXT,
            pop_global INTEGER,
            format TEXT,
            readability TEXT,
            readable INTEGER,
            total_chapters REAL
        );
        CREATE INDEX idx_meta_format ON manga_metadata(format);
        CREATE INDEX idx_meta_readable ON manga_metadata(readable);
        CREATE INDEX idx_meta_type ON manga_metadata(type);
        CREATE INDEX idx_meta_rating ON manga_metadata(content_rating);
        CREATE VIRTUAL TABLE manga_search USING fts5(
            title,
            title_alternates,
            tags,
            synopsis,
            flags,
            tokenize = "unicode61 remove_diacritics 2 tokenchars '_'"
        );
        CREATE TABLE title_aliases (
            series_id INTEGER,
            alias TEXT,
            alias_norm TEXT
        );
        """
    )

    batch_meta: list[tuple[Any, ...]] = []
    batch_fts: list[tuple[Any, ...]] = []
    batch_aliases: list[tuple[int, str, str]] = []

    def flush() -> None:
        if not batch_meta:
            return
        con.executemany(
            """
            INSERT INTO manga_metadata (
                id, title, type, content_rating, status, year,
                rating, rating_filled, cover, description, tags, tag_weights,
                pop_global, format, readability, readable, total_chapters
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            batch_meta,
        )
        con.executemany(
            """
            INSERT INTO manga_search(rowid, title, title_alternates, tags, synopsis, flags)
            VALUES (?,?,?,?,?,?)
            """,
            batch_fts,
        )
        if batch_aliases:
            con.executemany("INSERT INTO title_aliases VALUES (?,?,?)", batch_aliases)
        batch_meta.clear()
        batch_fts.clear()
        batch_aliases.clear()

    n = 0
    fmt_counts: Counter[str] = Counter()
    read_counts: Counter[str] = Counter()
    rating_counts: Counter[str] = Counter()

    for r, a in _rows(src_path, avail_path):
        if not in_scope(r):
            continue

        sid = int(r["id"])
        cr = r["content_rating"]
        mtype = r["type"]
        rating_counts[cr] += 1

        raw_tags = {t: w for t, w in record_tags(r).items() if t in keep}
        # Store tags ordered by weight priority (core > defining > recurrent > others)
        tag_names = sorted(
            raw_tags,
            key=lambda t: (WEIGHT_PRIORITY.get(raw_tags[t], 9), -counts[t], t),
        )
        tag_weights_slug = {slug(t): raw_tags[t] for t in tag_names}
        tag_text = " ".join(
            (slug(t) + " ") * (2 if raw_tags[t] in ("core", "defining") else 1)
            for t in sorted(tag_names)
        ).strip()

        alts = extract_title_alternates_from_record(r)
        if not alts and sid in alt_map:
            alts = alt_map[sid]
        alt_text = " | ".join(alts)

        # Populate normalized aliases table for fast exact-title lookups
        alias_candidates = [r["title"]] + alts
        seen_alias: set[str] = set()
        for al in alias_candidates:
            na = norm_alias(al)
            if na and na not in seen_alias:
                seen_alias.add(na)
                batch_aliases.append((sid, al, na))

        fmt = format_class(a)
        read = readability(a)
        fmt_counts[fmt] += 1
        read_counts[read] += 1
        readable = 1 if read in ("official_english", "english_online") else 0

        rating = float(r["rating"]) if r.get("rating") is not None else None
        year = int(r["year"]) if r.get("year") else None
        desc = r.get("description") or ""

        batch_meta.append(
            (
                sid,
                r["title"],
                mtype,
                cr,
                r.get("status"),
                year,
                rating,
                rating if rating is not None else median_rating,
                r.get("cover"),
                desc,
                json.dumps(tag_names, ensure_ascii=False),
                json.dumps(tag_weights_slug, ensure_ascii=False),
                r.get("pop_global"),
                fmt,
                read,
                readable,
                a.get("total_chapters"),
            )
        )
        flags = f"fmt_{fmt} read_{'yes' if readable else 'no'} type_{mtype} rating_{cr}"
        batch_fts.append((sid, r["title"], alt_text, tag_text, desc, flags))

        n += 1
        if n % 20_000 == 0:
            flush()
            if verbose:
                print(n)

    flush()
    con.execute("CREATE INDEX idx_title_aliases_norm ON title_aliases(alias_norm)")
    con.execute("CREATE INDEX idx_title_aliases_id ON title_aliases(series_id)")
    con.commit()
    con.close()

    if verbose:
        print("Done:", n, "records ->", db_path)
        print("Content rating:", dict(rating_counts))
        print("Format:", dict(fmt_counts))
        print("Readability:", dict(read_counts))

    return {
        "records": n,
        "taxonomy_tags": len(keep),
        "content_ratings": dict(rating_counts),
        "formats": dict(fmt_counts),
    }


if __name__ == "__main__":
    build_database()
