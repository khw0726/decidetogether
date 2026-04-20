"""All prompt templates for the AutoMod Agent compiler and evaluator."""

from typing import Any, Optional


# ── Triage ─────────────────────────────────────────────────────────────────────

TRIAGE_SYSTEM = """You are a moderation rule classifier. Your task is to classify community rules into one of four categories:

- **actionable**: Describes a SPECIFIC content standard that an automated system can evaluate against a post or comment. The system must be able to look at the content and decide "violates" or "does not violate". Examples: "No self-promotion or spam", "Be respectful to other members", "No NSFW content", "Include your age and gender in the title".
  NOT actionable: rules about what happens after a violation ("Repeat offenders will be banned"), tips for users ("Consider posting to another subreddit"), guidance that can't be checked per-post ("Use the report button"), or rules about moderator behavior.
- **procedural**: Describes moderator procedures, enforcement consequences, escalation paths, or user instructions that cannot be checked by looking at content. Examples: "Moderators may act with discretion", "Repeated offenses will result in a permanent ban", "Use the report button instead of engaging trolls", "Message the mods if your post gets caught in the spam filter".
- **meta**: Describes rule governance, scope, or applicability. Examples: "Rules are subject to change without notice", "These rules apply to all posts and comments".
- **informational**: Provides community context, tips, or encouragement but no enforceable standard. Examples: "This is a community for Python developers", "We welcome beginners", "Consider posting to /r/LegalAdviceUK for UK questions", "It's nice to say thank you to people who help you".

Additionally, classify what type of content the rule applies to:
- **posts**: Rule only applies to top-level submissions (e.g. title format, flair, link requirements)
- **comments**: Rule only applies to comments/replies (e.g. answer quality, reply etiquette)
- **both**: Rule applies to all content (e.g. civility, no personal info, no spam)

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_triage_prompt(rule_text: str, community_name: str, platform: str) -> str:
    return f"""Classify this community rule for the "{community_name}" community on {platform}.

Rule text: {rule_text}

