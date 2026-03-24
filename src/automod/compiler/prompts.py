"""All prompt templates for the AutoMod Agent compiler and evaluator."""

from typing import Any, Optional


# ── Triage ─────────────────────────────────────────────────────────────────────

TRIAGE_SYSTEM = """You are a moderation rule classifier. Your task is to classify community rules into one of four categories:

- **actionable**: Describes a content standard that can be evaluated against a specific post. The agent can look at a post and determine if it violates this rule. Examples: "No self-promotion or spam", "Be respectful to other members", "No NSFW content".
- **procedural**: Describes moderator procedures, escalation paths, or enforcement discretion. Not evaluable per-post. Examples: "Moderators may act with discretion", "Repeated offenses will result in a permanent ban".
- **meta**: Describes rule governance, scope, or applicability. Examples: "Rules are subject to change without notice", "These rules apply to all posts and comments".
- **informational**: Provides community context but no enforceable standard. Examples: "This is a community for Python developers", "We welcome beginners and experts alike".

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_triage_prompt(rule_text: str, community_name: str, platform: str) -> str:
    return f"""Classify this community rule for the "{community_name}" community on {platform}.

Rule text: {rule_text}

Return JSON in exactly this format:
{{
  "rule_type": "actionable" | "procedural" | "meta" | "informational",
  "reasoning": "One sentence explaining why this classification was chosen."
}}"""


# ── Compile ────────────────────────────────────────────────────────────────────

COMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to compile a moderator's \
natural-language rule into a precise, structured checklist that an automated system can execute.

Each checklist item must have:
- description: Plain English question this item answers (concise, actionable)
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred)
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema (see below)
- combine_mode: How children combine: "all_must_pass" or "any_must_pass"
- fail_action: What to do if this item fails: "remove", "flag", or "continue"
- children: Nested sub-items (empty list if leaf)

Logic schemas:
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any"|"all", "negate": false}
- structural: {"type": "structural", "checks": [{"field": "account_age_days"|"post_type"|"flair"|"karma", "operator": "<"|">"|"<="|">="|"=="|"!="|"in", "value": ...}], "match_mode": "all"|"any"}
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Keep trees shallow (2 levels max). Generate exactly 3 positive examples (posts that follow the rule) and 3 negative examples (posts that violate the rule).

Return ONLY valid JSON with no markdown formatting or code blocks."""

