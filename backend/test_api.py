"""Comprehensive pytest suite for serious_recommender backend."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

try:
    from . import api, build_db, router
    from .cache import rate_limiter, state_store
    from .db import catalog_db
except ImportError:
    import api
    import build_db
    import router
    from cache import rate_limiter, state_store
    from db import catalog_db


def _make_record(
    sid: int,
    title: str,
    mtype: str,
    content_rating: str,
    tags2: list[list],
    description: str,
    pop_global: int,
    rating: float | None = 85.0,
    titles: list[dict] | None = None,
    status: str = "completed",
    year: int = 2020,
    chapters: int = 50,
    licensed: bool = True,
) -> tuple[dict, dict]:
    slim = {
        "id": sid,
        "title": title,
        "type": mtype,
        "content_rating": content_rating,
        "status": status,
        "year": year,
        "rating": rating,
        "cover": f"https://example.com/{sid}.jpg",
        "description": description,
        "tags2": tags2,
        "pop_global": pop_global,
        "merged_with": None,
        "genres": [],
    }
    if titles is not None:
        slim["titles"] = titles
    avail = {
        "id": sid,
        "total_chapters": chapters,
        "final_volume": 5,
        "status": status,
        "oneshot_tag": chapters == 1,
        "n_english_publishers": 1 if licensed else 0,
        "is_licensed": licensed,
        "webplatform_langs": ["en"] if licensed else [],
        "has_en_title": True,
    }
    return slim, avail


@pytest.fixture()
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build an isolated catalog DB, taxonomy, and state DB for each test."""
    slim_path = tmp_path / "series_slim.jsonl"
    avail_path = tmp_path / "series_avail.jsonl"
    raw_json_path = tmp_path / "series.json"
    db_path = tmp_path / "catalog_test.sqlite"
    tax_path = tmp_path / "taxonomy_test.json"
    state_path = tmp_path / "state_test.sqlite"

    records = [
        _make_record(
            2060,
            "Omniscient Reader",
            "manhwa",
            "safe",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["System", "core", 3, "Tropes > System"],
                ["Apocalypse", "defining", 3, "Settings > Apocalypse"],
                ["Smart Male Lead", "defining", 3, "Character > Smart Male Lead"],
                ["Action", "defining", 2, "Genres > Action"],
                ["Survival", "defining", 2, "Themes > Survival"],
                ["Swordplay", "recurrent", 2, "Activities > Swordplay"],
                ["Monsters", "incidental", 1, "Creatures > Monsters"],
            ],
            "Dokja reads an apocalyptic webnovel about survival and dungeons.",
            pop_global=12,
            rating=88.5,
            titles=[
                {"title": "Omniscient Reader"},
                {"title": "Omniscient Reader's Viewpoint"},
                {"title": "ORV"},
            ],
        ),
        _make_record(
            2061,
            "ORV Obscure Anthology",
            "manhwa",
            "safe",
            [
                ["Comedy", "core", 2, "Genres > Comedy"],
                ["Action", "incidental", 2, "Genres > Action"],
            ],
            "A minor parody anthology loosely related to ORV.",
            pop_global=95000,
            rating=62.0,
            titles=[{"title": "ORV"}],
        ),
        _make_record(
            3001,
            "Solo Necromancer",
            "manhwa",
            "safe",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["System", "core", 3, "Tropes > System"],
                ["Action", "defining", 2, "Genres > Action"],
                ["Survival", "defining", 2, "Themes > Survival"],
                ["Smart Male Lead", "defining", 2, "Character > Smart Male Lead"],
            ],
            "A dungeon hunter awakens a survival system inside monsters-filled gates.",
            pop_global=25,
            rating=84.0,
            chapters=120,
            licensed=True,
        ),
        _make_record(
            3002,
            "Tower Climber",
            "manhwa",
            "safe",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["System", "defining", 3, "Tropes > System"],
                ["Action", "defining", 2, "Genres > Action"],
            ],
            "Climbing a lethal dungeon tower with a status system.",
            pop_global=60,
            rating=81.0,
            chapters=1,
            licensed=False,
        ),
        _make_record(
            3003,
            "Tokyo Dungeon Blade",
            "manga",
            "suggestive",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["System", "defining", 3, "Tropes > System"],
                ["Action", "defining", 2, "Genres > Action"],
                ["Swordplay", "defining", 2, "Activities > Swordplay"],
            ],
            "Suggestive Japanese dungeon fantasy manga with swordplay and system.",
            pop_global=40,
            rating=82.5,
            chapters=80,
            licensed=True,
        ),
        _make_record(
            3004,
            "Crimson Velvet Contract",
            "manhwa",
            "erotica",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["Romance", "core", 3, "Genres > Romance"],
                ["Action", "defining", 2, "Genres > Action"],
            ],
            "Mature erotica fantasy manhwa set in cursed dungeons.",
            pop_global=90,
            rating=79.0,
            chapters=45,
            licensed=True,
        ),
        _make_record(
            3005,
            "Forbidden Abyss",
            "manga",
            "pornographic",
            [
                ["Dungeon", "core", 3, "Settings > Dungeon"],
                ["Action", "defining", 2, "Genres > Action"],
            ],
            "Explicit pornographic dungeon series.",
            pop_global=15,
            rating=75.0,
            chapters=20,
            licensed=True,
        ),
        _make_record(
            3006,
            "Wandering Murim Sage",
            "manhua",
            "safe",
            [
                ["Action", "core", 2, "Genres > Action"],
                ["Swordplay", "defining", 2, "Activities > Swordplay"],
                ["Apocalypse", "incidental", 2, "Settings > Apocalypse"],
            ],
            "A Chinese manhua martial artist wandering during chaos.",
            pop_global=120,
            rating=None,
            chapters=8,
            licensed=False,
        ),
    ]

    with open(slim_path, "w", encoding="utf-8") as fs, open(
        avail_path, "w", encoding="utf-8"
    ) as fa:
        for slim, avail in records:
            fs.write(json.dumps(slim) + "\n")
            fa.write(json.dumps(avail) + "\n")

    # Empty raw series.json so titles come cleanly from slim records above
    raw_json_path.write_text("[]", encoding="utf-8")

    # Build catalog DB (minimum tag frequency = 1 for small test fixture)
    build_db.build_database(
        src_path=str(slim_path),
        avail_path=str(avail_path),
        raw_json_path=str(raw_json_path),
        db_path=str(db_path),
        taxonomy_out=str(tax_path),
        min_tag_records=1,
        verbose=False,
    )

    # Also explicitly inject the pornographic record into the built SQLite DB
    # to verify server-side enforcement even if pornographic rows exist in DB.
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        INSERT INTO manga_metadata (
            id, title, type, content_rating, status, year, rating, rating_filled,
            cover, description, tags, tag_weights, pop_global, format, readability,
            readable, total_chapters
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            3005,
            "Forbidden Abyss",
            "manga",
            "pornographic",
            "completed",
            2020,
            75.0,
            75.0,
            None,
            "Explicit pornographic dungeon series.",
            json.dumps(["Dungeon", "Action"]),
            json.dumps({"dungeon": "core", "action": "defining"}),
            15,
            "series",
            "official_english",
            1,
            20.0,
        ),
    )
    con.execute(
        """
        INSERT INTO manga_search(rowid, title, title_alternates, tags, synopsis, flags)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            3005,
            "Forbidden Abyss",
            "",
            "dungeon dungeon action action",
            "Explicit pornographic dungeon series.",
            "fmt_series read_yes type_manga rating_pornographic",
        ),
    )
    con.commit()
    con.close()

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("TAXONOMY_PATH", str(tax_path))
    monkeypatch.setenv("CACHE_DB_PATH", str(state_path))
    monkeypatch.setenv("GEMINI_DAILY_CAP", "100")
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "100")

    catalog_db.set_db_path(str(db_path))
    state_store.set_db_path(str(state_path))
    rate_limiter.reset()

    tax_data, slugs = router.load_taxonomy(str(tax_path))
    monkeypatch.setattr(router, "tax", tax_data)
    monkeypatch.setattr(router, "SLUGS", slugs)
    monkeypatch.setattr(router, "USE_LLM", True)

    client = TestClient(api.app)
    return {
        "client": client,
        "db_path": db_path,
        "state_path": state_path,
        "tax_path": tax_path,
    }


def test_filter_flags_in_built_query(test_env):
    it = {
        "must": ["dungeon"],
        "boost": ["system"],
        "keywords": ["tower"],
        "format": None,
        "readable_only": False,
        "negative_tags": [],
    }
    caps = catalog_db.get_caps()

    q_default = router.build_query(it, loosen=0, filters={}, caps=caps)
    assert 'tags:"dungeon"' in q_default
    assert 'flags:"rating_safe"' in q_default
    assert "rating_suggestive" not in q_default
    assert "rating_erotica" not in q_default
    assert "rating_pornographic" not in q_default

    q_full = router.build_query(
        it,
        loosen=0,
        filters={
            "type": "manhwa",
            "include_suggestive": True,
            "include_mature": True,
            "readable_only": True,
            "format": "oneshot",
        },
        caps=caps,
    )
    assert 'flags:"rating_safe"' in q_full
    assert 'flags:"rating_suggestive"' in q_full
    assert 'flags:"rating_erotica"' in q_full
    assert 'flags:"type_manhwa"' in q_full
    assert 'flags:"fmt_oneshot"' in q_full
    assert 'flags:"read_yes"' in q_full
    assert "rating_pornographic" not in q_full


def test_pornographic_is_never_returned(test_env, monkeypatch: pytest.MonkeyPatch):
    client: TestClient = test_env["client"]

    def fake_llm(_text: str) -> str:
        return json.dumps(
            {
                "must_have_tags": ["dungeon"],
                "boost_tags": ["action", "system"],
                "keywords": ["dungeon"],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": True,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    resp = client.post(
        "/recommend",
        json={
            "text": "dungeon action series",
            "filters": {
                "type": "all",
                "include_suggestive": True,
                "include_mature": True,
                "readable_only": False,
                "format": "any",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    returned_ids = [r["id"] for r in data["results"]]
    returned_ratings = {r["content_rating"] for r in data["results"]}

    assert 3005 not in returned_ids
    assert "pornographic" not in returned_ratings
    # Safe, suggestive, and erotica should all be present when enabled
    assert {"safe", "suggestive", "erotica"}.issubset(returned_ratings)


def test_rating_matrix_independent_and_rating_scale(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]

    def fake_llm(_text: str) -> str:
        return json.dumps(
            {
                "must_have_tags": ["action"],
                "boost_tags": ["dungeon", "system"],
                "keywords": [],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": True,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    expected_by_flags = {
        (False, False): {"safe"},
        (True, False): {"safe", "suggestive"},
        (False, True): {"safe", "erotica"},
        (True, True): {"safe", "suggestive", "erotica"},
    }

    intent_id = None
    for (inc_sug, inc_mat), allowed_set in expected_by_flags.items():
        if intent_id is None:
            resp = client.post(
                "/recommend",
                json={
                    "text": "action dungeon",
                    "filters": {
                        "type": "all",
                        "include_suggestive": inc_sug,
                        "include_mature": inc_mat,
                    },
                    "limit": 20,
                },
            )
            intent_id = resp.json()["intent_id"]
        else:
            resp = client.post(
                "/refilter",
                json={
                    "intent_id": intent_id,
                    "filters": {
                        "type": "all",
                        "include_suggestive": inc_sug,
                        "include_mature": inc_mat,
                    },
                    "limit": 20,
                },
            )

        assert resp.status_code == 200
        results = resp.json()["results"]
        ratings = {r["content_rating"] for r in results}
        assert "pornographic" not in ratings
        if not inc_mat:
            assert "erotica" not in ratings
        if not inc_sug:
            assert "suggestive" not in ratings
        assert ratings == allowed_set

        by_id = {r["id"]: r for r in results}
        # SQLite rating 84.0 must be scaled to 8.4 (0-10)
        assert by_id[3001]["rating"] == 8.4
        # Unrated title (rating=None in DB, filler rating_filled) must return None
        assert by_id[3006]["rating"] is None


def test_cache_hit_costs_zero_gemini_calls_and_failures_not_cached(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]
    call_counter = {"n": 0}
    should_fail = {"flag": True}

    def fake_llm(_text: str) -> str | None:
        call_counter["n"] += 1
        if should_fail["flag"]:
            return None
        return json.dumps(
            {
                "must_have_tags": ["dungeon"],
                "boost_tags": ["system", "invented_fake_tag"],
                "keywords": [],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": False,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    # 1. Failing LLM call -> degraded=True and NOT cached
    r_fail = client.post("/recommend", json={"text": "dungeon system story"})
    assert r_fail.status_code == 200
    assert r_fail.json()["degraded"] is True
    assert call_counter["n"] == 1
    assert state_store.get_cache_size() == 0

    # 2. Succeeding LLM call -> invokes LLM once, drops unknown tags, populates cache
    should_fail["flag"] = False
    r_ok = client.post("/recommend", json={"text": "dungeon system story"})
    assert r_ok.status_code == 200
    body_ok = r_ok.json()
    assert body_ok["degraded"] is False
    assert "invented_fake_tag" not in body_ok["intent"]["boosts"]
    assert call_counter["n"] == 2
    assert state_store.get_cache_size() == 1

    # 3. Same normalized query with different filter -> cache hit costs 0 Gemini calls
    r_cached = client.post(
        "/recommend",
        json={
            "text": "Dungeon, system story!",
            "filters": {"type": "manga", "include_suggestive": True},
        },
    )
    assert r_cached.status_code == 200
    assert r_cached.json()["degraded"] is False
    assert call_counter["n"] == 2
    assert r_cached.json()["latency_ms"]["llm"] == 0


def test_refilter_makes_no_llm_call_and_supports_offset(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]
    calls = {"n": 0}

    def fake_llm(_text: str) -> str:
        calls["n"] += 1
        return json.dumps(
            {
                "must_have_tags": ["dungeon"],
                "boost_tags": ["system", "action"],
                "keywords": [],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": False,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    r1 = client.post(
        "/recommend",
        json={"text": "dungeon stories", "filters": {"type": "all"}, "limit": 1, "offset": 0},
    )
    assert r1.status_code == 200
    assert calls["n"] == 1
    d1 = r1.json()
    assert len(d1["results"]) == 1
    intent_id = d1["intent_id"]

    # Refilter to manga + suggestive with 0 LLM calls
    r2 = client.post(
        "/refilter",
        json={
            "intent_id": intent_id,
            "filters": {"type": "manga", "include_suggestive": True},
        },
    )
    assert r2.status_code == 200
    assert calls["n"] == 1
    d2 = r2.json()
    assert d2["latency_ms"]["llm"] == 0
    assert [x["id"] for x in d2["results"]] == [3003]

    # Refilter pagination offset=1 on safe results skips first item cleanly
    r3 = client.post(
        "/refilter",
        json={"intent_id": intent_id, "filters": {"type": "manhwa"}, "limit": 2, "offset": 1},
    )
    assert r3.status_code == 200
    assert calls["n"] == 1
    d3 = r3.json()
    assert d3["results"][0]["id"] != d1["results"][0]["id"]

    # Unknown intent_id -> clean 404 JSON error
    r4 = client.post("/refilter", json={"intent_id": "nonexistent_id"})
    assert r4.status_code == 404
    assert r4.json()["error"]["code"] == "INTENT_NOT_FOUND"


def test_seed_titles_orv_excluded_boosted_not_found_and_skipped(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]

    def fake_llm(_text: str) -> str:
        return json.dumps(
            {
                "must_have_tags": [],
                "boost_tags": [],
                "keywords": ["dungeon"],
                "similar_to": ["ORV"],
                "format": "none",
                "readable_only": False,
                "mature_intent": False,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    resp = client.post(
        "/recommend",
        json={
            "text": "something like ORV",
            "seed_titles": ["Missing Title XYZ", "Crimson Velvet Contract", "Forbidden Abyss"],
            "filters": {"type": "all", "include_suggestive": False, "include_mature": False},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    returned_ids = [r["id"] for r in data["results"]]

    # ORV (id=2060) resolved via alternate title + lowest pop_global (12 vs 95000),
    # excluded from results, and added top 5-8 core/defining tags into boosts
    assert 2060 not in returned_ids
    assert 3001 in returned_ids
    assert 5 <= len(data["intent"]["boosts"]) <= 8
    assert "dungeon" in data["intent"]["boosts"]
    assert "system" in data["intent"]["boosts"]

    # Missing seed reported cleanly
    assert data["not_found_titles"] == ["Missing Title XYZ"]
    # Out-of-scope erotica and pornographic seeds skipped without revealing their rating
    assert set(data["skipped_titles"]) == {"Crimson Velvet Contract", "Forbidden Abyss"}
    for skipped_msg in data["skipped_titles"]:
        assert "erotica" not in skipped_msg.lower()
        assert "pornographic" not in skipped_msg.lower()

    # Refilter enabling mature scope uses Crimson Velvet Contract (not skipped),
    # while Forbidden Abyss (pornographic) remains skipped and never returned
    r_mature = client.post(
        "/refilter",
        json={
            "intent_id": data["intent_id"],
            "filters": {"type": "all", "include_mature": True},
        },
    )
    assert r_mature.status_code == 200
    d_mature = r_mature.json()
    assert "Crimson Velvet Contract" not in d_mature["skipped_titles"]
    assert "Forbidden Abyss" in d_mature["skipped_titles"]
    assert 3004 not in [r["id"] for r in d_mature["results"]]  # seed excluded from own results
    assert 3005 not in [r["id"] for r in d_mature["results"]]


def test_seed_only_blank_text_succeeds_without_llm_call(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]
    calls = {"n": 0}

    def fake_llm(_text: str) -> str:
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(router, "call_llm", fake_llm)

    resp = client.post(
        "/recommend",
        json={"text": "", "seed_titles": ["ORV"], "filters": {"type": "manhwa"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert calls["n"] == 0
    assert data["degraded"] is False
    assert data["latency_ms"]["llm"] == 0
    assert 2060 not in [r["id"] for r in data["results"]]
    assert 3001 in [r["id"] for r in data["results"]]

    bad = client.post("/recommend", json={"text": "   "})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


def test_daily_gemini_cap_degrades_gracefully(test_env, monkeypatch: pytest.MonkeyPatch):
    client: TestClient = test_env["client"]
    monkeypatch.setenv("GEMINI_DAILY_CAP", "1")
    calls = {"n": 0}

    def fake_llm(_text: str) -> str:
        calls["n"] += 1
        return json.dumps(
            {
                "must_have_tags": ["dungeon"],
                "boost_tags": ["action"],
                "keywords": [],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": False,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)

    r1 = client.post("/recommend", json={"text": "first dungeon query"})
    assert r1.status_code == 200
    assert r1.json()["degraded"] is False
    assert calls["n"] == 1

    # Second uncached query exceeds GEMINI_DAILY_CAP=1 -> uses lexical_intent, degraded=True
    r2 = client.post("/recommend", json={"text": "second unique dungeon apocalypse query"})
    assert r2.status_code == 200
    assert r2.json()["degraded"] is True
    assert calls["n"] == 1
    assert state_store.get_cache_size() == 1


def test_feedback_rate_limit_health_and_key_priority(
    test_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client: TestClient = test_env["client"]

    # Check GEMINI_API_KEY env priority over local gemini_key.txt fallback
    monkeypatch.setenv("GEMINI_API_KEY", "env-test-secret-do-not-log")
    assert router._load_key() == "env-test-secret-do-not-log"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    local_key = tmp_path / "local_key.txt"
    local_key.write_text("first-key\nfallback-local-key\n", encoding="utf-8")
    monkeypatch.setattr(router, "BASE_DIR", tmp_path)
    monkeypatch.setattr(router, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(router, "KEY_FILE", "local_key.txt")
    assert router._load_key() == "fallback-local-key"

    # Feedback valid + invalid validation format
    fb_ok = client.post(
        "/feedback",
        json={"intent_id": "abc123", "series_id": 3001, "vote": 1, "note": "Great match"},
    )
    assert fb_ok.status_code == 200
    assert fb_ok.json()["ok"] is True
    assert len(state_store.list_feedback("abc123")) == 1

    fb_bad = client.post(
        "/feedback",
        json={"intent_id": "abc123", "series_id": 3001, "vote": 5, "note": "x" * 201},
    )
    assert fb_bad.status_code == 422
    assert fb_bad.json()["error"]["code"] == "VALIDATION_ERROR"

    # Health endpoint
    health = client.get("/health")
    assert health.status_code == 200
    h_json = health.json()
    assert h_json["ok"] is True
    assert h_json["db_records"] == 7  # 8 raw minus 1 pornographic
    assert h_json["taxonomy_tags"] > 0
    assert h_json["cache_size"] == 0

    # Rate limit enforcement
    monkeypatch.setenv("RATE_LIMIT_PER_MIN", "2")
    rate_limiter.reset()
    assert client.post("/feedback", json={"intent_id": "i", "series_id": 1, "vote": 1}).status_code == 200
    assert client.post("/feedback", json={"intent_id": "i", "series_id": 1, "vote": -1}).status_code == 200
    r_limited = client.post("/feedback", json={"intent_id": "i", "series_id": 1, "vote": 1})
    assert r_limited.status_code == 429
    assert r_limited.json()["error"]["code"] == "RATE_LIMITED"


def test_model_load_balancer_and_failover(monkeypatch: pytest.MonkeyPatch):
    balancer = router.ModelLoadBalancer()
    assert router.MODELS == [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-4-hydrogen",
    ]

    # 1. Load balancing keeps traffic within Tier 0 (500-RPD flash-lite) before Tier 1
    first = balancer.select_order()[0]
    assert first in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    balancer.record_dispatch(first)
    second = balancer.select_order()[0]
    assert second in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
    assert second != first

    # 2. Error cooldown (503 / 429) routes subsequent requests away from unhealthy model
    balancer.record_error("gemini-3.5-flash-lite", RuntimeError("503 UNAVAILABLE"))
    order = balancer.select_order()
    assert order[0] == "gemini-3.1-flash-lite"
    assert order[-1] == "gemini-3.5-flash-lite"

    # 3. If both primary models are cooling down, spill over to healthy Tier 1 backups
    balancer.record_error("gemini-3.1-flash-lite", RuntimeError("429 RESOURCE_EXHAUSTED"))
    order2 = balancer.select_order()
    assert order2[0] in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen")
    assert set(order2[-2:]) == {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}

    # 4. call_llm records dispatch + error cooldown and fails over immediately
    router.model_balancer.reset()
    attempted: list[str] = []

    def fake_call_gemini(model: str, _user_text: str) -> str:
        attempted.append(model)
        if len(attempted) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return '{"must_have_tags": ["action"], "boost_tags": [], "keywords": [], "similar_to": [], "format": "none", "readable_only": false, "mature_intent": false, "negative_tags": [], "already_read_titles": []}'

    monkeypatch.setattr(router, "_call_gemini", fake_call_gemini)
    res = router.call_llm("action story")
    assert res is not None
    assert len(attempted) == 2
    assert attempted[0] != attempted[1]

    # Next call skips the failed model immediately
    next_order = router.model_balancer.select_order()
    assert next_order[0] == attempted[1]
    assert next_order[-1] == attempted[0]
    router.model_balancer.reset()


def test_default_limit_is_13(test_env, monkeypatch: pytest.MonkeyPatch):
    client: TestClient = test_env["client"]
    con = sqlite3.connect(str(test_env["db_path"]))
    # Seed enough safe records (20 total) so default limit=13 can be observed directly
    for i in range(4001, 4016):
        con.execute(
            """
            INSERT INTO manga_metadata (
                id, title, type, content_rating, status, year, rating, rating_filled,
                cover, description, tags, tag_weights, pop_global, format, readability,
                readable, total_chapters
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                i,
                f"Dungeon Series {i}",
                "manhwa",
                "safe",
                "completed",
                2022,
                80.0,
                80.0,
                None,
                "Action dungeon series.",
                json.dumps(["Dungeon", "Action"]),
                json.dumps({"dungeon": "core", "action": "defining"}),
                200 + i,
                "series",
                "official_english",
                1,
                50.0,
            ),
        )
        con.execute(
            """
            INSERT INTO manga_search(rowid, title, title_alternates, tags, synopsis, flags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                i,
                f"Dungeon Series {i}",
                "",
                "dungeon dungeon action action",
                "Action dungeon series.",
                "fmt_series read_yes type_manhwa rating_safe",
            ),
        )
    con.commit()
    con.close()

    def fake_llm(_text: str) -> str:
        return json.dumps(
            {
                "must_have_tags": ["dungeon"],
                "boost_tags": ["action"],
                "keywords": [],
                "similar_to": [],
                "format": "none",
                "readable_only": False,
                "mature_intent": False,
                "negative_tags": [],
                "already_read_titles": [],
            }
        )

    monkeypatch.setattr(router, "call_llm", fake_llm)
    r_rec = client.post("/recommend", json={"text": "action dungeon"})
    assert r_rec.status_code == 200
    assert len(r_rec.json()["results"]) == 13

    r_ref = client.post("/refilter", json={"intent_id": r_rec.json()["intent_id"]})
    assert r_ref.status_code == 200
    assert len(r_ref.json()["results"]) == 13