Return JSON in exactly this format:
{{
  "rule_type": "actionable" | "procedural" | "meta" | "informational",
  "applies_to": "posts" | "comments" | "both",
  "reasoning": "One sentence explaining why this classification was chosen."
}}"""


# ── Compile ────────────────────────────────────────────────────────────────────

COMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to compile a moderator's \
natural-language rule into a precise, structured decision tree that an automated system can execute.

Each node in the tree is a YES/NO question where YES = a potential violation is detected.

Each checklist item must have:
- description: A short, concise yes/no question framed so that YES = violation signal (e.g. "Does the post contain spam keywords?")
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred). Keep the exact punctuation and wording. 
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema (see below)
- action: "remove", "flag", or "continue"
  - Leaf nodes (no children): use "remove" or "flag" to set the consequence. "continue" is not allowed for leaf nodes.
  - Non-leaf nodes (has children): MUST always be "continue". The verdict comes entirely from the children.
- children: Sub-items evaluated when this item says YES (empty list for leaf nodes)
- context_influenced: true if the community context (purpose, participants, stakes, tone) shaped how this item was framed, calibrated, or phrased. Set false when the item derives purely from the rule text.
- context_note: If context_influenced is true, a one-sentence explanation tracing the reasoning: "[situational fact] → [calibration decision]" (e.g. "Vulnerable population seeking crisis support → threshold lowered to 0.6 to catch dismissive comments that could cause real harm"). Set null otherwise.

COMMUNITY CONTEXT CALIBRATION:
When community context is provided, reason from the situation to your calibration choices:
- Read the PURPOSE to understand what "off-topic" or "low quality" means for this specific community.
- Read the PARTICIPANTS to understand who might be harmed and how — but also who might be discouraged by over-moderation.
- Read the STAKES to calibrate how aggressive vs. conservative your thresholds should be.
- Read the TONE to match rubric language and example tone to the community's actual communication style.
For every item where context shaped your choice, set context_influenced=true and write a context_note
that traces your reasoning: "[situational fact] → [calibration decision]".

Tree evaluation semantics:
- If an item says NO: no action, children are skipped.
- If an item says YES: apply its action, then evaluate children. Children can only escalate the verdict.
- Non-leaf action is always "continue" — children are the sole decision-makers.
- At every level, the worst action (REMOVE > FLAG > approve) wins across all siblings.
- Frame every question so YES = violation. Avoid "Does the post have X?" (where having X is good).
  Instead write "Does the post lack X?" or restructure using children.
- Use children to refine broad checks: e.g. a deterministic parent (fast gate) with a subjective child
  (confirms it's a real violation, not a false positive). Parent action = "continue" always.

Logic schemas:
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any"|"all", "negate": false, "field": "all"|"title"|"body"}
  - field: which part of the post to match against. "title" = title only, "body" = body/selftext only, "all" = title + body (default). Use "body" or "title" when the check is specifically about one field (e.g. "Is the body non-empty?").
  - negate=false: triggered when pattern IS found (e.g. spam keywords present)
  - negate=true: triggered when pattern is NOT found (e.g. required tag missing)
- structural: {"type": "structural", "checks": [{"field": "account_age_days"|"post_type"|"flair"|"karma", "operator": "<"|">"|"<="|">="|"=="|"!="|"in", "value": ...}], "match_mode": "all"|"any"}
  - triggered when the condition is true (e.g. account_age_days < 7 triggers for new accounts)
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Keep trees shallow (3 levels max). Generate one violating example and one borderline example per top-level checklist item. Borderline examples are posts that reasonable moderators might genuinely disagree on — they sit at the gray area of the rule.

For each example, include `related_checklist_item_description`: the exact description string of the checklist item this example is designed to trigger (for violating/borderline examples), or null for compliant examples.

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
      "action": "flag",
      "children": [],
      "context_influenced": false,
      "context_note": null
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
      "action": "flag",
      "children": [],
      "context_influenced": false,
      "context_note": null
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
      "action": "remove",
      "children": [],
      "context_influenced": false,
      "context_note": null
    },
    {
      "description": "Is the account new, which is a spam signal?",
      "rule_text_anchor": null,
      "item_type": "structural",
      "logic": {
        "type": "structural",
        "checks": [
          {"field": "account_age_days", "operator": "<", "value": 7}
        ],
        "match_mode": "all"
      },
      "action": "flag",
      "children": [],
      "context_influenced": false,
      "context_note": null
    }
  ],
  "examples": [
    {
      "label": "violating",
      "content": {
        "id": "example-1",
        "platform": "reddit",
        "author": {"username": "shopowner123", "account_age_days": 5, "platform_metadata": {}},
        "content": {"title": "Check out my new online store - 20% off this week!", "body": "Hi everyone! I just launched my store at myshop.com. Use code REDDIT20 for 20% off. Would love your feedback!", "media": [], "links": ["https://myshop.com"]},
        "context": {"channel": "r/community", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Clear self-promotion with discount code and external shop link",
      "related_checklist_item_description": "Does the content contain explicit promotional language or calls to action?"
    },
    {
      "label": "borderline",
      "content": {
        "id": "example-2b",
        "platform": "reddit",
        "author": {"username": "indie_dev", "account_age_days": 180, "platform_metadata": {}},
        "content": {"title": "I built a free tool that might help this community", "body": "After struggling with X myself, I spent a month building a small free tool. No monetization, just open source. Would love feedback if anyone finds it useful.", "media": [], "links": ["https://github.com/indie_dev/mytool"]},
        "context": {"channel": "r/community", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Shares a personal project but it's free/open-source and frames itself as community contribution — moderators might genuinely disagree on whether this crosses into self-promotion",
      "related_checklist_item_description": "Is this content primarily self-promotional, even without explicit keywords?"
    },
    {
      "label": "compliant",
      "content": {
        "id": "example-2c",
        "platform": "reddit",
        "author": {"username": "helpfuluser", "account_age_days": 365, "platform_metadata": {}},
        "content": {"title": "Tutorial: How I built a REST API in Python", "body": "I spent the weekend learning FastAPI and wanted to share what I learned. Here are the key concepts...", "media": [], "links": []},
        "context": {"channel": "r/community", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Genuine knowledge sharing, no commercial intent",
      "related_checklist_item_description": null
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
      "action": "remove",
      "children": [],
      "context_influenced": true,
      "context_note": "Community purpose is casual mobile gaming fun → threshold lowered to 0.55 because even mild soap-boxing undermines the lighthearted atmosphere."
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
      "action": "remove",
      "children": [],
      "context_influenced": false,
      "context_note": null
    }
  ],
  "examples": [
    {
      "label": "violating",
      "content": {
        "id": "example-3",
        "platform": "reddit",
        "author": {"username": "newuser", "account_age_days": 30, "platform_metadata": {}},
        "content": {"title": "I made a postcard with Pikmins marching for freedom", "body": "<a photo of Pikmin in front of the Capitol> Even Pikmins think the election was rigged!", "media": [], "links": []},
        "context": {"channel": "r/PikminBloomApp", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Political post",
      "related_checklist_item_description": "Does the post or the comment contain political, religious, or soap-boxing content?"
    },
    {
      "label": "borderline",
      "content": {
        "id": "example-4b",
        "platform": "reddit",
        "author": {"username": "casualplayer", "account_age_days": 90, "platform_metadata": {}},
        "content": {"title": "Anyone else feel like the new update is ruining the game?", "body": "I know this is just a game but the devs really seem to not care about the community anymore. It's frustrating and kind of insulting honestly.", "media": [], "links": []},
        "context": {"channel": "r/PikminBloomApp", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": null, "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Game-related but venting/negative in tone — could be borderline soap-boxing depending on how strictly the community enforces the 'keep it light' atmosphere",
      "related_checklist_item_description": "Does the post or the comment contain political, religious, or soap-boxing content?"
    },
    {
      "label": "compliant",
      "content": {
        "id": "example-4c",
        "platform": "reddit",
        "author": {"username": "regularuser", "account_age_days": 200, "platform_metadata": {}},
        "content": {"title": " Greetings from the White House ", "body": "<a photo of Pikmin in front of the White House>", "media": [], "links": []},
        "context": {"channel": "r/PikminBloomApp", "thread_id": null, "parent_post_id": null, "post_type": "self", "flair": "Showcase", "platform_metadata": {}},
        "timestamp": "2026-01-01T00:00:00Z"
      },
      "relevance_note": "Although it mentions the White House, the post does not discuss any political agenda.",
      "related_checklist_item_description": null
    }
  ]
}
"""