# Few-shot examples for compilation
COMPILE_FEW_SHOT_EXAMPLES = """
Here are two examples of well-compiled rules:

EXAMPLE 1:
Rule: "No self-promotion or spam. Posts should contribute to the community, not advertise products or services."
Output:
{
  "checklist_tree": [
    {
      "description": "Does the content contain explicit promotional language or calls to action?",
      "rule_text_anchor": "not advertise products or services",
      "item_type": "deterministic",
      "logic": {
        "type": "deterministic",
        "patterns": [
          {"regex": "\\\\b(buy|sell|discount|coupon|promo|affiliate|sponsored|shop now|click here|sign up|free trial)\\\\b", "case_sensitive": false}
        ],
        "match_mode": "any",
        "negate": false
      },
      "combine_mode": "all_must_pass",
      "weight": 1.0,
      "fail_action": "flag",
      "children": []
    },
    {
      "description": "Does the content contain known spam domains or URL shorteners?",
      "rule_text_anchor": "not advertise products or services",
      "item_type": "deterministic",
      "logic": {
        "type": "deterministic",
        "patterns": [
          {"regex": "(?i)(bit\\.ly|tinyurl\\.com|t\\.co|goo\\.gl)/\\S+", "case_sensitive": false}
        ],
        "match_mode": "any",
        "negate": false
      },
      "combine_mode": "all_must_pass",
      "weight": 1.0,
      "fail_action": "flag",
      "children": []
    },
    {
      "description": "Is this content primarily self-promotional, even without explicit keywords?",
      "rule_text_anchor": "Posts should contribute to the community",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Evaluate whether this post is primarily self-promotional. Does it mainly serve to advertise the poster's product, service, channel, or brand rather than contribute useful information or discussion to the community or legitimately sharing their work?",
        "rubric": "Consider: (1) Is the post centered on promoting something the author created or sells? (2) Does it include calls to action like 'check out my...', 'I made...', 'visit my...'? (3) Is there genuine value for readers beyond the promotional aspect? (4) Does the author disclose affiliation? Score higher (more promotional) when the post reads like an advertisement.",
        "threshold": 0.65,
        "examples_to_include": 5
      },
      "combine_mode": "all_must_pass",
      "weight": 1.5,
      "fail_action": "remove",
      "children": []
    },
    {
      "description": "Check if account is new (spam signal)",
      "rule_text_anchor": null,
      "item_type": "structural",
      "logic": {
        "type": "structural",
        "checks": [
          {"field": "account_age_days", "operator": "<", "value": 7}
        ],
        "match_mode": "all"
      },
      "combine_mode": "all_must_pass",
      "weight": 0.5,
      "fail_action": "flag",
      "children": []
    }
  ],
  "examples": [
    {
      "label": "negative",
      "content": {
        "id": "example-1",
        "platform": "reddit",
        "author": {"username": "shopowner123", "account_age_days": 5, "platform_metadata": {}},
        "content": {"title": "Check out my new online store - 20% off this week!", "body": "Hi everyone! I just launched my store at myshop.com. Use code REDDIT20 for 20% off. Would love your feedback!", "media": [], "links": ["https://myshop.com"]},
        "context": {"channel": "r/community", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Clear self-promotion with discount code and external shop link"
    },
    {
      "label": "positive",
      "content": {
        "id": "example-2",
        "platform": "reddit",
        "author": {"username": "helpfuluser", "account_age_days": 365, "platform_metadata": {}},
        "content": {"title": "Tutorial: How I built a REST API in Python", "body": "I spent the weekend learning FastAPI and wanted to share what I learned. Here are the key concepts...", "media": [], "links": []},
        "context": {"channel": "r/community", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Genuine knowledge sharing, no commercial intent"
    }
  ]
}

EXAMPLE 2:
Rule: "Stay on topic. Pikmin Bloom posts only. This means that this is not the place for politics, religion, soap boxing of any kind. It's a game subreddit, for posts about people having fun with a game. Have fun, keep it light. Thank you. "
Output:
{
  "checklist_tree": [
    {
      "description": "Does the post or the comment contain political, religious, or soap-boxing content?",
      "rule_text_anchor": "This means that this is not the place for politics, religion, soap boxing of any kind",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Evaluate whether this post or comment is political, religious, or soap-boxing. Does it mainly serve to express strong opinions on political or religious topics, or to lecture or preach to others, rather than contribute useful information or discussion to the community?",
        "rubric": "Score higher (more likely to violate) when: (1) The post or comment promotes a political agenda (2) The post or comment promotes religious beliefs (3) The post or comment is preachy or lecturing",
        "threshold": 0.65,
        "examples_to_include": 5
      },
      "combine_mode": "all_must_pass",
      "weight": 1.0,
      "fail_action": "remove",
      "children": []
    },
    {
      "description": "Is the post or comment irrelevant to the Pikmin Bloom game?",
      "rule_text_anchor": "Pikmin Bloom posts only",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Evaluate whether this post or comment is relevant to the Pikmin Bloom game. Does the post or comment have anything to do with the Pikmin Bloom game?",
        "rubric": "Score high (more likely to violate) when: (1) The post or comment does not discuss the Pikmin Bloom game (2) The post or comment does not discuss any related topics",
        "threshold": 0.65,
        "examples_to_include": 5
      },
      "combine_mode": "all_must_pass",
      "weight": 1.0,
      "fail_action": "remove",
      "children": []
    }
  ],
  "examples": [
    {
      "label": "negative",
      "content": {
        "id": "example-3",
        "platform": "reddit",
        "author": {"username": "newuser", "account_age_days": 30, "platform_metadata": {}},
        "content": {"title": "I made a postcard with Pikmins marching for freedom", "body": "<a photo of Pikmin in front of the Capitol> Even Pikmins think the election was rigged!", "media": [], "links": []},
        "context": {"channel": "r/PikminBloomApp", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Political post"
    },
    {
      "label": "positive",
      "content": {
        "id": "example-4",
        "platform": "reddit",
        "author": {"username": "regularuser", "account_age_days": 200, "platform_metadata": {}},
        "content": {"title": " Greetings from the White House ", "body": "<a photo of Pikmin in front of the White House>", "media": [], "links": []},
        "context": {"channel": "r/PikminBloomApp", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": "Showcase", "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Although it mentions the White House, the post does not discuss any political agenda."
    }
  ]
}
"""


def build_compile_prompt(
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
    existing_checklist: Optional[list] = None,
    existing_examples: Optional[list] = None,
) -> str:
    existing_context = ""
    if existing_checklist:
        import json
        existing_context += f"\n\nExisting checklist (preserve user customizations where rule intent unchanged):\n{json.dumps(existing_checklist, indent=2)}"
    if existing_examples:
        import json
        existing_context += f"\n\nExisting examples:\n{json.dumps(existing_examples, indent=2)}"

    return f"""{COMPILE_FEW_SHOT_EXAMPLES}

Now compile the following rule for the "{community_name}" community on {platform}.

Community context (other rules, for background):
{other_rules_summary if other_rules_summary else "No other rules yet."}
{existing_context}

Rule to compile:
{rule_text}

Generate a checklist tree with 2-3 items (can have children), plus exactly 3 positive and 3 negative examples.

Return JSON in exactly this format:
{{
  "checklist_tree": [...],
  "examples": [
    {{
      "label": "positive" | "negative" | "borderline",
      "content": {{...post content object...}},
      "relevance_note": "Why this example relates to the rule"
    }}
  ]
}}"""


# ── Subjective Evaluation ──────────────────────────────────────────────────────

