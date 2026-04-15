import logging
from datetime import datetime, timezone

import asyncpraw

logger = logging.getLogger(__name__)


def _safe_author_info(obj) -> tuple[str, int | None, int | None]:
    """Extract author name, account age, and karma safely."""
    try:
        author_name = obj.author.name
        account_age_days = int((obj.created_utc - obj.author.created_utc) / 86400)
        total_karma = obj.author.comment_karma + obj.author.link_karma
    except Exception:
        author_name, account_age_days, total_karma = "", None, None
    return author_name, account_age_days, total_karma


def _map_praw_post(post, subreddit: str) -> dict:
    """Map a asyncpraw Submission object to a PostContent-shaped dict."""
    author_name, account_age_days, total_karma = _safe_author_info(post)

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


def _map_praw_comment(comment, submission, subreddit: str, parent_chain: list) -> dict:
    """Map an asyncpraw Comment to a PostContent-shaped dict with thread context."""
    author_name, account_age_days, total_karma = _safe_author_info(comment)

    body = (comment.body or "").strip()
    if body in ("[removed]", "[deleted]"):
        body = ""

    # Build thread context: OP first, then ancestor comments in order
    thread_context = []

    # Always include the original post (OP)
    op_body = (submission.selftext or "").strip()
    if op_body in ("[removed]", "[deleted]"):
        op_body = ""
    try:
        op_author = submission.author.name
    except Exception:
        op_author = ""

    thread_context.append({
        "role": "op",
        "author": op_author,
        "content": {
            "title": submission.title.strip(),
            "body": op_body,
            "media": [],
            "links": [submission.url] if not submission.is_self else [],
        },
        "depth": 0,
        "platform_id": f"t3_{submission.id}",
    })

    # Add ancestor comments (from oldest to most recent parent)
    for i, ancestor in enumerate(parent_chain):
        ancestor_body = (ancestor.body or "").strip()
        if ancestor_body in ("[removed]", "[deleted]"):
            ancestor_body = ""
        try:
            ancestor_author = ancestor.author.name
        except Exception:
            ancestor_author = ""

        role = "parent_comment" if i == len(parent_chain) - 1 else "ancestor_comment"
        thread_context.append({
            "role": role,
            "author": ancestor_author,
            "content": {
                "title": "",
                "body": ancestor_body,
                "media": [],
                "links": [],
            },
            "depth": ancestor.depth,
            "platform_id": f"t1_{ancestor.id}",
        })

    return {
        "id": f"t1_{comment.id}",
        "platform": "reddit",
        "author": {
            "username": author_name,
            "account_age_days": account_age_days,
            "platform_metadata": {"karma": total_karma},
        },
        "content": {
            "title": "",
            "body": body,
            "media": [],
            "links": [],
        },
        "context": {
            "channel": f"r/{subreddit}",
            "thread_id": f"t3_{submission.id}",
            "parent_post_id": f"t1_{comment.parent_id}" if comment.parent_id else None,
            "post_type": "comment",
            "flair": None,
            "platform_metadata": {"score": comment.score, "permalink": comment.permalink},
        },
        "thread_context": thread_context,
        "timestamp": datetime.fromtimestamp(comment.created_utc, tz=timezone.utc).isoformat(),
    }


async def _collect_parent_chain(comment) -> list:
    """Walk up the parent chain to collect ancestor comments (excluding OP)."""
    chain = []
    current = comment
    # Limit depth to avoid excessive API calls
    max_depth = 5
    while max_depth > 0:
        parent_id = current.parent_id
        if not parent_id or parent_id.startswith("t3_"):
            # Reached the submission (OP) — stop
            break
        try:
            parent = await current._reddit.comment(parent_id.replace("t1_", ""))
            await parent.load()
            chain.append(parent)
            current = parent
            max_depth -= 1
        except Exception:
            break
    # Reverse so oldest ancestor comes first
    chain.reverse()
    return chain


async def crawl_subreddit_comments(
    subreddit: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    username: str = "",
    password: str = "",
    limit: int = 50,
) -> list[dict]:
    """Fetch recent comments from a subreddit with their thread context.

    Returns a list of PostContent-shaped dicts, each with thread_context populated.
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
            comments = []
            async for comment in sub.comments(limit=limit):
                body = (comment.body or "").strip()
                if body in ("[removed]", "[deleted]", ""):
                    continue

                try:
                    # Load the parent submission for OP context
                    submission = comment.submission
                    await submission.load()

                    # Collect ancestor comments for thread context
                    parent_chain = await _collect_parent_chain(comment)

                    mapped = _map_praw_comment(comment, submission, subreddit, parent_chain)
                    comments.append(mapped)
                except Exception as e:
                    logger.debug("Skipping comment %s: %s", comment.id, e)
                    continue

        logger.info("Crawled %d comments from r/%s", len(comments), subreddit)
        return comments
    except Exception as exc:
        logger.warning("Failed to crawl comments from r/%s: %s", subreddit, exc)
        return []


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