def build_compile_prompt(
    rule_title: str,
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
    existing_checklist: Optional[list] = None,
    existing_examples: Optional[list] = None,
    community_atmosphere: Optional[dict] = None,
    community_context: Optional[dict] = None,
    community_posts_sample: Optional[list] = None,
) -> str:
    import json

    existing_context = ""
    if existing_checklist:
        existing_context += f"\n\nExisting checklist (preserve user customizations where rule intent unchanged):\n{json.dumps(existing_checklist, indent=2)}"
    if existing_examples:
        existing_context += f"\n\nExisting examples:\n{json.dumps(existing_examples, indent=2)}"

    context_section = ""
    if community_context:
        ctx_lines = []
        for dim, label in [("purpose", "PURPOSE"), ("participants", "PARTICIPANTS"),
                           ("stakes", "STAKES"), ("tone", "TONE")]:
            d = community_context.get(dim, {})
            prose = d.get("prose", "")
            tags = d.get("tags", [])
            if prose:
                ctx_lines.append(f"  {label}:")
                ctx_lines.append(f"    {prose}")
                if tags:
                    ctx_lines.append(f"    [Tags: {', '.join(tags)}]")
        if ctx_lines:
            context_section = "\n\nCommunity context for calibration:\n" + "\n".join(ctx_lines)

    atmosphere_section = ""
    if community_atmosphere:
        atm = community_atmosphere
        lines = []
        if atm.get("tone"):
            lines.append(f"  Tone: {atm['tone']}")
        if atm.get("typical_content"):
            lines.append(f"  Typical content: {atm['typical_content']}")
        if atm.get("what_belongs"):
            lines.append(f"  What belongs: {atm['what_belongs']}")
        if atm.get("what_doesnt_belong"):
            lines.append(f"  What doesn't belong: {atm['what_doesnt_belong']}")
        if atm.get("moderation_style"):
            lines.append(f"  Moderation style: {atm['moderation_style']}")
        atmosphere_section = "\n\nCommunication patterns (auto-inferred):\n" + "\n".join(lines)

    posts_section = ""
    if community_posts_sample:
        acceptable = [p for p in community_posts_sample if p.get("label") == "acceptable"]
        unacceptable = [p for p in community_posts_sample if p.get("label") == "unacceptable"]
        parts = []
        if acceptable:
            snippets = []
            for p in acceptable[:4]:
                c = p.get("content", {}).get("content", {})
                title = c.get("title", "")
                body = (c.get("body", "") or "")
                note = p.get("note", "")
                snippets.append(f'    - "{title}" — {body}' + (f' [{note}]' if note else ''))
            parts.append("  Acceptable posts:\n" + "\n".join(snippets))
        if unacceptable:
            snippets = []
            for p in unacceptable[:4]:
                c = p.get("content", {}).get("content", {})
                title = c.get("title", "")
                body = (c.get("body", "") or "")
                note = p.get("note", "")
                snippets.append(f'    - "{title}" — {body}' + (f' [{note}]' if note else ''))
            parts.append("  Removed/unacceptable posts:\n" + "\n".join(snippets))
        if parts:
            posts_section = (
                "\n\nRepresentative community posts (use these to calibrate subjective rubric "
                "language/thresholds and to generate borderline examples realistic to this community's "
                "actual content style):\n" + "\n".join(parts)
            )

    return f"""{COMPILE_FEW_SHOT_EXAMPLES}

Now compile the following rule for the "{community_name}" community on {platform}.

Community context (other rules, for background):
{other_rules_summary if other_rules_summary else "No other rules yet."}{context_section}{atmosphere_section}{posts_section}
{existing_context}

Rule to compile:
{rule_title}: {rule_text}

Generate a minimal checklist tree — only as many items as the rule genuinely requires, no more. Simple rules need one item; complex rules may need several. Do not pad with redundant or overlapping items. For each item, provide one violating example and one borderline example.

Return JSON in exactly this format:
{{
  "checklist_tree": [
    {{
      "description": "...",
      "rule_text_anchor": "...",
      "item_type": "...",
      "logic": {{}},
      "action": "...",
      "children": [],
      "context_influenced": true | false,
      "context_note": "[situational fact] → [calibration decision], or null"
    }}
  ],
  "examples": [
    {{
      "label": "compliant" | "violating" | "borderline",
      "content": {{
        "id": "...",
        "platform": "...",
        "author": "...",
        "content": {{"title": "...", "body": "...", "media": [], "links": []}},
        "context": "...",
        "timestamp": "..."
      }},
      "relevance_note": "Why this example relates to the rule",
      "related_checklist_item_description": "Exact description of the checklist item this example primarily tests, or null for compliant examples"
    }}
  ]
}}"""


# ── Subjective Evaluation ──────────────────────────────────────────────────────

SUBJECTIVE_EVAL_SYSTEM = """You are a content moderation agent. Evaluate posts against the given criteria and return structured judgments.

For each item, assess whether the post passes or fails the criterion. Be consistent and calibrated — reserve high confidence for clear-cut cases.

When thread context is provided (the original post and/or parent comments), use it to understand the conversation flow. A comment may only make sense — or only violate a rule — in the context of what it's replying to. Evaluate the TARGET content, not the thread context itself.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def _render_thread_context(post_content: dict) -> str:
    """Render thread context (OP + parent comments) if present in the post."""
    import json

    thread_context = post_content.get("thread_context", [])
    if not thread_context:
        return ""

    parts = ["\n\n--- THREAD CONTEXT (for understanding the conversation — evaluate only the TARGET content above) ---"]
    for item in thread_context:
        role = item.get("role", "unknown")
        author = item.get("author", "unknown")
        content = item.get("content", {})
        title = content.get("title", "")
        body = content.get("body", "")
        depth = item.get("depth", 0)

        indent = "  " * depth
        label = {"op": "ORIGINAL POST", "parent_comment": "PARENT COMMENT", "ancestor_comment": "ANCESTOR COMMENT"}.get(role, role.upper())
        parts.append(f"\n{indent}[{label}] by u/{author}:")
        if title:
            parts.append(f"{indent}  Title: {title}")
        if body:
            # Truncate very long context to keep prompts reasonable
            display_body = body[:1500] + "..." if len(body) > 1500 else body
            parts.append(f"{indent}  {display_body}")

    parts.append("\n--- END THREAD CONTEXT ---")
    return "\n".join(parts)


def build_subjective_eval_prompt(
    post_content: dict,
    items_with_rubrics: list[dict],
    community_name: str,
    examples: list[dict],
    borderline_examples: list[dict] | None = None,
) -> str:
    import json

    examples_str = ""
    if examples:
        examples_str = f"\n\nClear community examples (compliant/violating — use for calibration):\n{json.dumps(examples[:4], indent=2)}"
    if borderline_examples:
        examples_str += f"\n\nBorderline calibration examples (reasonable moderators disagree on these — use to understand edge cases):\n{json.dumps(borderline_examples[:8], indent=2)}"

    items_str = json.dumps(items_with_rubrics, indent=2)

    # Render post without thread_context in the main JSON (it's shown separately for clarity)
    display_post = {k: v for k, v in post_content.items() if k != "thread_context"}
    post_str = json.dumps(display_post, indent=2)

    thread_context_str = _render_thread_context(post_content)

    is_comment = bool(post_content.get("thread_context"))
    content_label = "Comment" if is_comment else "Post"

    return f"""Evaluate this {content_label.lower()} for the "{community_name}" community.

