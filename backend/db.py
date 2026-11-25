"""SQLite database access, FTS5 filter query helpers, seed resolution, and catalog inspection."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

SEED_WEIGHT_SCORES = {
    "core": 3,
    "defining": 2,
    "recurrent": 1,
    "unweighted": 0,
    "incidental": 0,
}

# Overly broad metadata/demographic tags that hurt FTS OR specificity when copied from seeds
GENERIC_SEED_TAGS = {
    "male_lead",
    "female_lead",
    "based_on_a_novel",
    "based_on_a_web_novel",
    "based_on_a_light_novel",
    "adapted_to_anime",
    "adapted_to_drama_cd",
    "full_color",
    "web_comic",
    "21st_century",
    "south_korea",
    "japan",
    "china",
    "primarily_adult_cast",
    "foreign",
}


def default_db_path() -> str:
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return env_path
    local_db = BASE_DIR / "catalog_v1.sqlite"
    if local_db.exists():
        return str(local_db)
    return str(ROOT_DIR / "catalog_v1.sqlite")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def norm_title(s: str | None) -> str:
    folded = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


@dataclass(frozen=True)
class SchemaCaps:
    has_content_rating: bool
    has_tag_weights: bool
    has_title_alternates: bool
    has_title_aliases: bool
    has_rating_flags: bool
    has_type_flags: bool


class CatalogDB:
    """Thread-safe reader for catalog_v1.sqlite with backward-compatible schema detection."""

    def __init__(self, db_path: str | None = None) -> None:
        self._explicit_db_path = db_path
        self._local = threading.local()
        self._caps_lock = threading.Lock()
        self._caps_cache: dict[str, SchemaCaps] = {}

    @property
    def db_path(self) -> str:
        return os.environ.get("DB_PATH") or self._explicit_db_path or default_db_path()

    def set_db_path(self, db_path: str) -> None:
        self._explicit_db_path = db_path
        with self._caps_lock:
            self._caps_cache.pop(db_path, None)
        conns = getattr(self._local, "conns", None)
        if conns and db_path in conns:
            try:
                conns.pop(db_path).close()
            except Exception:
                pass

    def get_connection(self) -> sqlite3.Connection:
        path = self.db_path
        conns = getattr(self._local, "conns", None)
        if conns is None:
            conns = {}
            self._local.conns = conns
        con = conns.get(path)
        if con is not None:
            try:
                con.execute("SELECT 1")
                return con
            except sqlite3.Error:
                try:
                    con.close()
                except Exception:
                    pass
                conns.pop(path, None)
        con = sqlite3.connect(path, timeout=15.0)
        conns[path] = con
        return con

    def get_caps(self) -> SchemaCaps:
        path = self.db_path
        with self._caps_lock:
            cached = self._caps_cache.get(path)
            if cached is not None:
                return cached

        con = self.get_connection()
        meta_cols = {
            r[1] for r in con.execute("PRAGMA table_info(manga_metadata)").fetchall()
        }
        fts_cols = {
            r[1] for r in con.execute("PRAGMA table_info(manga_search)").fetchall()
        }
        has_aliases = (
            con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='title_aliases'"
            ).fetchone()
            is not None
        )
        sample_flag = con.execute("SELECT flags FROM manga_search LIMIT 1").fetchone()
        flag_text = sample_flag[0] if sample_flag and sample_flag[0] else ""
        caps = SchemaCaps(
            has_content_rating="content_rating" in meta_cols,
            has_tag_weights="tag_weights" in meta_cols,
            has_title_alternates="title_alternates" in fts_cols,
            has_title_aliases=has_aliases,
            has_rating_flags=("rating_" in flag_text),
            has_type_flags=("type_" in flag_text),
        )
        with self._caps_lock:
            self._caps_cache[path] = caps
        return caps

    @staticmethod
    def allowed_ratings(filters: dict[str, Any] | None = None) -> list[str]:
        f = filters or {}
        allowed = ["safe"]
        if f.get("include_suggestive"):
            allowed.append("suggestive")
        if f.get("include_mature"):
            allowed.append("erotica")
        # Server-side invariant: never allow "pornographic" under any filter combination.
        return allowed

    @staticmethod
    def _fts_tokens(text: str) -> list[str]:
        return [tok for tok in re.findall(r"\w+", unicodedata.normalize("NFKC", text)) if tok]

    def resolve_seed_titles(
        self,
        names: list[str],
        allowed_ratings: list[str],
        taxonomy_slugs: set[str],
        max_tags_per_seed: int = 8,
        taxonomy_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Resolve seed titles via FTS5 title/alternate-title search.

        Picks lowest pop_global (highest popularity) when multiple candidates match.
        Extracts top 5-8 core/defining weighted tags if inside allowed_ratings.
        """
        con = self.get_connection()
        caps = self.get_caps()

        seen_names: set[str] = set()
        exclude_ids: set[int] = set()
        boost_tags: list[str] = []
        not_found_titles: list[str] = []
        skipped_titles: list[str] = []

        cr_select = "m.content_rating" if caps.has_content_rating else "'safe'"
        tw_select = "m.tag_weights" if caps.has_tag_weights else "NULL"
        alt_select = "s.title_alternates" if caps.has_title_alternates else "''"

        for raw in names:
            clean_name = (raw or "").strip()
            if not clean_name:
                continue
            dedup_key = clean_name.lower()
            if dedup_key in seen_names:
                continue
            seen_names.add(dedup_key)

            toks = self._fts_tokens(clean_name)
            if not toks:
                not_found_titles.append(clean_name)
                continue

            phrase = " ".join(toks)
            queries: list[str] = []
            if caps.has_title_alternates:
                queries.append(f'(title:"{phrase}" OR title_alternates:"{phrase}")')
                if len(toks) > 1:
                    and_expr = " AND ".join(f'"{t}"' for t in toks)
                    queries.append(f"(title:({and_expr}) OR title_alternates:({and_expr}))")
            else:
                queries.append(f'title:"{phrase}"')
                if len(toks) > 1:
                    and_expr = " AND ".join(f'"{t}"' for t in toks)
                    queries.append(f"title:({and_expr})")

            rows: list[tuple[Any, ...]] = []
            for fq in queries:
                try:
                    rows = con.execute(
                        f"""
                        SELECT m.id, m.title, {cr_select}, m.pop_global,
                               m.tags, {tw_select}, s.tags, {alt_select}
                        FROM manga_search s
                        CROSS JOIN manga_metadata m ON m.id = s.rowid
                        WHERE manga_search MATCH ?
                        ORDER BY COALESCE(m.pop_global, 9999999) ASC, m.id ASC
                        LIMIT 25
                        """,
                        (fq,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    break

            if not rows and caps.has_title_aliases:
                n_alias = norm_title(clean_name)
                if n_alias:
                    rows = con.execute(
                        f"""
                        SELECT m.id, m.title, {cr_select}, m.pop_global,
                               m.tags, {tw_select}, s.tags, {alt_select}
                        FROM title_aliases a
                        JOIN manga_metadata m ON m.id = a.series_id
                        JOIN manga_search s ON s.rowid = m.id
                        WHERE a.alias_norm = ?
                        ORDER BY COALESCE(m.pop_global, 9999999) ASC, m.id ASC
                        LIMIT 25
                        """,
                        (n_alias,),
                    ).fetchall()

            if not rows:
                not_found_titles.append(clean_name)
                continue

            # Prefer exact normalized match against primary title or alternates,
            # breaking ties by highest popularity (lowest pop_global rank).
            target_norm = norm_title(clean_name)

            def match_rank(row: tuple[Any, ...]) -> tuple[int, int, int]:
                sid, m_title, _cr, pop, _t, _tw, _stags, m_alts = row
                exact = 1
                if target_norm:
                    if norm_title(m_title) == target_norm:
                        exact = 0
                    elif m_alts:
                        for part in str(m_alts).split("|"):
                            if norm_title(part) == target_norm:
                                exact = 0
                                break
                return (exact, int(pop) if pop is not None else 9_999_999, int(sid))

            best = min(rows, key=match_rank)
            seed_id = int(best[0])
            seed_rating = (best[2] or "safe").lower()
            exclude_ids.add(seed_id)

            # Enforce allowed rating scope without revealing rating in response.
            if seed_rating == "pornographic" or seed_rating not in allowed_ratings:
                skipped_titles.append(clean_name)
                continue

            extracted = self._extract_seed_tags(
                tags_json=best[4],
                tag_weights_json=best[5],
                fts_tags_text=best[6],
                taxonomy_slugs=taxonomy_slugs,
                max_tags=max_tags_per_seed,
                taxonomy_counts=taxonomy_counts,
            )
            for tag_slug in extracted:
                if tag_slug not in boost_tags:
                    boost_tags.append(tag_slug)

        return {
            "exclude_ids": exclude_ids,
            "boost_tags": boost_tags,
            "not_found_titles": not_found_titles,
            "skipped_titles": skipped_titles,
        }

    @staticmethod
    def _extract_seed_tags(
        tags_json: str | None,
        tag_weights_json: str | None,
        fts_tags_text: str | None,
        taxonomy_slugs: set[str],
        max_tags: int = 8,
        taxonomy_counts: dict[str, int] | None = None,
    ) -> list[str]:
        """Extract up to 5-8 top core/defining tags valid in taxonomy, preferring trope specificity."""
        if not tags_json or max_tags <= 0:
            return []
        try:
            display_tags = json.loads(tags_json)
        except (json.JSONDecodeError, TypeError):
            return []

        weights_map: dict[str, int] = {}
        if tag_weights_json:
            try:
                raw_w = json.loads(tag_weights_json)
                if isinstance(raw_w, dict):
                    weights_map = {
                        k: SEED_WEIGHT_SCORES.get(str(v).lower(), 0)
                        for k, v in raw_w.items()
                    }
            except (json.JSONDecodeError, TypeError):
                weights_map = {}
        elif fts_tags_text:
            # Fallback: build_db repeats core/defining slugs twice in manga_search.tags
            tokens = fts_tags_text.split()
            counts: dict[str, int] = {}
            for tok in tokens:
                counts[tok] = counts.get(tok, 0) + 1
            weights_map = {k: (2 if c >= 2 else 1) for k, c in counts.items()}

        counts_map = taxonomy_counts or {}
        large_catalog = bool(counts_map) and max(counts_map.values(), default=0) >= 500

        scored: list[tuple[int, int, int, int, str]] = []
        seen: set[str] = set()
        for idx, name in enumerate(display_tags):
            s = slugify(str(name))
            if not s or s not in taxonomy_slugs or s in seen:
                continue
            seen.add(s)
            w_score = weights_map.get(s, 0)
            is_generic = 1 if s in GENERIC_SEED_TAGS else 0
            corp_count = counts_map.get(s, idx)
            # On large catalogs, prefer mid-to-specific tropes (>=280 records) ahead of hyper-rare <280 noise
            ultra_rare = 1 if (large_catalog and corp_count < 280) else 0
            scored.append((is_generic, -w_score, ultra_rare, corp_count, s))

        scored.sort()
        return [s for _gen, _neg_w, _rare, _cnt, s in scored[:max_tags]]

    def _sql_filter_clause(
        self,
        filters: dict[str, Any] | None,
        intent: dict[str, Any],
        exclude_ids: set[int] | None,
    ) -> tuple[str, list[Any]]:
        """Build defense-in-depth SQL WHERE predicates enforcing ratings, type, readable, format, exclusions."""
        caps = self.get_caps()
        f = filters or {}
        clauses: list[str] = []
        params: list[Any] = []

        allowed = self.allowed_ratings(f)
        if caps.has_content_rating:
            placeholders = ",".join("?" for _ in allowed)
            clauses.append(f"COALESCE(m.content_rating, 'safe') IN ({placeholders})")
            params.extend(allowed)
            clauses.append("COALESCE(m.content_rating, 'safe') != 'pornographic'")
        elif "erotica" not in allowed:
            clauses.append("COALESCE(m.tags, '[]') NOT LIKE '%\"Erotica\"%'")

        ftype = f.get("type", "all")
        if ftype and ftype != "all":
            clauses.append("m.type = ?")
            params.append(ftype)
        else:
            clauses.append("m.type IN ('manga', 'manhwa', 'manhua')")

        ffmt = f.get("format", "any")
        eff_fmt = ffmt if (ffmt and ffmt != "any") else intent.get("format")
        if eff_fmt and eff_fmt != "none":
            clauses.append("m.format = ?")
            params.append(eff_fmt)

        if f.get("readable_only") or intent.get("readable_only"):
            clauses.append("m.readable = 1")

        if exclude_ids:
            excl_list = sorted(exclude_ids)
            placeholders = ",".join("?" for _ in excl_list)
            clauses.append(f"m.id NOT IN ({placeholders})")
            params.extend(excl_list)

        return (" AND ".join(clauses), params)

    def fetch_candidates(
        self,
        fts_query: str,
        filters: dict[str, Any] | None = None,
        intent: dict[str, Any] | None = None,
        limit: int = 200,
        synopsis_weight: float = 6.0,
        rating_weight: float = 1.0,
        exclude_ids: set[int] | None = None,
    ) -> tuple[list[tuple[Any, ...]], int]:
        """Execute FTS5 search with server-side filters and return (rows, total_matching)."""
        con = self.get_connection()
        caps = self.get_caps()
        it = intent or {}

        sql_where, sql_params = self._sql_filter_clause(filters, it, exclude_ids)
        cr_select = "COALESCE(m.content_rating, 'safe')" if caps.has_content_rating else "'safe'"

        if caps.has_title_alternates:
            bm25_expr = "bm25(manga_search, 1.0, 0.0, 10.0, ?, 0.0)"
        else:
            bm25_expr = "bm25(manga_search, 1.0, 10.0, ?, 0.0)"

        count_row = con.execute(
            f"""
            SELECT COUNT(*)
            FROM manga_search s
            CROSS JOIN manga_metadata m ON m.id = s.rowid
            WHERE manga_search MATCH ? AND {sql_where}
            """,
            [fts_query, *sql_params],
        ).fetchone()
        total = int(count_row[0]) if count_row else 0

        if total == 0:
            return [], 0

        rows = con.execute(
            f"""
            SELECT m.id, m.title, m.type, m.status, m.year, m.format, m.readability,
                   m.rating_filled, m.pop_global, m.tags, m.description,
                   {bm25_expr} AS score,
                   m.rating, m.cover, m.readable, {cr_select}
            FROM manga_search s
            CROSS JOIN manga_metadata m ON m.id = s.rowid
            WHERE manga_search MATCH ? AND {sql_where}
            ORDER BY score - (m.rating_filled / 100.0) * ?
            LIMIT ?
            """,
            [synopsis_weight, fts_query, *sql_params, rating_weight, limit],
        ).fetchall()

        return rows, total

    def get_record_count(self) -> int:
        con = self.get_connection()
        caps = self.get_caps()
        if caps.has_content_rating:
            row = con.execute(
                "SELECT COUNT(*) FROM manga_metadata WHERE COALESCE(content_rating, 'safe') != 'pornographic'"
            ).fetchone()
        else:
            row = con.execute("SELECT COUNT(*) FROM manga_metadata").fetchone()
        return int(row[0]) if row else 0


catalog_db = CatalogDB()
