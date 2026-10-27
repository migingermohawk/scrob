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
from models.comments import Comment
from models.global_settings import GlobalSettings
from models.profile import UserProfileData
from models.users import User
from routers import comments as comments_router


# Same SQLite-can't-compile-Postgres-JSONB workaround as test_auth_me_api_key.py -
# GlobalSettings also has JSONB columns (radarr_tags/sonarr_tags), and
# create_comment queries that table unconditionally.
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class CommentRoutesWiringTests(unittest.TestCase):
    """create_comment/update_comment/delete_comment use
    get_current_user_or_api_key_strict (was the JWT-only get_current_user),
    so a client that only holds an API key can post, edit and delete
    comments as well as read them."""

    def test_create_comment_uses_the_strict_or_api_key_dependency(self):
        sig = inspect.signature(comments_router.create_comment)
        self.assertIs(sig.parameters["current_user"].default.dependency, get_current_user_or_api_key_strict)

    def test_update_comment_uses_the_strict_or_api_key_dependency(self):
        sig = inspect.signature(comments_router.update_comment)
        self.assertIs(sig.parameters["current_user"].default.dependency, get_current_user_or_api_key_strict)

    def test_delete_comment_uses_the_strict_or_api_key_dependency(self):
        sig = inspect.signature(comments_router.delete_comment)
        self.assertIs(sig.parameters["current_user"].default.dependency, get_current_user_or_api_key_strict)


class PostCommentWithApiKeyTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end over real HTTP (not dependency_overrides for auth - that
    would hide bugs in the actual api_key wiring, which is the point of
    this change). Caught a real one during development: the api_key branch
    of get_current_user_or_api_key_strict didn't selectinload(User.profile),
    so create_comment's synchronous current_user.display_name/.profile
    reads raised MissingGreenlet for any api_key-authenticated request even
    though the exact same code worked fine over a JWT."""

    async def asyncSetUp(self):
        self.engine = _make_engine()
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(UserProfileData.__table__.create)
            await conn.run_sync(Comment.__table__.create)
            await conn.run_sync(GlobalSettings.__table__.create)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            s.add(User(id=2, email="b@b.c", username="bob", api_key="k-bob"))
            await s.commit()

        app = FastAPI()
        app.include_router(comments_router.router, prefix="/comments")

        async def _override_get_db():
            async with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = _override_get_db

        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.engine.dispose)

    async def test_creates_a_comment_authenticated_by_api_key_query_param(self):
        resp = await self.client.post(
            "/comments",
            params={"api_key": "k-alice"},
            json={"media_type": "movie", "tmdb_id": 123, "content": "great film"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["content"], "great film")
        self.assertEqual(body["username"], "alice")

    async def test_creates_a_comment_authenticated_by_x_api_key_header(self):
        resp = await self.client.post(
            "/comments",
            headers={"X-Api-Key": "k-alice"},
            json={"media_type": "movie", "tmdb_id": 123, "content": "great film"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["username"], "alice")

    async def test_rejects_creation_with_no_credentials(self):
        resp = await self.client.post(
            "/comments",
            json={"media_type": "movie", "tmdb_id": 123, "content": "great film"},
        )
        self.assertEqual(resp.status_code, 401)

    async def test_updates_own_comment_via_api_key(self):
        create = await self.client.post(
            "/comments",
            params={"api_key": "k-alice"},
            json={"media_type": "movie", "tmdb_id": 123, "content": "first draft"},
        )
        comment_id = create.json()["id"]

        update = await self.client.patch(
            f"/comments/{comment_id}",
            params={"api_key": "k-alice"},
            json={"content": "revised"},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["content"], "revised")

    async def test_cannot_update_someone_elses_comment_via_api_key(self):
        create = await self.client.post(
            "/comments",
            params={"api_key": "k-alice"},
            json={"media_type": "movie", "tmdb_id": 123, "content": "alice's take"},
        )
        comment_id = create.json()["id"]

        update = await self.client.patch(
            f"/comments/{comment_id}",
            params={"api_key": "k-bob"},
            json={"content": "hijacked"},
        )
        self.assertEqual(update.status_code, 403)

    async def test_deletes_own_comment_via_api_key(self):
        create = await self.client.post(
            "/comments",
            params={"api_key": "k-alice"},
            json={"media_type": "movie", "tmdb_id": 123, "content": "throwaway"},
        )
        comment_id = create.json()["id"]

        delete = await self.client.delete(f"/comments/{comment_id}", params={"api_key": "k-alice"})
        self.assertEqual(delete.status_code, 200, delete.text)


if __name__ == "__main__":
    unittest.main()