{content_label} to evaluate (TARGET):
{post_str}
{thread_context_str}
{examples_str}

Evaluate the following checklist items. Each item is a yes/no question where YES = violation detected.

For each item:
- triggered: true means YES, the violation described by the question IS present
- triggered: false means NO, the {content_label.lower()} is fine for this criterion
- confidence: 0.0 to 1.0 (how confident you are in this judgment)
{"- IMPORTANT: Consider the thread context when evaluating. A comment's meaning, tone, and relevance depend on what it's replying to." if is_comment else ""}

Return JSON in exactly this format:
{{
  "results": [
    {{
      "item_id": "...",
      "triggered": true | false,
      "confidence": 0.0-1.0,
      "reasoning": "Brief explanation of why the violation is or is not present"
    }}
  ]
}}

Items to evaluate:
{items_str}"""


# ── Single Item Inference ──────────────────────────────────────────────────────

INFER_ITEM_SYSTEM = """You are a moderation rule compiler. Given a checklist item description (a yes/no question where YES = violation), classify it and generate the appropriate logic JSON.

Item types:
- **deterministic**: Can be evaluated with regex pattern matching against post text. Use when the violation is detectable by specific words, phrases, URLs, or formatting patterns.
- **structural**: Can be evaluated against post metadata fields (account_age_days, karma, post_type, flair, etc.). Use when the violation depends on who posted or how, not what they wrote.
- **subjective**: Requires LLM judgment. Use when detecting the violation requires understanding context, intent, or nuance that patterns can't capture.

Logic schemas:
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any", "negate": false, "field": "all"|"title"|"body"}
  - field: "title" = title only, "body" = body only, "all" = title + body (default). Use "body" or "title" when the check targets a specific field.
- structural: {"type": "structural", "checks": [{"field": "...", "operator": "<|>|==|!=|<=|>=", "value": ...}], "match_mode": "all"}
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Note: the `action` field is NOT part of this inference — it is provided separately by the user.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_infer_item_prompt(
    description: str,
    rule_text: Optional[str] = None,
    community_name: str = "",
    existing_items: Optional[list[dict]] = None,
) -> str:
    parts = []
    if community_name:
        parts.append(f"Community: {community_name}")
    if rule_text:
        parts.append(f"Rule this item belongs to:\n{rule_text}")
    if existing_items:
        items_str = "\n".join(
            f"- [{item['item_type']}] {item['description']}"
            for item in existing_items
        )
        parts.append(f"Existing checklist items for this rule (for context, avoid duplication):\n{items_str}")
    parts.append(f"New item description: {description}")
    parts.append("Classify this item and generate the logic JSON.")
    return "\n\n".join(parts)


# ── Community Norms ────────────────────────────────────────────────────────────

COMMUNITY_NORMS_SYSTEM = """You are a community culture evaluator. Your task is to assess whether a post "feels off" for a community even if it doesn't violate any explicit rule. This is a holistic judgment about cultural fit and community norms.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_community_norms_prompt(
    post_content: dict,
    community_name: str,
    rules_summary: str,
    recent_decisions: list[dict],
    community_atmosphere: Optional[dict] = None,
    community_context: Optional[dict] = None,
) -> str:
    import json

    decisions_str = ""
    if recent_decisions:
        decisions_str = f"\n\nRecent moderator decisions for context:\n{json.dumps(recent_decisions[:5], indent=2)}"

    # Render community context if available
    context_str = ""
    if community_context:
        ctx_lines = []
        for dim, label in [("purpose", "Purpose"), ("participants", "Participants"),
                           ("stakes", "Stakes"), ("tone", "Tone")]:
            d = community_context.get(dim, {})
            prose = d.get("prose", "")
            if prose:
                ctx_lines.append(f"  {label}: {prose}")
        if ctx_lines:
            context_str = "\n\nCommunity context:\n" + "\n".join(ctx_lines)

    # Render post without thread_context in main JSON
    display_post = {k: v for k, v in post_content.items() if k != "thread_context"}
    post_str = json.dumps(display_post, indent=2)

    thread_context_str = _render_thread_context(post_content)

    is_comment = bool(post_content.get("thread_context"))
    content_label = "comment" if is_comment else "post"

    return f"""Assess whether this {content_label} fits the culture and norms of the "{community_name}" community, even if it doesn't violate explicit rules.

Community rules summary:
{rules_summary}
{decisions_str}
{context_str}

{"Community atmosphere:" if community_atmosphere else ""}
{json.dumps(community_atmosphere, indent=2) if community_atmosphere else ""}

