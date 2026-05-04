import logging
import time
from datetime import datetime, timezone

import asyncpraw

logger = logging.getLogger(__name__)


_KNOWN_BOTS = {
    "automoderator",
    "remindmebot",
    "sneakpeekbot",
    "wikisummarizerbot",
    "b0trank",
    "savevideo",
    "auto-tldr",
    "gifreversingbot",
    "sub_doesnt_exist_bot",
    "imagesofnetwork",
    "haikubotinc",
    "of_patrol_bot",
    "same_subreddit_bot",
}


def _is_bot_username(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    if n in _KNOWN_BOTS:
        return True
    return n.endswith("bot") or n.endswith("-bot") or n.endswith("_bot")


def _is_bot_obj(obj) -> bool:
    """True if a praw post/comment is from a bot or distinguished moderator."""
    try:
        if getattr(obj, "distinguished", None) == "moderator":
            return True
    except Exception:
        pass
    try:
        name = obj.author.name if obj.author else ""
    except Exception:
        name = ""
    return _is_bot_username(name)


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
                if _is_bot_obj(comment):
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


async def crawl_subreddit_posts(
    subreddit: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    username: str = "",
    password: str = "",
    limit: int = 20,
    sort: str = "new",
    time_filter: str = "month",
) -> list[dict]:
    """Fetch posts from a public subreddit via the Reddit API.

    `sort` selects the listing: "new" (recent posts, ignores time_filter) or
    "top" (uses time_filter). Returns an empty list on any error so callers
    can proceed gracefully.
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
            if sort == "top":
                listing = sub.top(time_filter=time_filter, limit=limit)
            else:
                listing = sub.new(limit=limit)
            async for post in listing:
                if post.stickied:
                    continue
                if not post.title.strip():
                    continue
                if _is_bot_obj(post):
                    continue
                posts.append(_map_praw_post(post, subreddit))
        logger.info("Crawled %d %s posts from r/%s", len(posts), sort, subreddit)
        return posts
    except Exception as exc:
        logger.warning("Failed to crawl r/%s: %s", subreddit, exc)
        return []


async def sample_subreddit_for_context(
    subreddit: str,
    client_id: str,
    client_secret: str,
    user_agent: str,
    username: str = "",
    password: str = "",
) -> dict[str, list[dict]]:
    """Sample posts from a subreddit using activity-based strategy for context generation.

    Returns a dict with keys: hot, top, controversial, ignored, comments.
    Each value is a list of lightweight dicts (title, body, score, num_comments).
    """
    result: dict[str, list[dict]] = {
        "hot": [],
        "top": [],
        "controversial": [],
        "ignored": [],
        "comments": [],
    }

    try:
        async with asyncpraw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            username=username or None,
            password=password or None,
        ) as reddit:
            sub = await reddit.subreddit(subreddit)

            # Hot posts — typical day-to-day content (primary sample)
            hot_posts = []
            async for post in sub.hot(limit=25):
                if post.stickied:
                    continue
                if _is_bot_obj(post):
                    continue
                hot_posts.append(post)
                result["hot"].append({
                    "title": post.title.strip(),
                    "body": (post.selftext or "")[:300].strip(),
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "upvote_ratio": post.upvote_ratio,
                })

            # Top posts — what the community celebrates (secondary)
            async for post in sub.top(time_filter="month", limit=10):
                if post.stickied:
                    continue
                if _is_bot_obj(post):
                    continue
                result["top"].append({
                    "title": post.title.strip(),
                    "body": (post.selftext or "")[:300].strip(),
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "upvote_ratio": post.upvote_ratio,
                })

            # Controversial — where norms are contested
            async for post in sub.controversial(time_filter="month", limit=10):
                if post.stickied:
                    continue
                if _is_bot_obj(post):
                    continue
                result["controversial"].append({
                    "title": post.title.strip(),
                    "body": (post.selftext or "")[:300].strip(),
                    "score": post.score,
                    "num_comments": post.num_comments,
                    "upvote_ratio": post.upvote_ratio,
                })

            # Ignored posts — community-rejected content that signals implicit
            # norm violations.  Uses combined relative criteria (low upvote_ratio
            # + below-median engagement) so it works for both small and large subs.
            all_new_posts: list = []
            min_age_hours = 12
            for tf in ("week", "month"):
                scan_limit = 200 if tf == "week" else 500
                async for post in sub.new(limit=scan_limit):
                    age_hours = (time.time() - post.created_utc) / 3600
                    age_days = age_hours / 24
                    if tf == "week" and age_days > 7:
                        break
                    if tf == "month" and age_days > 30:
                        break
                    if post.stickied or age_hours < min_age_hours:
                        continue
                    if _is_bot_obj(post):
                        continue
                    all_new_posts.append(post)
                if len(all_new_posts) >= 50:
                    break

            if all_new_posts:
                scores = sorted(p.score for p in all_new_posts)
                comments = sorted(p.num_comments for p in all_new_posts)
                median_score = scores[len(scores) // 2] if scores else 1
                median_comments = comments[len(comments) // 2] if comments else 2

                # Combined filter: low upvote_ratio AND below-median engagement
                candidates = [
                    p for p in all_new_posts
                    if p.upvote_ratio < 0.45
                    and p.score < median_score
                    and p.num_comments < median_comments
                ]

                # For small subs, also include posts matching absolute thresholds
                if median_score <= 3:
                    abs_candidates = [
                        p for p in all_new_posts
                        if p.score <= 1 and p.num_comments <= 2
                        and p not in candidates
                    ]
                    candidates.extend(abs_candidates)

                # Fallback: if too few, take bottom N by upvote_ratio
                if len(candidates) < 5:
                    by_ratio = sorted(all_new_posts, key=lambda p: p.upvote_ratio)
                    candidates = by_ratio[:10]

                for p in candidates[:20]:
                    result["ignored"].append({
                        "title": p.title.strip(),
                        "body": (p.selftext or "")[:300].strip(),
                        "score": p.score,
                        "num_comments": p.num_comments,
                        "upvote_ratio": p.upvote_ratio,
                    })

            # Top comments from hot posts — actual language and tone
            for post in hot_posts[:10]:
                try:
                    post.comment_sort = "top"
                    await post.load()
                    await post.comments.replace_more(limit=0)
                    for comment in post.comments[:5]:
                        body = (comment.body or "").strip()
                        if not body or body in ("[removed]", "[deleted]"):
                            continue
                        if _is_bot_obj(comment):
                            continue
                        result["comments"].append({
                                "body": body[:300],
                                "score": comment.score,
                                "post_title": post.title.strip(),
                            })
                except Exception:
                    continue

        logger.info(
            "Sampled r/%s: %d hot, %d top, %d controversial, %d ignored, %d comments",
            subreddit,
            len(result["hot"]),
            len(result["top"]),
            len(result["controversial"]),
            len(result["ignored"]),
            len(result["comments"]),
        )
    except Exception as exc:
        logger.warning("Failed to sample r/%s for context: %s", subreddit, exc)

    return result
