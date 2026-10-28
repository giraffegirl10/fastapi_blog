import pytest
from httpx import AsyncClient, patch

from tests.conftest import auth_header, create_test_user, login_user

@pytest.mark.anyio
async def test_get_posts_empty(client: AsyncClient):
    response = await client.get("/api/posts")

    assert response.status_code == 200
    data = response.json() 
    assert data["posts"] == []
    assert data["total"] == 0
    assert data["has_more"] is False


@pytest.mark.anyio
async def test_get_posts_not_found(client: AsyncClient):
    response = await client.get("/api/posts/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Post not found"}

@pytest.mark.anyio
async def test_create_post_success(client: AsyncClient):
    user = await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    response = await client.post(
        "/api/posts",
        json={"title": "My First Post", "content": "This is the content"},
        headers=headers,
    )

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "My First Post"
    assert data["content"] == "This is the content"
    assert data["user_id"] == user["id"]
    assert "id" in data
    assert "date_posted" in data
    assert data["author"]["username"] == "testuser"

@pytest.mark.anyio
async def test_create_post_unauthorized(client: AsyncClient):
    response = await client.post(
        "/api/posts",
        json={"title": "Test Post", "content": "Test content"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"

@pytest.mark.anyio
async def test_update_post_success(client: AsyncClient):
    await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    response = await client.post(
        "/api/posts",
        json={"title": "Original Title", "content": "Original content"},
        headers=headers,
    )
    post_id = response.json()["id"]

    response = await client.patch(
        f"/api/posts/{post_id}",
        json={"title": "Updated Title"},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["content"] == "Original content"

@pytest.mark.anyio
async def test_update_post_wrong_user(client: AsyncClient):
    await create_test_user(client, username="user1", email="user1@example.com")
    token1 = await login_user(client, email="user1@example.com")

    response = await client.post(
        "/api/posts",
        json={"title": "User 1's Post", "content": "Only user 1 can edit this"},
        headers=auth_header(token1),
    )
    post_id = response.json()["id"]

    await create_test_user(client, username="user2", email="user2@example.com")
    token2 = await login_user(client, email="user2@example.com")

    response = await client.patch(
        f"/api/posts/{post_id}",
        json={"title": "Hacked Title"},
        headers=auth_header(token2),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to update this post"

@pytest.mark.anyio
async def test_get_posts_with_pagination(client: AsyncClient):
    await create_test_user(client)
    token = await login_user(client)
    headers = auth_header(token)

    for i in range(5):
        response = await client.post(
            "/api/posts",
            json={"title": f"Post {i}", "content": f"Content for post {i}"},
            headers=headers,
        )
        assert response.status_code == 201

    response = await client.get("/api/posts")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["posts"]) == 5
    assert data["has_more"] is False

    response = await client.get("/api/posts?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["posts"]) == 2
    assert data["has_more"] is True

    response = await client.get("/api/posts?skip=2&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["posts"]) == 2
    assert data["skip"] == 2
    assert data["limit"] == 2



async def create_test_post(client: AsyncClient, headers: dict[str, str]) -> int:
    response = await client.post(
        "/api/posts",
        json={"title": "Likeable Post", "content": "Please like me"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.anyio
async def test_new_post_has_zero_likes(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)

    response = await client.get(f"/api/posts/{post_id}")

    assert response.json()["likes"] == 0


@pytest.mark.anyio
async def test_like_post_success(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)

    response = await client.post(f"/api/posts/{post_id}/like", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"post_id": post_id, "likes": 1, "liked": True}
    response = await client.get(f"/api/posts/{post_id}")
    assert response.json()["likes"] == 1


@pytest.mark.anyio
async def test_like_post_twice_counts_once(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)

    await client.post(f"/api/posts/{post_id}/like", headers=headers)
    response = await client.post(f"/api/posts/{post_id}/like", headers=headers)

    assert response.status_code == 200
    assert response.json()["likes"] == 1


@pytest.mark.anyio
async def test_likes_from_different_users_add_up(client: AsyncClient):
    await create_test_user(client, username="user1", email="user1@example.com")
    headers1 = auth_header(await login_user(client, email="user1@example.com"))
    await create_test_user(client, username="user2", email="user2@example.com")
    headers2 = auth_header(await login_user(client, email="user2@example.com"))
    post_id = await create_test_post(client, headers1)

    await client.post(f"/api/posts/{post_id}/like", headers=headers1)
    response = await client.post(f"/api/posts/{post_id}/like", headers=headers2)

    assert response.json()["likes"] == 2


@pytest.mark.anyio
async def test_unlike_post(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)
    await client.post(f"/api/posts/{post_id}/like", headers=headers)

    response = await client.delete(f"/api/posts/{post_id}/like", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"post_id": post_id, "likes": 0, "liked": False}


@pytest.mark.anyio
async def test_unlike_without_like_keeps_count(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)

    response = await client.delete(f"/api/posts/{post_id}/like", headers=headers)

    assert response.status_code == 200
    assert response.json()["likes"] == 0


@pytest.mark.anyio
async def test_like_post_unauthorized(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)

    response = await client.post(f"/api/posts/{post_id}/like")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_like_post_not_found(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))

    response = await client.post("/api/posts/999/like", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Post not found"}


@pytest.mark.anyio
async def test_get_liked_post_ids(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    liked_id = await create_test_post(client, headers)
    other_id = await create_test_post(client, headers)
    await client.post(f"/api/posts/{liked_id}/like", headers=headers)

    response = await client.get(
        "/api/posts/liked",
        params={"post_ids": [liked_id, other_id]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == [liked_id]


@pytest.mark.anyio
async def test_delete_liked_post(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    post_id = await create_test_post(client, headers)
    await client.post(f"/api/posts/{post_id}/like", headers=headers)

    response = await client.delete(f"/api/posts/{post_id}", headers=headers)

    assert response.status_code == 204


@pytest.mark.anyio
async def test_deleting_user_removes_their_likes(client: AsyncClient):
    await create_test_user(client, username="author", email="author@example.com")
    author_headers = auth_header(await login_user(client, email="author@example.com"))
    fan = await create_test_user(client, username="fan", email="fan@example.com")
    fan_headers = auth_header(await login_user(client, email="fan@example.com"))
    post_id = await create_test_post(client, author_headers)
    await client.post(f"/api/posts/{post_id}/like", headers=fan_headers)

    response = await client.delete(f"/api/users/{fan['id']}", headers=fan_headers)

    assert response.status_code == 204
    response = await client.get(f"/api/posts/{post_id}")
    assert response.json()["likes"] == 0


@pytest.mark.anyio
async def test_popular_posts_ordered_by_likes(client: AsyncClient):
    await create_test_user(client, username="user1", email="user1@example.com")
    headers1 = auth_header(await login_user(client, email="user1@example.com"))
    await create_test_user(client, username="user2", email="user2@example.com")
    headers2 = auth_header(await login_user(client, email="user2@example.com"))
    unliked_id = await create_test_post(client, headers1)
    one_like_id = await create_test_post(client, headers1)
    two_likes_id = await create_test_post(client, headers1)
    await client.post(f"/api/posts/{one_like_id}/like", headers=headers1)
    await client.post(f"/api/posts/{two_likes_id}/like", headers=headers1)
    await client.post(f"/api/posts/{two_likes_id}/like", headers=headers2)

    response = await client.get("/api/posts/popular")

    assert response.status_code == 200
    data = response.json()
    assert [post["id"] for post in data] == [two_likes_id, one_like_id]
    assert [post["likes"] for post in data] == [2, 1]
    assert unliked_id not in [post["id"] for post in data]


@pytest.mark.anyio
async def test_popular_posts_respects_limit(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    for _ in range(3):
        post_id = await create_test_post(client, headers)
        await client.post(f"/api/posts/{post_id}/like", headers=headers)

    response = await client.get("/api/posts/popular?limit=2")

    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.anyio
async def test_popular_posts_empty_without_likes(client: AsyncClient):
    await create_test_user(client)
    headers = auth_header(await login_user(client))
    await create_test_post(client, headers)

    response = await client.get("/api/posts/popular")

    assert response.status_code == 200
    assert response.json() == []