{content_label.capitalize()} to evaluate:
{post_str}
{thread_context_str}

Consider:
1. Does this {content_label} fit the type of content this community normally discusses?
2. Does the tone match what's expected here?
3. Even if technically rule-compliant, does it feel like an attempt to game the rules?
4. Would long-time community members likely be bothered by this {content_label}?
{"5. In the context of the thread, is this comment derailing, trolling, or responding in bad faith?" if is_comment else ""}

Return JSON in exactly this format:
{{
  "violates_norms": true | false,
  "confidence": 0.0-1.0,
  "reasoning": "Explanation of why this {content_label} does or doesn't fit community norms"
}}"""


# ── Community Atmosphere Generation ───────────────────────────────────────────

GENERATE_ATMOSPHERE_SYSTEM = """You are a community culture analyst. Given a sample of posts from a community (labeled as acceptable or removed/unacceptable), infer the community's atmosphere, tone, and norms.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_generate_atmosphere_prompt(
    community_name: str,
    platform: str,
    acceptable_posts: list[dict],
    unacceptable_posts: list[dict],
    rules_summary: Optional[str] = None,
) -> str:
    import json

    rules_section = ""
    if rules_summary:
        rules_section = f"\nCommunity rules (for context on what the community explicitly enforces):\n{rules_summary}\n"

    return f"""Analyze these sample posts from the "{community_name}" community on {platform} and infer the community's atmosphere and norms.
{rules_section}
Acceptable posts (approved by moderators or marked as good examples):
{json.dumps(acceptable_posts, indent=2)}

Removed/unacceptable posts (removed by moderators or marked as bad examples):
{json.dumps(unacceptable_posts, indent=2)}

Based on the rules and post samples, characterize the community's culture and moderation standards. Go beyond what the rules literally say — infer tone, style, and the unwritten norms that the post samples reveal.

Return JSON in exactly this format:
{{
  "tone": "Short description of the community's tone and vibe (e.g. 'casual, wholesome, family-friendly')",
  "typical_content": "What kinds of posts are typical and welcome here",
  "what_belongs": "A sentence describing what content fits this community",
  "what_doesnt_belong": "A sentence describing what content doesn't fit, even if not explicitly rule-violating",
  "moderation_style": "How strict or lenient moderators tend to be, and what they prioritize"
}}"""


# ── Community Context Generation ─────────────────────────────────────────────

GENERATE_CONTEXT_SYSTEM = """You are a community culture analyst. Given a community's metadata and \
a representative sample of its actual posts and comments, generate a structured community context profile.

Your analysis should reflect OBSERVED BEHAVIOR — what the community actually does — not just what its \
sidebar says. Use the sampled posts and comments as primary evidence for tone and stakes.

For each of the four dimensions (purpose, participants, stakes, tone), produce:
1. A prose description (2-3 sentences) grounded in evidence from the posts
2. Categorical tags from the provided taxonomy (3-5 per dimension)

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_generate_context_prompt(
    community_name: str,
    platform: str,
    description: str,
    rules_summary: str,
    subscribers: Optional[int] = None,
    sampled_posts: Optional[dict[str, list[dict]]] = None,
    taxonomy: Optional[dict] = None,
) -> str:
    import json

    meta_parts = [f"Name: r/{community_name}" if platform == "reddit" else f"Name: {community_name}",
                  f"Platform: {platform}"]
    if subscribers:
        meta_parts.append(f"Subscribers: {subscribers:,}")
    meta_section = " | ".join(meta_parts)

    posts_section = ""
    if sampled_posts:
        parts = []
        for category, label in [
            ("hot", "Hot posts (current front page — typical day-to-day content)"),
            ("top", "Celebrated posts (top of last month — what gets especially rewarded)"),
            ("controversial", "Controversial posts (last month — where norms are contested)"),
            ("ignored", "Ignored posts (score ≤ 1, at least 12h old — unwelcome but not rule-breaking)"),
            ("comments", "Top comments (from popular threads — actual language and tone)"),
        ]:
            items = sampled_posts.get(category, [])
            if not items:
                continue
            snippets = []
            for p in items:
                if category == "comments":
                    body = p.get("body", "")[:300]
                    score = p.get("score", "?")
                    snippets.append(f"    [{score} pts] {body}")
                else:
                    title = p.get("title", "")
                    body = (p.get("body", "") or "")[:200]
                    score = p.get("score", "?")
                    comments = p.get("num_comments", "?")
                    ratio = p.get("upvote_ratio")
                    ratio_str = f", {ratio:.0%} upvoted" if ratio is not None else ""
                    snippet = f"    [{score} pts, {comments} comments{ratio_str}] {title}"
                    if body:
                        snippet += f" — {body}"
                    snippets.append(snippet)
            parts.append(f"  {label}:\n" + "\n".join(snippets))
        if parts:
            posts_section = "\n\nSAMPLED POSTS — What the community actually does:\n\n" + "\n\n".join(parts)

    taxonomy_section = ""
    if taxonomy:
        parts = []
        for dim in ["purpose", "participants", "stakes", "tone"]:
            cats = taxonomy.get(dim, {})
            tag_list = ", ".join(cats.keys())
            parts.append(f"  {dim.upper()}: {tag_list}")
        taxonomy_section = "\n\nAVAILABLE TAXONOMY TAGS (pick 3-5 per dimension from these):\n" + "\n".join(parts)

    return f"""Analyze the community "{community_name}" on {platform} and generate a structured context profile.

COMMUNITY METADATA:
  {meta_section}
  Description: {description or '(none)'}
  Rules: {rules_summary or '(none)'}
{posts_section}
{taxonomy_section}