SUBJECTIVE_EVAL_SYSTEM = """You are a content moderation agent. Evaluate posts against the given criteria and return structured judgments.

For each item, assess whether the post passes or fails the criterion. Be consistent and calibrated — reserve high confidence for clear-cut cases.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_subjective_eval_prompt(
    post_content: dict,
    items_with_rubrics: list[dict],
    community_name: str,
    examples: list[dict],
) -> str:
    import json

    examples_str = ""
    if examples:
        examples_str = f"\n\nRelevant examples from this community:\n{json.dumps(examples[:10], indent=2)}"

    items_str = json.dumps(items_with_rubrics, indent=2)
    post_str = json.dumps(post_content, indent=2)

    return f"""Evaluate this post for the "{community_name}" community.

Post content:
{post_str}
{examples_str}

Evaluate the following checklist items:
{items_str}

For each item, return whether the post PASSES or FAILS the criterion:
- passes: true means the post is FINE (does not violate this criterion)
- passes: false means the post FAILS this criterion (violates the rule)
- confidence: 0.0 to 1.0 (how confident you are in this judgment)

Return JSON in exactly this format:
{{
  "results": [
    {{
      "item_id": "...",
      "passes": true | false,
      "confidence": 0.0-1.0,
      "reasoning": "Brief explanation of why the post passes or fails this criterion"
    }}
  ]
}}"""


# ── Community Norms ────────────────────────────────────────────────────────────

COMMUNITY_NORMS_SYSTEM = """You are a community culture evaluator. Your task is to assess whether a post "feels off" for a community even if it doesn't violate any explicit rule. This is a holistic judgment about cultural fit and community norms.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_community_norms_prompt(
    post_content: dict,
    community_name: str,
    rules_summary: str,
    recent_decisions: list[dict],
) -> str:
    import json

    decisions_str = ""
    if recent_decisions:
        decisions_str = f"\n\nRecent moderator decisions for context:\n{json.dumps(recent_decisions[:5], indent=2)}"

    post_str = json.dumps(post_content, indent=2)

    return f"""Assess whether this post fits the culture and norms of the "{community_name}" community, even if it doesn't violate explicit rules.

Community rules summary:
{rules_summary}
{decisions_str}

Post:
{post_str}

Consider:
1. Does this post fit the type of content this community normally discusses?
2. Does the tone match what's expected here?
3. Even if technically rule-compliant, does it feel like an attempt to game the rules?
4. Would long-time community members likely be bothered by this post?

Return JSON in exactly this format:
{{
  "violates_norms": true | false,
  "confidence": 0.0-1.0,
  "reasoning": "Explanation of why this post does or doesn't fit community norms"
}}"""


# ── Suggest from Examples ──────────────────────────────────────────────────────

SUGGEST_FROM_EXAMPLES_SYSTEM = """You are a moderation rule optimization assistant. Given a set of labeled examples and the current checklist, suggest improvements to better align the checklist with the examples.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_suggest_from_examples_prompt(
    rule_text: str,
    checklist_items: list[dict],
    examples: list[dict],
    community_name: str,
) -> str:
    import json

    return f"""Analyze these labeled examples for the "{community_name}" community and suggest improvements to the moderation checklist.

Rule text:
{rule_text}

Current checklist:
{json.dumps(checklist_items, indent=2)}

Labeled examples:
{json.dumps(examples, indent=2)}

Identify patterns where the checklist might be:
1. Missing criteria that distinguish positive from negative examples
2. Over-triggering (flagging positives as violations)
3. Under-triggering (missing clear violations)
4. Using thresholds or patterns that need adjustment

Return JSON in exactly this format:
{{
  "suggestions": [
    {{
      "suggestion_type": "checklist" | "rule_text",
      "target": "item_id or null for new items",
      "description": "What to change and why",
      "proposed_change": {{...the updated item or rule text snippet...}},
      "reasoning": "Which examples motivated this suggestion"
    }}
  ]
}}"""


# ── Suggest from Checklist ─────────────────────────────────────────────────────

SUGGEST_FROM_CHECKLIST_SYSTEM = """You are a moderation rule alignment assistant. Given changes to a checklist, suggest new examples that test the updated logic and optionally suggest rule text updates.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_suggest_from_checklist_prompt(
    rule_text: str,
    checklist_items: list[dict],
    existing_examples: list[dict],
    community_name: str,
) -> str:
    import json

    return f"""The moderation checklist for the "{community_name}" community has been updated. Generate new examples that test the updated logic, especially edge cases.

Rule text:
{rule_text}

Updated checklist:
{json.dumps(checklist_items, indent=2)}

Existing examples (do not duplicate these):
{json.dumps(existing_examples, indent=2)}

Generate examples that:
1. Test boundary cases near the new thresholds or patterns
2. Cover scenarios the existing examples don't address
3. Include both positive (rule-following) and negative (rule-violating) cases

Return JSON in exactly this format:
{{
  "suggested_examples": [
    {{
      "label": "positive" | "negative" | "borderline",
      "content": {{...normalized post content...}},
      "relevance_note": "What aspect of the updated checklist this example tests"
    }}
  ],
  "rule_text_suggestions": [
    {{
      "description": "Optional suggestion to update rule text if checklist has diverged",
      "proposed_text": "...",
      "reasoning": "..."
    }}
  ]
}}"""
