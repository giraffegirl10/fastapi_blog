"""Fill the database with randomly generated sample users, posts and likes.

Run with:  uv run python populate_db.py [--seed N] [--users N] [--posts N]

Each run removes the previous sample data first. Sample users are the ones
with an @example.com email; any other account (and its posts) is left alone.
Pass --seed to get the same "random" data every time.
"""

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update

import models
from config import settings
from database import AsyncSessionLocal, engine
from image_utils import _get_s3_client
from main import app

SAMPLE_EMAIL_DOMAIN = "example.com"
# Every sample account shares this password so you can log in as any of them.
SAMPLE_PASSWORD = "SamplePassword1!"

USERNAME_ADJECTIVES = [
    "Quiet", "Pixel", "Lucky", "Cosmic", "Sunny", "Rusty", "Clever", "Midnight",
    "Velvet", "Brave", "Mellow", "Swift", "Golden", "Curious", "Paper", "Neon",
]
USERNAME_NOUNS = [
    "Falcon", "Baker", "Otter", "Coder", "Hiker", "Maple", "Comet", "Lantern",
    "Fern", "Pebble", "Sparrow", "Teapot", "Harbor", "Juniper", "Canyon", "Wren",
]

# Each topic has title templates ({word} is filled from its words list) and a
# pool of sentences; a post is one title plus a few shuffled sentences.
TOPICS = {
    "python": {
        "titles": [
            "What I Learned Building a {word}",
            "Five Tips for Cleaner {word} Code",
            "Stop Overthinking Your {word}",
            "A Beginner's Guide to {word}",
            "Why I Finally Started Using {word}",
        ],
        "words": [
            "REST API", "Type Hints", "Async Code", "Pydantic Models", "Unit Tests",
            "Database Migrations", "Dependency Injection", "Error Handling",
        ],
        "sentences": [
            "Small, focused functions made the whole thing easier to test.",
            "Reading the official docs end to end saved me hours of guessing.",
            "Type hints caught three bugs before I even ran the code.",
            "I rewrote it twice, and the second rewrite was half the size.",
            "Writing the tests first felt slow, but it paid off by day two.",
            "Naming things well turned out to be the hardest part.",
            "Async is great, as long as everything underneath is async too.",
            "The error messages got much friendlier once I validated input early.",
            "My advice: keep the first version boring and make it work.",
        ],
    },
    "cooking": {
        "titles": [
            "The Easiest {word} I've Ever Made",
            "My Weeknight {word} Routine",
            "I Tried Making {word} From Scratch",
            "In Defense of {word}",
        ],
        "words": [
            "Sourdough", "Ramen", "Banana Bread", "Tomato Soup", "Dumplings",
            "Pancakes", "Chili", "Focaccia",
        ],
        "sentences": [
            "The secret ingredient was patience, and a little extra salt.",
            "It took three tries before it looked anything like the photo.",
            "Fresh herbs make a bigger difference than I expected.",
            "I doubled the garlic, which I recommend without hesitation.",
            "Leftovers were somehow even better the next day.",
            "A cast iron pan made the crust come out perfectly.",
            "Next time I'm prepping everything before turning on the stove.",
        ],
    },
    "outdoors": {
        "titles": [
            "A Weekend Hike to {word}",
            "What I Pack for a Day at {word}",
            "Sunrise at {word}",
            "Getting Lost (On Purpose) Near {word}",
        ],
        "words": [
            "Cedar Ridge", "the Lake Trail", "Granite Falls", "Pine Hollow",
            "the Coastal Bluffs", "Echo Canyon", "Willow Creek",
        ],
        "sentences": [
            "The trail was steeper than the map suggested.",
            "We started before dawn and had the summit to ourselves.",
            "Bring more water than you think you need.",
            "The wildflowers were out in full force this time of year.",
            "My boots finally earned their first real layer of mud.",
            "There's no cell signal, which turned out to be the best part.",
            "The view at the top made every switchback worth it.",
        ],
    },
    "books": {
        "titles": [
            "My Thoughts on {word}",
            "Books That Changed How I Think About {word}",
            "Reading More {word} This Year",
            "A Short Review: {word}",
        ],
        "words": [
            "Science Fiction", "Productivity", "Short Stories", "History",
            "Mystery Novels", "Poetry", "Graphic Novels",
        ],
        "sentences": [
            "I read it in two sittings and immediately wanted to start over.",
            "The middle dragged, but the ending made up for it.",
            "Audiobooks have doubled how much I get through each month.",
            "I keep a notebook of quotes now, and it's already half full.",
            "It's the kind of book you lend to a friend and never get back.",
            "Some chapters I had to read twice, in the best way.",
        ],
    },
    "games": {
        "titles": [
            "Why I Keep Coming Back to {word}",
            "Game Night Report: {word}",
            "Is {word} Worth Learning?",
            "My Hot Take on {word}",
        ],
        "words": [
            "Chess", "Cozy Farming Games", "Tabletop RPGs", "Puzzle Games",
            "Speedrunning", "Deck Builders", "Co-op Board Games",
        ],
        "sentences": [
            "We said one more round, and then it was 2am.",
            "The learning curve is steep, but it clicks eventually.",
            "Nothing beats the moment a risky strategy actually works.",
            "I lost every game and still had a great time.",
            "The soundtrack alone is worth the price.",
            "Playing with friends turns every mistake into a story.",
        ],
    },
    "everyday": {
        "titles": [
            "Small Wins: {word}",
            "Trying a New {word} for a Month",
            "Notes on {word}",
            "Things Nobody Told Me About {word}",
        ],
        "words": [
            "Morning Routine", "Houseplants", "Journaling", "Learning Guitar",
            "Working From Home", "Photography", "Running",
        ],
        "sentences": [
            "Starting small made it much easier to stick with.",
            "Week one was rough, but week three felt natural.",
            "I didn't expect it to improve my sleep, but it did.",
            "Tracking progress in a notebook kept me honest.",
            "The hardest part was simply getting started each day.",
            "I'm not an expert, but I'm a lot better than when I began.",
        ],
    },
}

