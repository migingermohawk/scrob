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
from models.base import Base, PrivacyLevel
from models.events import WatchEvent
from models.follows import Follow
from models.media import Media
from models.profile import UserProfileData
from models.show import Show
from models.users import User
from routers import history as history_router
from routers import media as media_router


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
        app.include_router(media_router.router, prefix="/media")

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


class WatchersTests(_Base):
    """GET /media/{type}/{tmdb_id}/watchers: people the caller follows who
    watched the title, honouring profile privacy."""

    async def seed(self):
        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            users = {
                2: ("bob", PrivacyLevel.public, "avatar.png"),
                3: ("carol", PrivacyLevel.friends_only, None),  # follows alice back
                4: ("dave", PrivacyLevel.friends_only, None),   # does not
                5: ("erin", PrivacyLevel.private, None),
                6: ("frank", PrivacyLevel.public, None),        # not followed
            }
            for uid, (name, privacy, avatar) in users.items():
                s.add(User(id=uid, email=f"{name}@b.c", username=name, api_key=f"k-{name}"))
                s.add(UserProfileData(user_id=uid, privacy_level=privacy, avatar_path=avatar,
                                      display_name="Bobby" if name == "bob" else None))
            for uid in (2, 3, 4, 5):
                s.add(Follow(follower_id=1, following_id=uid))
            s.add(Follow(follower_id=3, following_id=1))

            s.add(Media(id=1, tmdb_id=100, media_type="movie", title="Movie"))
            for uid, day in ((2, 1), (3, 5), (4, 2), (5, 3), (6, 4)):
                s.add(WatchEvent(user_id=uid, media_id=1, completed=True, watched_at=datetime(2026, 10, day)))
            # Bob also has an unfinished second play - doesn't move his date.
            s.add(WatchEvent(user_id=2, media_id=1, completed=False, watched_at=datetime(2026, 10, 9)))

            s.add(_two_season_show(1, 500, "Show"))
            self._episodes(s, 1, 10, 1, 2)
            s.add(WatchEvent(user_id=2, media_id=11, completed=True, watched_at=datetime(2026, 9, 1)))
            await s.commit()

    async def test_movie_watchers_respect_privacy_newest_first(self):
        resp = await self.client.get("/media/movie/100/watchers")
        self.assertEqual(resp.status_code, 200, resp.text)
        watchers = resp.json()["watchers"]
        self.assertEqual([w["username"] for w in watchers], ["carol", "bob"])
        bob = watchers[1]
        self.assertEqual(bob["display_name"], "Bobby")
        self.assertEqual(bob["avatar_url"], "/profile/avatar/2")
        self.assertTrue(bob["last_watched_at"].startswith("2026-10-01"))
        self.assertIsNone(watchers[0]["avatar_url"])

    async def test_series_counts_any_watched_episode(self):
        resp = await self.client.get("/media/series/500/watchers")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([w["username"] for w in resp.json()["watchers"]], ["bob"])

    async def test_nobody_watched_is_empty(self):
        resp = await self.client.get("/media/movie/999/watchers")
        self.assertEqual(resp.json(), {"watchers": []})

    async def test_episode_type_is_rejected(self):
        resp = await self.client.get("/media/episode/100/watchers")
        self.assertEqual(resp.status_code, 400, resp.text)
