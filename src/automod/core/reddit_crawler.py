import logging

import asyncpraw

logger = logging.getLogger(__name__)


async def crawl_subreddit_top_posts(
    subreddit: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    username: str = "",
    password: str = "",
    limit: int = 20,
) -> list[dict]:
    """Fetch top posts from a public subreddit via the Reddit API.

    Returns an empty list on any error (network failure, private subreddit,
    rate limit, invalid credentials, etc.) so callers can proceed gracefully.
    """
    try:
        async with asyncpraw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            username=username or None,
            password=password or None,
        ) as reddit:
            sub = await reddit.subreddit(subreddit)
            posts = []
            async for post in sub.top(time_filter="month", limit=limit):
                if post.stickied:
                    continue
                title = post.title.strip()
                body = (post.selftext or "").strip()
                if body in ("[removed]", "[deleted]"):
                    body = ""
                if not title:
                    continue
                posts.append({"content": {"title": title, "body": body}})
        logger.info("Crawled %d posts from r/%s", len(posts), subreddit)
        return posts
    except Exception as exc:
        logger.warning("Failed to crawl r/%s: %s", subreddit, exc)
        return []