CLOSERS = [
    "Has anyone else tried this?",
    "More on this soon.",
    "Thanks for reading!",
    "Let me know what you'd do differently.",
    "",
]


def random_username(rng: random.Random, taken: set[str]) -> str:
    while True:
        name = rng.choice(USERNAME_ADJECTIVES) + rng.choice(USERNAME_NOUNS)
        if rng.random() < 0.4:
            name += str(rng.randint(1, 99))
        if name not in taken:
            taken.add(name)
            return name


def random_post(rng: random.Random, used_titles: set[str]) -> dict:
    while True:
        topic = TOPICS[rng.choice(list(TOPICS))]
        title = rng.choice(topic["titles"]).format(word=rng.choice(topic["words"]))
        if title not in used_titles:
            used_titles.add(title)
            break

    sentences = rng.sample(topic["sentences"], k=rng.randint(2, 4))
    content = " ".join([*sentences, rng.choice(CLOSERS)]).strip()
    return {"title": title, "content": content}


async def clear_sample_data() -> None:
    async with AsyncSessionLocal() as db:
        sample_users = select(models.User.id).where(
            models.User.email.like(f"%@{SAMPLE_EMAIL_DOMAIN}"),
        )

        # Delete profile pictures from S3 (need DB records to know which files)
        result = await db.execute(
            select(models.User.image_file).where(
                models.User.id.in_(sample_users),
                models.User.image_file.is_not(None),
            ),
        )
        filenames = result.scalars().all()
        if filenames:
            s3 = _get_s3_client()
            s3.delete_objects(
                Bucket=settings.s3_bucket_name,
                Delete={"Objects": [{"Key": f"profile_pics/{f}"} for f in filenames]},
            )
            print(f"Deleted {len(filenames)} images from S3")

        # Likes go with their posts and users via ON DELETE CASCADE
        await db.execute(
            delete(models.PasswordResetToken).where(
                models.PasswordResetToken.user_id.in_(sample_users),
            ),
        )
        await db.execute(delete(models.Post).where(models.Post.user_id.in_(sample_users)))
        await db.execute(delete(models.User).where(models.User.id.in_(sample_users)))
        await db.commit()
    print("Cleared previous sample data")


