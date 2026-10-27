import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 - populates Base.metadata with every table
from db import get_db
from models.base import Base
from models.collection import Collection
from models.media import Media
from models.show import Show
from models.users import User, UserSettings
from routers import media as media_router


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _movie_result(tmdb_id, title):
    return {"id": tmdb_id, "title": title, "poster_path": None, "release_date": "2020-01-01",
            "vote_average": 7.0, "genre_ids": []}


class RecommendedEndpointTests(unittest.IsolatedAsyncioTestCase):
    """GET /media/recommended: recommendations seeded from what the person
    collected, leaving out what they already have or dropped."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            s.add(User(id=1, email="a@example.com", username="alice", api_key="k-alice"))
            s.add(Media(id=1, tmdb_id=100, media_type="movie", title="Collected Movie"))
            s.add(Media(id=2, tmdb_id=102, media_type="movie", title="Dropped Movie"))
            s.add(Collection(user_id=1, media_id=1, added_at=datetime(2026, 1, 1)))
            s.add(UserSettings(user_id=1, dropped_movies=[2], dropped_shows=[], next_up_hidden_shows=[]))
            await s.commit()

        app = FastAPI()
        app.include_router(media_router.router, prefix="/media")

        async def _override_get_db():
            async with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = _override_get_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.engine.dispose)

    async def test_requires_authentication(self):
        resp = await self.client.get("/media/recommended")
        self.assertEqual(resp.status_code, 401)

    async def test_no_tmdb_key_is_an_empty_result_not_an_error(self):
        with patch.object(media_router, "get_user_tmdb_key", new=AsyncMock(return_value=None)):
            resp = await self.client.get("/media/recommended", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {"results": []})

    async def test_recommends_from_the_collection_and_leaves_out_collected_and_dropped(self):
        recs = [_movie_result(100, "Already Collected"), _movie_result(102, "Dropped"),
                _movie_result(200, "Fresh One"), _movie_result(201, "Fresh Two")]
        with patch.object(media_router, "get_user_tmdb_key", new=AsyncMock(return_value="tmdb-key")), \
             patch.object(media_router.tmdb, "get_movie_recommendations", new=AsyncMock(return_value={"results": recs})):
            resp = await self.client.get("/media/recommended", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        titles = sorted(r["title"] for r in resp.json()["results"])
        self.assertEqual(titles, ["Fresh One", "Fresh Two"])

    async def test_a_person_with_nothing_collected_gets_nothing(self):
        async with self.Session() as s:
            s.add(User(id=2, email="b@example.com", username="bob", api_key="k-bob"))
            await s.commit()
        with patch.object(media_router, "get_user_tmdb_key", new=AsyncMock(return_value="tmdb-key")):
            resp = await self.client.get("/media/recommended", params={"api_key": "k-bob"})
        self.assertEqual(resp.json(), {"results": []})


class RecentCollectedShowsQueryTests(unittest.IsolatedAsyncioTestCase):
    def test_the_query_is_valid_on_postgresql(self):
        """PostgreSQL rejects SELECT DISTINCT with an ORDER BY on a column that is
        not selected, so the query groups instead."""
        sql = str(media_router._recent_collected_show_ids_stmt(1, 5).compile(dialect=postgresql.dialect()))
        self.assertIn("GROUP BY", sql)
        self.assertNotIn("DISTINCT", sql)

    async def test_newest_collected_show_comes_first_and_each_show_once(self):
        engine = create_async_engine(
            "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as s:
            s.add(User(id=1, email="a@example.com", username="alice", api_key="k"))
            s.add(Show(id=1, tmdb_id=500, title="Old Show"))
            s.add(Show(id=2, tmdb_id=501, title="New Show"))
            for mid, show_id, day in [(10, 1, 1), (11, 1, 2), (12, 2, 5)]:
                s.add(Media(id=mid, media_type="episode", title="E", show_id=show_id, season_number=1, episode_number=mid))
                s.add(Collection(user_id=1, media_id=mid, added_at=datetime(2026, 1, day)))
            await s.commit()
        async with Session() as s:
            rows = (await s.execute(media_router._recent_collected_show_ids_stmt(1, 5))).all()
        await engine.dispose()
        self.assertEqual([r[0] for r in rows], [501, 500])


if __name__ == "__main__":
    unittest.main()