Based on ALL of the above, generate community context.
For each dimension, write prose that reflects observed behavior (not just stated rules),
then assign tags from the taxonomy.

Pay special attention to:
- TONE: Use the actual comments from hot posts to characterize everyday language, humor, formality — not the sidebar's aspirations
- STAKES: Use the contrast between hot/celebrated and ignored posts to identify what actually matters here
- The gap between stated rules and actual behavior (e.g. rules say "be civil" but hot post comments are savage)

Return JSON in exactly this format:
{{
  "purpose": {{
    "prose": "2-3 sentences describing what this community is for, grounded in observed post content",
    "tags": ["tag1", "tag2", "tag3"]
  }},
  "participants": {{
    "prose": "2-3 sentences describing who participates and their characteristics",
    "tags": ["tag1", "tag2", "tag3"]
  }},
  "stakes": {{
    "prose": "2-3 sentences describing what could go wrong with harmful content OR over-moderation",
    "tags": ["tag1", "tag2", "tag3"]
  }},
  "tone": {{
    "prose": "2-3 sentences describing actual communication style based on observed posts/comments",
    "tags": ["tag1", "tag2", "tag3"]
  }}
}}"""


# ── Recompile (diff) ───────────────────────────────────────────────────────────

RECOMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to update an existing \
checklist tree to reflect changes to the rule text, while preserving as much of the existing structure as possible.

You will be given:
- The updated rule text
- The existing checklist items (each with an id, description, rule_text_anchor, and other fields)

For each existing item, decide:
- "keep": The rule text change does not affect this item. Return it unchanged.
- "update": The item still applies but needs field changes (description, logic, action, etc.).
- "delete": The rule text change makes this item obsolete or incorrect.

You may also emit:
- "add": A brand new item required by the updated rule text that has no equivalent in the existing checklist.

Guidelines:
- Use rule_text_anchor as the primary signal. If the anchor phrase still appears in the updated rule text \
(even if reworded), prefer "keep" or "update" over "delete"+"add".
- Only "delete" an item when the concept it checks is genuinely gone from the rule.
- Only "add" when the rule text introduces a brand-new concept with no equivalent in any existing item. \
Do NOT add items to "improve" or "complete" the checklist — your job is strictly to reflect the diff.
- If the updated rule text is shorter or simpler than before, expect mostly "keep"/"delete" ops and zero or very few "add" ops.
- Prefer fewer operations. When in doubt, keep an existing item rather than replacing it.
- Preserve existing items' ids exactly — do not invent new ids for updated items.
- Children of kept/updated items are handled inline — include them under "children" as before.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_recompile_prompt(
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
    existing_items: list,
) -> str:
    import json

    return f"""Update the checklist tree for the "{community_name}" community on {platform} to reflect the updated rule text.

Community context (other rules, for background):
{other_rules_summary if other_rules_summary else "No other rules yet."}

Existing checklist items (with ids):
{json.dumps(existing_items, indent=2)}

Updated rule text:
{rule_text}

Return JSON in exactly this format:
{{
  "operations": [
    {{"op": "keep", "existing_id": "..."}},
    {{"op": "update", "existing_id": "...", "description": "...", "rule_text_anchor": "...", "item_type": "...", "logic": {{}}, "action": "...", "children": []}},
    {{"op": "delete", "existing_id": "..."}},
    {{"op": "add", "description": "...", "rule_text_anchor": "...", "item_type": "...", "logic": {{}}, "action": "...", "children": []}}
  ]
}}"""


# ── Suggest from Examples ──────────────────────────────────────────────────────

SUGGEST_FROM_EXAMPLES_SYSTEM = """You are a moderation rule optimization assistant. Given a set of labeled examples and the current checklist, suggest improvements to better align the checklist with the examples.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_suggest_from_examples_prompt(
    rule_text: str,
    checklist_items: list[dict],
    examples: list[dict],
    community_name: str,
    violating_counts: dict[str, int] | None = None,
) -> str:
    import json

    # Annotate each item with its violating example count
    annotated_items = []
    for item in checklist_items:
        annotated = dict(item)
        if violating_counts is not None:
            annotated["violating_example_count"] = violating_counts.get(item.get("id", ""), 0)
        annotated_items.append(annotated)

    return f"""Analyze these labeled examples for the "{community_name}" community and suggest improvements to the moderation checklist.

Rule text:
{rule_text}

Current checklist (each item includes violating_example_count — the number of violating examples linked to it):
{json.dumps(annotated_items, indent=2)}

Labeled examples:
{json.dumps(examples, indent=2)}

Identify patterns where the checklist might be:
1. Missing criteria that distinguish compliant from violating examples
2. Over-triggering (flagging compliant posts as violations)
3. Under-triggering (missing clear violations)
4. Using thresholds or patterns that need adjustment

Items with a parent_id are children of another item. Items with action "continue" are parent nodes whose children contain the actual checks. You can suggest adding a new child item under an existing parent by setting target to null and parent_id to the parent's ID.

For deterministic items: only propose a pattern update if the item's violating_example_count >= 3. When that threshold is met, analyze the literal text of those violating examples and propose a refined regex pattern in proposed_change (under the "patterns" key) that matches them but not the compliant examples.

For each suggestion:
- If suggestion_type is "rule_text": set proposed_text to the COMPLETE updated rule text (all paragraphs preserved — only modify the specific sentences that need changing, leave the rest intact). Do NOT return a partial snippet.
- If suggestion_type is "checklist" and target is an item ID: set proposed_change to the fields to update on that item.
- If suggestion_type is "checklist" and target is null: set proposed_change to the new item object. Set parent_id to an existing item's ID to add it as a child, or null for a new root item.

Return JSON in exactly this format:
{{
  "suggestions": [
    {{
      "suggestion_type": "checklist" | "rule_text",
      "target": "item_id to update, or null for new items / rule_text",
      "parent_id": "parent item_id for new child items, or null",
      "description": "What to change and why",
      "proposed_text": "For rule_text: the COMPLETE updated rule text",
      "proposed_change": {{...for checklist: the item fields...}},
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
3. Include both compliant (rule-following) and violating (triggering checklist) cases

For rule_text_suggestions: proposed_text must be the COMPLETE updated rule text (all paragraphs, not a snippet — only modify the specific sentences that need changing, leave the rest intact).

Return JSON in exactly this format:
{{
  "suggested_examples": [
    {{
      "label": "compliant" | "violating" | "borderline",
      "content": {{
        "id": "...",
        "platform": "...",
        "author": "...",
        "content": {{"title": "...", "body": "...", "media": [], "links": []}},
        "context": "...",
        "timestamp": "..."
      }},
      "relevance_note": "What aspect of the updated checklist this example tests",
      "related_checklist_item_description": "Exact description of the checklist item this example primarily tests (null if it spans multiple items)"
    }}
  ],
  "rule_text_suggestions": [
    {{
      "description": "Optional suggestion to update rule text if checklist has diverged",
      "proposed_text": "Complete updated rule text here, not a snippet",
      "reasoning": "..."
    }}
  ]
}}"""


