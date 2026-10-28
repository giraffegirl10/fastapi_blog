from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from auth import CurrentUser
from config import settings
from database import get_db
from schemas import (
    PaginatedPostsResponse,
    PostCreate,
    PostLikeResponse,
    PostResponse,
    PostUpdate,
)

router = APIRouter()


@router.get("", response_model=PaginatedPostsResponse)
async def get_posts(
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
):

    count_result = await db.execute(select(func.count()).select_from(models.Post))
    total = count_result.scalar() or 0

    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc())
        .offset(skip)
        .limit(limit),
    )
    posts = result.scalars().all()

    has_more = skip + len(posts) < total

    return PaginatedPostsResponse(
        posts=[PostResponse.model_validate(post) for post in posts],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more,
    )


@router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    post: PostCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    new_post = models.Post(
        title=post.title,
        content=post.content,
        user_id=current_user.id,
    )
    db.add(new_post)
    await db.commit()
    await db.refresh(new_post, attribute_names=["author"])
    return new_post


@router.get("/popular", response_model=list[PostResponse])
async def get_popular_posts(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
):
    """Return the most liked posts; posts nobody has liked are left out."""
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.likes > 0)
        .order_by(models.Post.likes.desc(), models.Post.date_posted.desc())
        .limit(limit),
    )
    return result.scalars().all()


@router.get("/liked", response_model=list[int])
async def get_liked_post_ids(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    post_ids: Annotated[list[int], Query(max_length=100)],
):
    """Return which of the given posts the current user has liked."""
    result = await db.execute(
        select(models.PostLike.post_id).where(
            models.PostLike.user_id == current_user.id,
            models.PostLike.post_id.in_(post_ids),
        ),
    )
    return result.scalars().all()


async def get_like_count_or_404(db: AsyncSession, post_id: int) -> int:
    likes = await db.scalar(select(models.Post.likes).where(models.Post.id == post_id))
    if likes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    return likes


@router.post("/{post_id}/like", response_model=PostLikeResponse)
async def like_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_like_count_or_404(db, post_id)

    # ON CONFLICT DO NOTHING makes liking twice a no-op, even for racing requests.
    result = await db.execute(
        insert(models.PostLike)
        .values(user_id=current_user.id, post_id=post_id)
        .on_conflict_do_nothing()
        .returning(models.PostLike.post_id),
    )
    if result.first() is not None:
        await db.execute(
            update(models.Post)
            .where(models.Post.id == post_id)
            .values(likes=models.Post.likes + 1),
        )
    await db.commit()

    likes = await get_like_count_or_404(db, post_id)
    return PostLikeResponse(post_id=post_id, likes=likes, liked=True)


@router.delete("/{post_id}/like", response_model=PostLikeResponse)
async def unlike_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_like_count_or_404(db, post_id)

    result = await db.execute(
        delete(models.PostLike)
        .where(
            models.PostLike.user_id == current_user.id,
            models.PostLike.post_id == post_id,
        )
        .returning(models.PostLike.post_id),
    )
    if result.first() is not None:
        await db.execute(
            update(models.Post)
            .where(models.Post.id == post_id)
            .values(likes=models.Post.likes - 1),
        )
    await db.commit()

    likes = await get_like_count_or_404(db, post_id)
    return PostLikeResponse(post_id=post_id, likes=likes, liked=False)


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(post_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.id == post_id),
    )
    post = result.scalars().first()
    if post:
        return post
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")


@router.put("/{post_id}", response_model=PostResponse)
async def update_post_full(
    post_id: int,
    post_data: PostCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    if post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this post",
        )

    post.title = post_data.title
    post.content = post_data.content

    await db.commit()
    await db.refresh(post, attribute_names=["author"])
    return post


@router.patch("/{post_id}", response_model=PostResponse)
async def update_post_partial(
    post_id: int,
    post_data: PostUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    if post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this post",
        )

    update_data = post_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(post, field, value)

    await db.commit()
    await db.refresh(post, attribute_names=["author"])
    return post


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    if post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this post",
        )

    await db.delete(post)
    await db.commit()