async def recount_likes() -> None:
    """Make every post's cached like count match its post_likes rows."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(models.Post).values(
                likes=select(func.count())
                .select_from(models.PostLike)
                .where(models.PostLike.post_id == models.Post.id)
                .scalar_subquery(),
            ),
        )
        await db.commit()


async def spread_post_dates(rng: random.Random, post_ids: list[int]) -> None:
    """Give posts random dates over the last ~4 months."""
    now = datetime.now(UTC)
    dates = sorted(
        now - timedelta(days=rng.uniform(0, 120), minutes=rng.randint(0, 1440))
        for _ in post_ids
    )
    async with AsyncSessionLocal() as db:
        for post_id, date in zip(post_ids, dates, strict=True):
            await db.execute(
                update(models.Post)
                .where(models.Post.id == post_id)
                .values(date_posted=date),
            )
        await db.commit()


async def populate(seed: int | None, user_count: int, post_count: int) -> None:
    rng = random.Random(seed)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        await clear_sample_data()

        print(f"\nCreating {user_count} users...")
        users: list[dict] = []
        taken_names: set[str] = set()
        for _ in range(user_count):
            username = random_username(rng, taken_names)
            email = f"{username.lower()}@{SAMPLE_EMAIL_DOMAIN}"
            response = await client.post(
                "/api/users",
                json={"username": username, "email": email, "password": SAMPLE_PASSWORD},
            )
            response.raise_for_status()

            response = await client.post(
                "/api/users/token",
                data={"username": email, "password": SAMPLE_PASSWORD},
            )
            response.raise_for_status()
            token = response.json()["access_token"]


            users.append({"username": username, "token": token})
            print(f"  Created: {username}")

        print(f"\nCreating {post_count} posts...")
        post_ids: list[int] = []
        used_titles: set[str] = set()
        for _ in range(post_count):
            author = rng.choice(users)
            response = await client.post(
                "/api/posts",
                json=random_post(rng, used_titles),
                headers={"Authorization": f"Bearer {author['token']}"},
            )
            response.raise_for_status()
            post_ids.append(response.json()["id"])

        await spread_post_dates(rng, post_ids)

        print("\nAdding likes...")
        like_total = 0
        for user in users:
            # Each user likes a random handful of posts
            for post_id in rng.sample(post_ids, k=rng.randint(0, post_count // 3)):
                response = await client.post(
                    f"/api/posts/{post_id}/like",
                    headers={"Authorization": f"Bearer {user['token']}"},
                )
                response.raise_for_status()
                like_total += 1

    await recount_likes()
    await engine.dispose()

    print("\nDone!")
    print(f"  {user_count} users (password for all: see SAMPLE_PASSWORD in this file)")
    print(f"  {post_count} posts")
    print(f"  {like_total} likes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, help="repeatable random data")
    parser.add_argument("--users", type=int, default=6)
    parser.add_argument("--posts", type=int, default=40)
    args = parser.parse_args()

    max_posts = sum(len(t["titles"]) * len(t["words"]) for t in TOPICS.values())
    if not 1 <= args.posts <= max_posts:
        parser.error(f"--posts must be between 1 and {max_posts}")
    if args.users < 1:
        parser.error("--users must be at least 1")

    # If running on Windows, change the event loop policy to prevent the
    # "RuntimeError: Event loop is closed" error upon exit.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(populate(args.seed, args.users, args.posts))