# ── Fill Missing Examples ──────────────────────────────────────────────────────

FILL_EXAMPLES_SYSTEM = """You are a content moderation testing specialist. Generate realistic post examples that clearly trigger specific moderation checklist items.

For each checklist item provided, generate exactly one violating example — a post that clearly and unambiguously triggers that specific item --- and one borderline example — a post that is on the edge of triggering the item. Make examples realistic and specific enough to be useful test cases.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_fill_examples_prompt(
    rule_text: str,
    community_name: str,
    platform: str,
    items_needing_examples: list[dict],
    existing_examples: Optional[list[dict]] = None,
) -> str:
    import json

    existing_str = ""
    if existing_examples:
        existing_str = f"\n\nExisting examples (do not duplicate these):\n{json.dumps(existing_examples, indent=2)}"

    return f"""Generate one violating example for each of the following checklist items from the "{community_name}" community on {platform}.

Rule text:
{rule_text}

Checklist items that need a violating example:
{json.dumps(items_needing_examples, indent=2)}
{existing_str}

Generate exactly one violating post per item. Set related_checklist_item_description to the exact description of the item it triggers.

Return JSON in exactly this format:
{{
  "examples": [
    {{
      "label": "violating",
      "content": {{
        "id": "...",
        "platform": "...",
        "author": "...",
        "content": {{"title": "...", "body": "...", "media": [], "links": []}},
        "context": "...",
        "timestamp": "..."
      }},
      "relevance_note": "Why this example triggers the checklist item",
      "related_checklist_item_description": "Exact description of the checklist item this triggers"
    }},
    {{
      "label": "borderline",
      "content": {{
        "id": "...",
        "platform": "...",
        "author": "...",
        "content": {{"title": "...", "body": "...", "media": [], "links": []}},
        "context": "...",
        "timestamp": "..."
      }},
      "relevance_note": "Why this example triggers the checklist item",
      "related_checklist_item_description": "Exact description of the checklist item this triggers"
    }}
  ]
}}"""


# ── Diagnose Rule Health ────────────────────────────────────────────────────────

DIAGNOSE_HEALTH_SYSTEM = """You are a moderation rule health analyst. You will be given a community rule, its checklist items, and accumulated performance metrics from moderator decisions (false positive rates, false negative rates, confidence distributions, and example posts).

Your job is to diagnose which specific problem each underperforming item has and propose the minimal, targeted fix.

Five possible diagnoses:
- **tighten_rubric**: The rubric description is too vague — the model is guessing and making inconsistent calls. Fix: rewrite the rubric to be more precise and unambiguous.
- **adjust_threshold**: The rubric logic is correct but the sensitivity is miscalibrated. Fix: raise or lower the threshold value in the logic field. You MUST include the specific new threshold value (e.g., change threshold from 0.6 → 0.75).
- **promote_to_deterministic**: Clear text patterns have emerged that a regex can catch reliably — no LLM needed. Fix: change item_type to "deterministic" and add regex patterns.
- **split_item**: One item is trying to evaluate two different things, causing confusion. Fix: propose splitting into two focused items.
- **add_item** (new_items only): Violations exist that aren't covered by any current item. Fix: add a new checklist item.

