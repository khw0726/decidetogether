import logging
from datetime import datetime, timezone

import asyncpraw

logger = logging.getLogger(__name__)


def _map_praw_post(post, subreddit: str) -> dict:
    """Map a asyncpraw Submission object to a PostContent-shaped dict."""
    try:
        author_name = post.author.name
        account_age_days = int((post.created_utc - post.author.created_utc) / 86400)
        total_karma = post.author.comment_karma + post.author.link_karma
    except Exception:
        author_name, account_age_days, total_karma = "", None, None

    body = (post.selftext or "").strip()
    if body in ("[removed]", "[deleted]"):
        body = ""

    links = [post.url] if not post.is_self else []

    return {
        "id": f"t3_{post.id}",
        "platform": "reddit",
        "author": {
            "username": author_name,
            "account_age_days": account_age_days,
            "platform_metadata": {"karma": total_karma},
        },
        "content": {
            "title": post.title.strip(),
            "body": body,
            "media": [],
            "links": links,
        },
        "context": {
            "channel": f"r/{subreddit}",
            "post_type": "self" if post.is_self else "link",
            "flair": post.link_flair_text or None,
            "platform_metadata": {"score": post.score, "permalink": post.permalink},
        },
        "timestamp": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
    }


async def crawl_subreddit_top_posts(
    subreddit: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    username: str = "",
    password: str = "",
    limit: int = 20,
    time_filter: str = "month",
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
            async for post in sub.top(time_filter=time_filter, limit=limit):
                if post.stickied:
                    continue
                if not post.title.strip():
                    continue
                posts.append(_map_praw_post(post, subreddit))
        logger.info("Crawled %d posts from r/%s", len(posts), subreddit)
        return posts
    except Exception as exc:
        logger.warning("Failed to crawl r/%s: %s", subreddit, exc)
        return []
