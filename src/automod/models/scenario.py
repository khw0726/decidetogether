"""Scenario file schema for hypothetical-community user studies.

A scenario file fully describes one user-study setup: the (fake) community to
create, the rules to install, the posts to seed into the moderation queue,
and a `base_subreddit` whose live context is crawled ONCE per scenario id and
cached on disk so every setup run for the same scenario sees identical context.
"""

from typing import Optional

from pydantic import BaseModel, Field

from .schemas import PostContent


class ScenarioCommunity(BaseModel):
    """Community metadata to create."""
    name: str
    description: str = ""


class ScenarioRule(BaseModel):
    title: str
    text: str
    priority: Optional[int] = None


class ScenarioQueuePost(BaseModel):
    """One post to seed into the moderation queue.

    `content` is the PostContent the evaluation engine consumes. `label` is
    optional ground-truth ("acceptable" | "unacceptable") — currently unused
    by the runtime but preserved for later analysis.
    """
    content: PostContent
    label: Optional[str] = None
    note: Optional[str] = None


class ScenarioFile(BaseModel):
    """Top-level scenario file shape (see scenarios/example.json)."""
    id: str = Field(..., description="Unique scenario id; used as the context-cache key")
    base_subreddit: str = Field(..., description="Real subreddit crawled once for community context")
    community: ScenarioCommunity
    rules: list[ScenarioRule] = []
    queue_posts: list[ScenarioQueuePost] = []


class ScenarioSummary(BaseModel):
    """Lightweight scenario descriptor for the listing endpoint."""
    id: str
    filename: str
    community_name: str
    base_subreddit: str
    rule_count: int
    queue_post_count: int
    context_cached: bool
