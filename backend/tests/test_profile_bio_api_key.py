import inspect
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from db import get_db
from dependencies import get_current_user_or_api_key_strict
from models.profile import UserProfileData
from models.users import User
from routers import profile as profile_router


# Same SQLite-can't-compile-Postgres-JSONB workaround as
# test_comments_api_key.py - UserProfileData has several JSONB columns
# (movie_genres/show_genres/disliked_genres/streaming_services).
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class UpdateProfileRouteWiringTests(unittest.TestCase):
    """PATCH /profile/me uses get_current_user_or_api_key_strict (was the
    JWT-only get_current_user), so a client that only holds an API key and
    already reads GET /profile/me with it can edit the bio and genre
    preferences too."""

    def test_update_profile_uses_the_strict_or_api_key_dependency(self):
        sig = inspect.signature(profile_router.update_profile)
        self.assertIs(sig.parameters["current_user"].default.dependency, get_current_user_or_api_key_strict)


class UpdateProfileWithApiKeyTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end over real HTTP (not dependency_overrides for auth - the
    point of this test is to prove the actual api_key wiring works, same
    reasoning as test_comments_api_key.py's own end-to-end class, which
    caught a real MissingGreenlet bug that a dependency-override test would
    have hidden)."""

    async def asyncSetUp(self):
        self.engine = _make_engine()
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(UserProfileData.__table__.create)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            s.add(User(id=2, email="b@b.c", username="bob", api_key="k-bob"))
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

    async def test_updates_bio_authenticated_by_api_key_query_param(self):
        resp = await self.client.patch("/profile/me", params={"api_key": "k-alice"}, json={"bio": "Watching things."})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["bio"], "Watching things.")

    async def test_updates_bio_authenticated_by_x_api_key_header(self):
        resp = await self.client.patch("/profile/me", headers={"X-Api-Key": "k-alice"}, json={"bio": "Via header."})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["bio"], "Via header.")

    async def test_rejects_update_with_no_credentials(self):
        resp = await self.client.patch("/profile/me", json={"bio": "nope"})
        self.assertEqual(resp.status_code, 401)

    async def test_get_then_patch_only_affects_the_authenticated_users_own_profile(self):
        await self.client.patch("/profile/me", params={"api_key": "k-alice"}, json={"bio": "alice's bio"})
        bob_view = await self.client.patch("/profile/me", params={"api_key": "k-bob"}, json={"bio": "bob's bio"})
        self.assertEqual(bob_view.status_code, 200, bob_view.text)
        self.assertEqual(bob_view.json()["bio"], "bob's bio")

        alice_view = await self.client.get("/profile/me", params={"api_key": "k-alice"})
        self.assertEqual(alice_view.json()["bio"], "alice's bio")


if __name__ == "__main__":
    unittest.main()
