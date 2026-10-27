import os
import unittest
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import models  # noqa: F401 - populates Base.metadata with every table
from db import get_db
from dependencies import get_optional_user_or_api_key
from models.base import Base
from models.comments import Comment
from models.media import Media
from models.ratings import Rating
from models.users import User
from routers import profile as profile_router


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class RecentCommentsRatingTests(unittest.IsolatedAsyncioTestCase):
    """GET /profile/{user_id} attaches the person's own rating to each of their
    5 most recent comments. Movie, series and season comments resolve through
    the Media and Rating tables; episode comments are left unrated (a comment
    stores the show's tmdb_id for those, so there is nothing to look up)."""

    async def asyncSetUp(self):
        self.engine = _make_engine()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            alice = User(id=1, email="a@b.c", username="alice", api_key="k-alice")
            s.add(alice)

            # A movie she rated and commented on.
            movie = Media(id=1, tmdb_id=100, media_type="movie", title="A Movie")
            s.add(movie)
            s.add(Rating(id=1, user_id=1, media_id=1, rating=8.5))
            s.add(Comment(
                id=1, user_id=1, media_type="movie", tmdb_id=100,
                content="Loved it", created_at=datetime(2026, 9, 12, 10, 0, 0),
            ))

            # A show she rated a specific season of (not the whole show).
            show = Media(id=2, tmdb_id=200, media_type="series", title="A Show")
            s.add(show)
            s.add(Rating(id=2, user_id=1, media_id=2, season_number=2, rating=9.0))
            s.add(Comment(
                id=2, user_id=1, media_type="season", tmdb_id=200, season_number=2,
                content="Best season", created_at=datetime(2026, 9, 12, 11, 0, 0),
            ))

            # A movie she commented on but never rated.
            unrated = Media(id=3, tmdb_id=300, media_type="movie", title="Unrated Movie")
            s.add(unrated)
            s.add(Comment(
                id=3, user_id=1, media_type="movie", tmdb_id=300,
                content="No opinion yet", created_at=datetime(2026, 9, 12, 12, 0, 0),
            ))

            # An episode comment - deliberately left out of the rating join.
            s.add(Comment(
                id=4, user_id=1, media_type="episode", tmdb_id=200,
                season_number=1, episode_number=3,
                content="Great episode", created_at=datetime(2026, 9, 12, 13, 0, 0),
            ))
            await s.commit()

        app = FastAPI()
        app.include_router(profile_router.router, prefix="/profile")

        async def _override_get_db():
            async with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = _override_get_db

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.engine.dispose)

        # Simplest correct override: return the real alice User instance for
        # every request in this test class - every test here only cares
        # about alice's own profile (is_owner True, so the private/public/
        # mutual-follow branches in _check_profile_access are skipped).
        async def _current_user_override():
            async with self.Session() as session:
                from sqlalchemy import select
                res = await session.execute(select(User).where(User.id == 1))
                return res.scalar_one()

        app.dependency_overrides[get_optional_user_or_api_key] = _current_user_override

    async def test_movie_comment_carries_the_movie_rating(self):
        resp = await self.client.get("/profile/1")
        self.assertEqual(resp.status_code, 200, resp.text)
        comments = {c["id"]: c for c in resp.json()["recent_comments"]}
        self.assertEqual(comments[1]["user_rating"], 8.5)

    async def test_season_comment_carries_that_seasons_rating_not_the_shows(self):
        resp = await self.client.get("/profile/1")
        comments = {c["id"]: c for c in resp.json()["recent_comments"]}
        self.assertEqual(comments[2]["user_rating"], 9.0)

    async def test_unrated_movie_comment_has_no_rating(self):
        resp = await self.client.get("/profile/1")
        comments = {c["id"]: c for c in resp.json()["recent_comments"]}
        self.assertIsNone(comments[3]["user_rating"])

    async def test_episode_comment_has_no_rating(self):
        resp = await self.client.get("/profile/1")
        comments = {c["id"]: c for c in resp.json()["recent_comments"]}
        self.assertIsNone(comments[4]["user_rating"])


if __name__ == "__main__":
    unittest.main()
