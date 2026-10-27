import os
import unittest
from datetime import datetime

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
from models.events import WatchEvent
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


class WatchedIdsTests(_Base):
    """GET /history/watched-ids: the TMDB ids of watched movies, fully watched
    shows and dropped titles, so a client can hide them from discovery rows."""

    async def seed(self):
        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            s.add(User(id=2, email="b@b.c", username="bob", api_key="k-bob"))
            s.add(Media(id=1, tmdb_id=100, media_type="movie", title="Watched Movie"))
            s.add(Media(id=2, tmdb_id=101, media_type="movie", title="Half-watched Movie"))
            s.add(Media(id=3, tmdb_id=102, media_type="movie", title="Dropped Movie"))

            s.add(_two_season_show(1, 500, "Finished Show"))
            self._episodes(s, 1, 10, 1, 2)
            self._episodes(s, 1, 12, 2, 2)
            s.add(_two_season_show(2, 501, "In-progress Show"))
            self._episodes(s, 2, 20, 1, 2)
            self._episodes(s, 2, 22, 2, 2)
            s.add(_two_season_show(3, 502, "Dropped Show"))

            s.add(WatchEvent(user_id=1, media_id=1, completed=True, watched_at=datetime(2026, 1, 1)))
            s.add(WatchEvent(user_id=1, media_id=2, completed=False, progress_percent=40.0))
            # Finished Show: every aired episode (season 1 twice - a rewatch
            # must not over-count).
            for media_id in (10, 10, 11, 12, 13):
                s.add(WatchEvent(user_id=1, media_id=media_id, completed=True))
            # In-progress Show: season 1 only.
            for media_id in (20, 21):
                s.add(WatchEvent(user_id=1, media_id=media_id, completed=True))
            # Someone else's watch never leaks in.
            s.add(WatchEvent(user_id=2, media_id=22, completed=True))

            s.add(UserSettings(user_id=1, dropped_movies=[3], dropped_shows=[3], next_up_hidden_shows=[]))
            await s.commit()

    async def test_returns_watched_movies_fully_watched_shows_and_dropped(self):
        resp = await self.client.get("/history/watched-ids")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), {
            "movies": [100],
            "shows": [500],
            "dropped_movies": [102],
            "dropped_shows": [502],
        })
