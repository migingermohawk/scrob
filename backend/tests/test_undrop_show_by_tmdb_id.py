import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 - populates Base.metadata with every table
from db import get_db
from dependencies import get_current_user_or_api_key
from models.base import Base
from models.media import Media
from models.show import Show
from models.users import User, UserSettings
from routers import history as history_router


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _two_season_show(id_, tmdb_id, title):
    # Season 0 (specials) is ignored; seasons 1 and 2 have 2 episodes each.
    return Show(
        id=id_, tmdb_id=tmdb_id, title=title,
        tmdb_data={"seasons": [
            {"season_number": 0, "episode_count": 5},
            {"season_number": 1, "episode_count": 2},
            {"season_number": 2, "episode_count": 2},
        ]},
    )


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = _make_engine()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        await self.seed()

        app = FastAPI()
        app.include_router(history_router.router, prefix="/history")

        async def _override_get_db():
            async with self.Session() as session:
                yield session

        async def _current_user():
            async with self.Session() as session:
                return (await session.execute(select(User).where(User.id == 1))).scalar_one()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user_or_api_key] = _current_user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.engine.dispose)

    async def seed(self):
        raise NotImplementedError

    def _episodes(self, s, show_id, first_id, season, count):
        for i in range(count):
            s.add(Media(
                id=first_id + i, media_type="episode", title=f"S{season}E{i + 1}",
                show_id=show_id, season_number=season, episode_number=i + 1,
            ))


class DropShowByTmdbIdTests(_Base):
    """POST and DELETE /history/drop/show accept tmdb_id, so a title opened
    straight from TMDB can be dropped and undropped before scrob has it."""

    async def seed(self):
        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            s.add(_two_season_show(7, 700, "Known Show"))
            s.add(UserSettings(user_id=1, dropped_shows=[], dropped_movies=[], next_up_hidden_shows=[]))
            await s.commit()

    async def _dropped(self):
        async with self.Session() as s:
            return (await s.execute(select(UserSettings).where(UserSettings.user_id == 1))).scalar_one().dropped_shows

    async def test_drop_and_undrop_by_tmdb_id(self):
        resp = await self.client.post("/history/drop/show", json={"tmdb_id": 700})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(await self._dropped(), [7])

        resp = await self.client.delete("/history/drop/show", params={"tmdb_id": 700})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(await self._dropped(), [])

    async def test_show_id_still_works(self):
        resp = await self.client.post("/history/drop/show", json={"show_id": 7})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(await self._dropped(), [7])

    async def test_neither_id_is_a_400(self):
        resp = await self.client.post("/history/drop/show", json={})
        self.assertEqual(resp.status_code, 400, resp.text)

    async def test_undrop_unknown_tmdb_id_is_a_no_op(self):
        resp = await self.client.delete("/history/drop/show", params={"tmdb_id": 999})
        self.assertEqual(resp.status_code, 200, resp.text)