Diagnosis rules:
- Only diagnose items with decision_count ≥ 3 and fp_rate > 0.15 OR fn_rate > 0.15 (unless uncovered violations force an add_item).
- Items with both low FP and FN rates are healthy — skip them entirely.
- For threshold adjustments: high confidence errors (avg_confidence_errors > 0.70) with fp_rate > 0.20 → threshold too low (raise it). Low confidence errors (avg_confidence_errors < 0.60) with fn_rate > 0.20 → rubric ambiguous (tighten_rubric instead).
- One diagnosis per item maximum. Choose the single most impactful fix.
- proposed_change must contain all fields you want to update on the existing item. Omit fields you don't want to change.
- **IMPORTANT**: Only include `"children"` in proposed_change when the fix structurally restructures child items (e.g. split_item). For tighten_rubric, adjust_threshold, and promote_to_deterministic, do NOT include `"children"` — the existing children will be preserved automatically.
- For split_item: proposed_change represents the first (updated) item. Add the second item to new_items with `"split_from": "<item_id>"` so the system can merge both into a single atomic fix.
- Skip items with decision_count < 3.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_diagnose_health_prompt(
    rule_text: str,
    checklist_items: list[dict],
    health_data: dict,
) -> str:
    import json

    items_section = []
    item_metrics_by_id = {m["item_id"]: m for m in health_data.get("items", [])}

    for item in checklist_items:
        metrics = item_metrics_by_id.get(item["id"], {})
        examples = metrics.get("examples", {})

        # Build compact example list (max 5 per item)
        example_rows = []
        for label in ("violating", "compliant", "borderline"):
            for ex in (examples.get(label) or [])[:2]:
                example_rows.append(f"  [{label.upper()}] {ex.get('title', '(no title)')}")

        fp_rate = metrics.get("false_positive_rate", 0.0)
        fn_rate = metrics.get("false_negative_rate", 0.0)
        fp_count = metrics.get("false_positive_count", 0)
        fn_count = metrics.get("false_negative_count", 0)
        total = metrics.get("decision_count", 0)
        avg_conf_correct = metrics.get("avg_confidence_correct")
        avg_conf_errors = metrics.get("avg_confidence_errors")

        item_block = {
            "id": item["id"],
            "description": item["description"],
            "item_type": item["item_type"],
            "action": item["action"],
            "logic": item.get("logic", {}),
            "metrics": {
                "decision_count": total,
                "fp_rate": round(fp_rate, 3),
                "fp_count": fp_count,
                "fn_rate": round(fn_rate, 3),
                "fn_count": fn_count,
                "avg_confidence_correct": round(avg_conf_correct, 3) if avg_conf_correct is not None else None,
                "avg_confidence_errors": round(avg_conf_errors, 3) if avg_conf_errors is not None else None,
            },
            "examples": example_rows,
        }
        items_section.append(item_block)

    overall = health_data.get("overall", {})
    uncovered = health_data.get("uncovered_violations", [])

    return f"""Analyze the health of this rule's checklist and diagnose which items need fixing.

Rule text:
{rule_text}

Overall stats: {overall.get("total_decisions", 0)} decisions, {overall.get("override_rate", 0.0):.1%} override rate

Checklist items with performance metrics:
{json.dumps(items_section, indent=2)}

Uncovered violations (removed by moderators but match no checklist item):
{json.dumps([u.get("title", "") for u in uncovered[:8]], indent=2) if uncovered else "None"}

Return JSON in exactly this format:
{{
  "diagnoses": [
    {{
      "item_id": "<exact item id from above>",
      "action": "tighten_rubric | adjust_threshold | promote_to_deterministic | split_item",
      "reasoning": "Concise explanation of what the metrics reveal and why this fix addresses it",
      "proposed_change": {{
        "description": "...",
        "item_type": "deterministic | structural | subjective",
        "logic": {{}},
        "action": "remove | flag | continue"
      }},
      "confidence": "high | medium | low"
    }}
  ],
  "new_items": [
    {{
      "action": "add_item",
      "reasoning": "What pattern the uncovered violations share",
      "proposed_item": {{
        "description": "...",
        "item_type": "deterministic | structural | subjective",
        "logic": {{}},
        "action": "remove | flag | continue",
        "rule_text_anchor": null,
        "context_influenced": false,
        "context_note": null,
        "children": []
      }},
      "motivated_by": ["<example_id>"]
    }}
  ]
}}"""


# ── Synthesize Rule from Overrides ─────────────────────────────────────────────

SYNTHESIZE_RULE_SYSTEM = """You are a community moderation rule author. You will be given posts that moderators removed or flagged even though no existing rule covered them. Your job is to infer the underlying community norm and articulate it as a clear, enforceable rule.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_synthesize_rule_prompt(
    examples: list[dict],
    community_name: str,
    platform: str,
) -> str:
    import json

    return f"""These posts were manually removed or flagged by moderators of the "{community_name}" community on {platform}, but no existing rule matched them. Identify the common pattern and write a new rule that would cover it.

Moderator override examples (posts removed without a matching rule):
{json.dumps(examples, indent=2)}

Identify what these posts have in common that warranted moderation. If the examples are too varied or the pattern is unclear, still provide a best-guess rule but set confidence to "low".

Return JSON in exactly this format:
{{
  "title": "Short rule title (≤ 10 words)",
  "text": "Full rule text as it would appear in the community rules",
  "confidence": "low" | "medium" | "high",
  "reasoning": "Brief explanation of the inferred pattern and what the examples have in common"
}}"""


# ── Link violations to checklist items ────────────────────────────────────────

LINK_VIOLATIONS_SYSTEM = """You are a moderation analysis assistant. Your task is to match violating examples (posts that moderators removed) to the checklist items they violate.

Each checklist item describes a specific check the moderation system performs. Each violation is a post that was removed by a moderator but is not yet linked to any checklist item.

For each violation, determine which checklist item (if any) it most closely matches. A violation matches a checklist item if the reason the post was removed aligns with what the checklist item checks for.

Only propose a link if you are reasonably confident the violation is caught by that checklist item. If a violation doesn't clearly match any item, omit it."""


def build_link_violations_prompt(
    rule_text: str,
    checklist_items: list[dict],
    violations: list[dict],
) -> str:
    import json

    items_str = json.dumps(checklist_items, indent=2)
    violations_str = json.dumps(violations, indent=2)

    return f"""Rule text: {rule_text}

Current checklist items:
{items_str}

Uncovered violations (removed by moderators, not yet linked to any checklist item):
{violations_str}

For each violation that matches a checklist item, return the link. Only include confident matches."""
