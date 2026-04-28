"""All prompt templates for the AutoMod Agent compiler and evaluator."""

from typing import Any, Optional


def _extract_note(note) -> tuple[str, str]:
    """Extract (text, tag) from a note in either old (str) or new (dict) format."""
    if isinstance(note, dict):
        return note.get("text", ""), note.get("tag", "")
    return str(note), ""


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
- description: A terse yes/no question, ≤12 words, framed so YES = violation. No preamble or hedging. (e.g. "Contains spam keywords?")
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred). Keep the exact punctuation and wording.
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema (see below). For subjective items, keep `prompt_template` to one sentence and `rubric` to ≤2 short sentences listing the signals to weigh — no preamble, no restatement of the description.
- action: "remove", "warn", or "continue"
  - Leaf nodes (no children): use "remove" or "warn" to set the consequence. "continue" is not allowed for leaf nodes.
  - Non-leaf nodes (has children): MUST always be "continue". The verdict comes entirely from the children.
- children: Sub-items evaluated when this item says YES (empty list for leaf nodes)
- context_influenced: true if the community context (purpose, participants, stakes, tone) shaped how this item was framed, calibrated, or phrased. Set false when the item derives purely from the rule text.
- context_note: If context_influenced is true, a single clause ≤20 words: "[situational fact] → [calibration decision]" (e.g. "Crisis-support audience → threshold 0.6 to catch dismissive comments"). No hedging. Set null otherwise.

COMMUNITY CONTEXT CALIBRATION:
Community context reflects the community's own self-understanding and moderation priorities — not an outside \
observer's assessment. When context is provided, reason from it to your calibration choices:
- Read the PURPOSE to understand what "off-topic" or "low quality" means for THIS community's moderation scope.
- Read the PARTICIPANTS to understand who the community sees itself as. If participants voluntarily \
  engage with risky activity, don't over-protect — calibrate for actual moderation-relevant risks, not \
  paternalistic concern.
- Read the STAKES to calibrate thresholds. STAKES reflect what THIS community's moderators enforce \
  against. Low-stakes topics = community tolerates that content = set MORE permissive thresholds. \
  High-stakes topics = mods actively remove = set MORE sensitive thresholds.
- Read the TONE to match rubric language and example tone to the community's actual communication style. \
  Don't penalize content that matches the community's observed tone.
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
      "description": "Contains promotional language or calls to action?",
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
      "action": "warn",
      "children": [],
      "context_influenced": false,
      "context_note": null
    },
    {
      "description": "Contains known spam domains or URL shorteners?",
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
      "action": "warn",
      "children": [],
      "context_influenced": false,
      "context_note": null
    },
    {
      "description": "Primarily self-promotional, even without keywords?",
      "rule_text_anchor": "Posts should contribute to the community",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Is this post primarily an advertisement for the author's product, service, or brand rather than a contribution to the community?",
        "rubric": "Score higher when the post centers on something the author sells, includes calls to action ('check out my...'), or offers little value beyond promotion.",
        "threshold": 0.65,
        "examples_to_include": 5
      },
      "action": "remove",
      "children": [],
      "context_influenced": false,
      "context_note": null
    },
    {
      "description": "New account (spam signal)?",
      "rule_text_anchor": null,
      "item_type": "structural",
      "logic": {
        "type": "structural",
        "checks": [
          {"field": "account_age_days", "operator": "<", "value": 7}
        ],
        "match_mode": "all"
      },
      "action": "warn",
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
      "description": "Political, religious, or soap-boxing content?",
      "rule_text_anchor": "This means that this is not the place for politics, religion, soap boxing of any kind",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Does this post or comment push a political/religious agenda or lecture others rather than contribute to the discussion?",
        "rubric": "Score higher when content promotes a political agenda, promotes religious beliefs, or is preachy/lecturing.",
        "threshold": 0.65,
        "examples_to_include": 5
      },
      "action": "remove",
      "children": [],
      "context_influenced": true,
      "context_note": "Casual mobile-gaming community → threshold 0.55 since mild soap-boxing breaks the lighthearted tone."
    },
    {
      "description": "Irrelevant to the Pikmin Bloom game?",
      "rule_text_anchor": "Pikmin Bloom posts only",
      "item_type": "subjective",
      "logic": {
        "type": "subjective",
        "prompt_template": "Does this post or comment have anything to do with the Pikmin Bloom game?",
        "rubric": "Score higher when the content does not discuss Pikmin Bloom or any related topic.",
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
      "relevance_note": "Game-related but venting/negative in tone — could be borderline soap-boxing depending on how strictly the community enforces the 'keep it light' tone",
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
            notes = d.get("notes", [])
            if notes:
                ctx_lines.append(f"  {label}:")
                for note in notes:
                    text, tag = _extract_note(note)
                    tag_suffix = f" [{tag}]" if tag else ""
                    ctx_lines.append(f"    - {text}{tag_suffix}")
        if ctx_lines:
            context_section = "\n\nCommunity context for calibration:\n" + "\n".join(ctx_lines)

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
{other_rules_summary if other_rules_summary else "No other rules yet."}{context_section}{posts_section}
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

REASONING STYLE: keep `reasoning` to ONE short sentence (≤25 words). Point at the specific signal that drove the call. No preamble ("This post..."), no restatement of the criterion, no hedging.

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
      "reasoning": "One short sentence (≤25 words) naming the signal that drove the call"
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

REASONING STYLE: keep `reasoning` to ONE short sentence (≤25 words). Name the specific way it fits or doesn't (tone, topic, framing). No preamble, no restatement of the question, no hedging.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_community_norms_prompt(
    post_content: dict,
    community_name: str,
    rules_summary: str,
    recent_decisions: list[dict],
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
            notes = d.get("notes", [])
            if notes:
                ctx_lines.append(f"  {label}:")
                for note in notes:
                    text, tag = _extract_note(note)
                    tag_suffix = f" [{tag}]" if tag else ""
                    ctx_lines.append(f"    - {text}{tag_suffix}")
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
  "reasoning": "One short sentence (≤25 words) naming the specific tone/topic/framing signal"
}}"""


# ── Community Context Generation ─────────────────────────────────────────────

GENERATE_CONTEXT_SYSTEM = """You are a community culture analyst. Given a community's metadata and \
a representative sample of its actual posts and comments, generate a structured community context profile.

Your output will be used to calibrate an automated moderation system. Each dimension should answer: \
how should this aspect of the community shape moderation decisions?

Your analysis should reflect OBSERVED BEHAVIOR — what the community actually does — not just what its \
sidebar says. Describe the community from its OWN perspective, not an outside observer's. Use the \
sampled posts and comments as primary evidence.

CRITICAL FRAMING: For every dimension, ask "what does this mean for how THIS community should be \
moderated?" — not "how would an outsider describe this community?"
- If the community celebrates content that an outsider might find risky, that content has LOW moderation \
  stakes here — moderating it would remove what the community values most.
- If participants voluntarily engage with risky activity and signal awareness, don't frame them as \
  "vulnerable" — that leads to over-protective moderation that the community would reject.
- The gap between stated rules and actual behavior tells you what the community REALLY enforces.

For each of the four dimensions (purpose, participants, stakes, tone):
1. Select 2-4 tags from the taxonomy (prefer fewer, sharper tags — only the ones that materially \
shape moderation here).
2. For each selected tag, write a TERSE explanation: ≤15 words, a single short clause, no preamble, \
no hedging, no rephrasing of the tag itself. Skim-readable. Should fit on one line.

GOOD: "Memes celebrated; treat strict literalism as out-of-step."
BAD:  "This community appears to value lighthearted humor and meme-based content, so moderators \
should be careful not to overly enforce literal interpretations of rules."

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
            ("ignored", "Ignored posts (low engagement + downvoted — community-rejected content, implicit norm violations)"),
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
For each dimension, SELECT 2-4 tags (prefer fewer, sharper tags). For EACH selected tag, write one \
TERSE calibration note (≤15 words, a single clause, no preamble or hedging) — grounded in observed \
behavior, written for fast skimming.

The tag is primary; the note is a per-tag explanation. Do NOT produce free-standing notes that aren't \
tied to a specific tag, and do NOT produce a separate list of tags alongside prose notes. Every note \
MUST be paired with exactly one tag.

For each dimension, write from the MODERATOR'S perspective — what this means for moderation decisions:

- PURPOSE: Describe the purpose as it relates to moderation scope. What counts as on-topic vs off-topic \
  HERE? A trading community that's really about entertainment/memes has different moderation boundaries \
  than one focused on serious analysis. Use celebrated vs ignored posts as evidence.
- PARTICIPANTS: Describe participants as the community sees itself, not as an outsider would label them. \
  If participants voluntarily engage with risky activity and signal awareness, don't frame them as \
  "vulnerable" — that leads to over-protective moderation. Focus on: who is actually at risk of harm \
  from content that moderators should remove?
- STAKES: Stakes must reflect what THIS community's moderators enforce against, not external harm. \
  Content that gets high scores despite seeming "risky" to an outsider = LOW moderation stakes for that \
  topic. The question is always: "Would the mods of THIS community remove this?" — not "Could this \
  cause harm in the abstract?" Distinguish community-internal moderation priorities from external \
  concerns the community does NOT moderate.
- TONE: Use the actual comments from hot posts to characterize everyday language, humor, formality. \
  If stated rules say "be civil" but celebrated content is profanity-laden banter, the moderation-relevant \
  tone is the observed one. Moderating against the actual tone would remove the community's most valued content.
- The gap between stated rules and actual behavior tells you what the community REALLY enforces.

Return JSON in exactly this format (each note = one selected tag + brief explanation of how it applies):
{{
  "purpose": {{
    "notes": [
      {{"tag": "taxonomy_tag", "text": "How this tag applies to this community's moderation"}},
      {{"tag": "another_tag", "text": "How this tag applies here"}}
    ]
  }},
  "participants": {{
    "notes": [
      {{"tag": "taxonomy_tag", "text": "How this tag applies here"}},
      {{"tag": "another_tag", "text": "How this tag applies here"}}
    ]
  }},
  "stakes": {{
    "notes": [
      {{"tag": "taxonomy_tag", "text": "How this tag applies here"}},
      {{"tag": "another_tag", "text": "How this tag applies here"}}
    ]
  }},
  "tone": {{
    "notes": [
      {{"tag": "taxonomy_tag", "text": "How this tag applies here"}},
      {{"tag": "another_tag", "text": "How this tag applies here"}}
    ]
  }}
}}"""


# ── No-Context Compile (Pass 1 of two-pass) ──────────────────────────────────

NO_CONTEXT_COMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to compile a moderator's \
natural-language rule into a precise, structured decision tree that an automated system can execute.

Compile based SOLELY on the rule text. Do NOT consider community culture, tone, participant demographics, \
or stakes. Produce a neutral, context-free checklist that any community with this rule text would use.

Each node in the tree is a YES/NO question where YES = a potential violation is detected.

Each checklist item must have:
- description: A terse yes/no question, ≤12 words, framed so YES = violation. No preamble or hedging.
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred)
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema. For subjective items keep `prompt_template` to one sentence and `rubric` to ≤2 short sentences.
- action: "remove", "warn", or "continue"
  - Leaf nodes (no children): use "remove" or "warn". "continue" is not allowed for leaf nodes.
  - Non-leaf nodes (has children): MUST always be "continue".
- children: Sub-items evaluated when this item says YES (empty list for leaf nodes)

All items should have context_influenced=false and context_note=null (no context is provided).

Tree evaluation semantics:
- If an item says NO: no action, children are skipped.
- If an item says YES: apply its action, then evaluate children.
- Non-leaf action is always "continue" — children are the sole decision-makers.
- At every level, the worst action (REMOVE > FLAG > approve) wins across all siblings.
- Frame every question so YES = violation.
- Use children to refine broad checks: e.g. deterministic parent (fast gate) with subjective child.

Logic schemas:
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any"|"all", "negate": false, "field": "all"|"title"|"body"}
- structural: {"type": "structural", "checks": [{"field": "...", "operator": "<"|">"|"=="|"!="|"<="|">="|"in", "value": ...}], "match_mode": "all"|"any"}
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Keep trees shallow (3 levels max). Generate one violating and one borderline example per top-level item.

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_no_context_compile_prompt(
    rule_title: str,
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
    existing_checklist: Optional[list] = None,
    existing_examples: Optional[list] = None,
) -> str:
    import json

    existing_context = ""
    if existing_checklist:
        existing_context += f"\n\nExisting checklist (preserve user customizations where rule intent unchanged):\n{json.dumps(existing_checklist, indent=2)}"
    if existing_examples:
        existing_context += f"\n\nExisting examples:\n{json.dumps(existing_examples, indent=2)}"

    return f"""{COMPILE_FEW_SHOT_EXAMPLES}

Now compile the following rule for the "{community_name}" community on {platform}.
NOTE: Compile based SOLELY on the rule text — no community context calibration.

Community context (other rules, for background):
{other_rules_summary if other_rules_summary else "No other rules yet."}
{existing_context}

Rule to compile:
{rule_title}: {rule_text}

Generate a minimal checklist tree — only as many items as the rule genuinely requires, no more. For each item, provide one violating example and one borderline example.

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
      "context_influenced": false,
      "context_note": null
    }}
  ],
  "examples": [
    {{
      "label": "compliant" | "violating" | "borderline",
      "content": {{...}},
      "relevance_note": "...",
      "related_checklist_item_description": "..."
    }}
  ]
}}"""


# ── Context Adjust (Pass 2 of two-pass) ──────────────────────────────────────

CONTEXT_ADJUST_SYSTEM = """You are a community moderation calibration expert. You are given a base checklist tree \
(compiled purely from rule text, without community context) and a community context profile. Your job is to \
adjust the checklist to fit THIS specific community.

You may:
1. **Adjust thresholds** on subjective items (e.g., lower from 0.7 to 0.6 for sensitive communities)
2. **Refine rubric language** to match the community's tone and priorities
3. **Add new items** that the community context demands but the rule text alone didn't suggest \
   (e.g., a support community might need a "toxic positivity" check under a civility rule)
4. **Keep items unchanged** when no context-driven adjustment is needed

For every item you modify or add, set context_influenced=true and:
- Write a context_note tracing your reasoning: "[situational fact from context] → [calibration decision]"
- Set context_change_types to an array of what you changed: "threshold", "rubric", "description", "action", \
"new_item" (for items added by context), "pattern" (regex changes), "check" (structural check changes). \
An item can have multiple change types (e.g. ["threshold", "rubric"]).

For EVERY item derived from a base-checklist entry (i.e., not a brand-new context-added item), set \
`base_description` to the EXACT description string of the base item you started from — copy it verbatim \
from the base checklist input. This is the ONLY way the UI can diff current-vs-base, so it is required \
whenever the item has a base counterpart, regardless of whether you changed the description. Set \
`base_description` to null ONLY when context_change_types=["new_item"] (the item has no base equivalent).

Items you keep unchanged should have context_influenced=false, context_note=null, context_change_types=[], \
and base_description set to their own description (copied from the base entry).

Return the FULL adjusted checklist tree (not a diff) plus an adjustment_summary as an array of short \
bullet strings (one per change, each under 20 words). Example: \
["Lowered threshold on item X from 0.7 → 0.6 (vulnerable population)", "Added new toxic-positivity check"].

Return ONLY valid JSON with no markdown formatting or code blocks."""


def build_context_adjust_prompt(
    rule_title: str,
    rule_text: str,
    community_name: str,
    platform: str,
    base_checklist: list[dict],
    community_context: dict,
    community_posts_sample: Optional[list] = None,
    pinned_items: Optional[list[dict]] = None,
    current_checklist: Optional[list[dict]] = None,
    custom_context_notes: Optional[list[dict]] = None,
) -> str:
    import json

    ctx_lines = []
    for dim, label in [("purpose", "PURPOSE"), ("participants", "PARTICIPANTS"),
                       ("stakes", "STAKES"), ("tone", "TONE")]:
        d = community_context.get(dim, {})
        notes = d.get("notes", [])
        if notes:
            ctx_lines.append(f"  {label}:")
            for note in notes:
                text, tag = _extract_note(note)
                tag_suffix = f" [{tag}]" if tag else ""
                ctx_lines.append(f"    - {text}{tag_suffix}")
    context_section = "\n".join(ctx_lines)

    custom_notes_section = ""
    if custom_context_notes:
        custom_lines = []
        for note in custom_context_notes:
            text, tag = _extract_note(note)
            if not text:
                continue
            tag_suffix = f" [{tag}]" if tag else ""
            custom_lines.append(f"    - {text}{tag_suffix}")
        if custom_lines:
            custom_notes_section = (
                "\n\nRULE-SPECIFIC CALIBRATION NOTES (these apply only to THIS rule and override "
                "community defaults where they conflict — moderator added these as explicit "
                "guidance):\n" + "\n".join(custom_lines)
            )

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
                snippets.append(f'    - "{title}" — {body}')
            parts.append("  Acceptable posts:\n" + "\n".join(snippets))
        if unacceptable:
            snippets = []
            for p in unacceptable[:4]:
                c = p.get("content", {}).get("content", {})
                title = c.get("title", "")
                body = (c.get("body", "") or "")
                snippets.append(f'    - "{title}" — {body}')
            parts.append("  Removed/unacceptable posts:\n" + "\n".join(snippets))
        if parts:
            posts_section = "\n\nRepresentative community posts:\n" + "\n".join(parts)

    pinned_section = ""
    if pinned_items:
        pinned_lines = []
        for p in pinned_items:
            desc = p.get("description", "")
            note = p.get("context_override_note", "")
            pinned_lines.append(f'  - "{desc}"' + (f" — Moderator note: {note}" if note else ""))
        pinned_section = (
            "\n\nMODERATOR-PINNED ITEMS (preserve these items' calibration exactly as-is — "
            "do not adjust thresholds, rubric, or logic):\n" + "\n".join(pinned_lines)
        )

    current_section = ""
    if current_checklist:
        current_section = f"""

Current live checklist (what moderators see today, already context-adjusted):
{json.dumps(current_checklist, indent=2)}

NOTE: Your adjustment_summary should describe changes relative to the CURRENT LIVE checklist above, \
not the base checklist. For example, if the current checklist has threshold 0.70 and you set it to 0.72, \
write "Raised threshold from 0.70 → 0.72", not from the base value. If an item is unchanged from the \
current live version, do NOT mention it in the summary."""

    return f"""Adjust this base checklist for the "{community_name}" community on {platform}.

Rule: {rule_title}: {rule_text}

Community context:
{context_section}{custom_notes_section}{posts_section}{pinned_section}

Base checklist (compiled from rule text only, no context):
{json.dumps(base_checklist, indent=2)}
{current_section}

Review each item and adjust for this community's context. Return the full adjusted tree plus a summary.

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
      "context_note": "[situational fact] → [calibration decision], or null",
      "context_change_types": ["threshold", "rubric", ...] or [],
      "base_description": "verbatim description from the base checklist entry this was derived from, or null only for new_item"
    }}
  ],
  "adjustment_summary": "Human-readable summary of what was adjusted and why (2-5 sentences)"
}}"""


# ── Recompile (diff) ───────────────────────────────────────────────────────────

RECOMPILE_SYSTEM = """You are an expert community moderation system architect. Your job is to update an existing \
checklist tree to reflect changes to the rule text. This runs on every keystroke (debounced) in a fluid editor \
where unchanged subtrees are CACHED — so emitting the smallest possible diff is critical for performance, not \
just hygiene.

You will be given:
- The updated rule text
- The existing checklist items (each with an id, description, rule_text_anchor, and other fields)

For each existing item, decide:
- "keep": The rule text change does not affect this item. Return it unchanged. **THIS IS THE DEFAULT.**
- "update": The item still applies but needs targeted field changes. Include ONLY the fields that change \
(description, logic, action, rule_text_anchor) — omit fields that stay the same. The system merges them in.
- "delete": The rule text change makes this item obsolete or incorrect.

You may also emit:
- "add": A brand new item required by the updated rule text that has no equivalent in the existing checklist.

MINIMAL-DIFF DISCIPLINE (this matters):
- Default to "keep". An "update" should reflect a real change, not a rewording for its own sake.
- For rubric tweaks: change ONLY the rubric/threshold field; leave description and item_type alone.
- For threshold tweaks: change ONLY the logic.threshold value.
- Do NOT rewrite a rubric just because the rule text was reworded — if the meaning is unchanged, "keep".
- A small text edit should produce 0-2 ops total. A larger conceptual change may produce more, but if you \
find yourself emitting >8 non-keep operations, the change is no longer incremental — emit them anyway, but \
the caller will fall back to a full recompile.
- Use rule_text_anchor as the primary signal. If the anchor phrase still appears in the updated rule text \
(even if reworded), prefer "keep" or "update" over "delete"+"add".
- "delete"+"add" pair on the same concept is almost always wrong — use "update" instead so the id is \
preserved (cached evaluation results stay valid).
- Only "add" when the rule text introduces a brand-new concept with no equivalent in any existing item. \
Do NOT add items to "improve" or "complete" the checklist — strictly reflect the rule-text diff.
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

DIAGNOSE_HEALTH_SYSTEM = """You are a moderation rule health analyst. You will be given a community rule, its checklist items, and accumulated performance metrics from moderator decisions (false positive rates, false negative rates, confidence distributions, and example posts). You will also see moderator feedback on error cases — their notes explain WHY they disagreed with the agent's verdict.

Your job is to diagnose which specific problem each underperforming item has and propose the minimal, targeted fix. Pay close attention to moderator feedback — it reveals the root cause of errors (e.g., "this is satire" suggests the rubric doesn't account for tone/context).

Five possible diagnoses:
- **tighten_rubric**: The rubric description is too vague — the model is guessing and making inconsistent calls. Fix: rewrite the rubric to be more precise and unambiguous.
- **adjust_threshold**: The rubric logic is correct but the sensitivity is miscalibrated. Fix: raise or lower the threshold value in the logic field. You MUST include the specific new threshold value (e.g., change threshold from 0.6 → 0.75).
- **promote_to_deterministic**: Clear text patterns have emerged that a regex can catch reliably — no LLM needed. Fix: change item_type to "deterministic" and add regex patterns.
- **split_item**: One item is trying to evaluate two different things, causing confusion. Fix: propose splitting into two focused items.
- **add_item** (new_items only): Violations exist that aren't covered by any current item. Fix: add a new checklist item.

Diagnosis rules:
- Only diagnose items with decision_count ≥ 3 and fp_rate > 0.15 OR fn_rate > 0.15 (unless uncovered violations force an add_item).
- Items with both low FP and FN rates are healthy — skip them entirely.
- HIERARCHY: When diagnosing items that have children, determine whether the problem is in the parent's \
gate logic or in a specific child. Diagnose at the MOST SPECIFIC level. Do not diagnose both a parent \
and its child for the same underlying issue. If the child's threshold/rubric is wrong, diagnose the child. \
Only diagnose the parent if the parent's own gate condition is the root cause.
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

        # Build moderator feedback on error cases
        mod_feedback_rows = []
        for case in (metrics.get("wrongly_flagged") or [])[:3]:
            notes = case.get("moderator_notes") or ""
            cat = case.get("moderator_reasoning_category") or ""
            if notes or cat:
                parts = [f"[WRONGLY_FLAGGED] \"{case.get('title', '')}\""]
                if notes:
                    parts.append(f"mod notes: \"{notes}\"")
                if cat:
                    parts.append(f"({cat})")
                mod_feedback_rows.append("  " + " — ".join(parts))
        for case in (metrics.get("missed_violations") or [])[:3]:
            notes = case.get("moderator_notes") or ""
            cat = case.get("moderator_reasoning_category") or ""
            if notes or cat:
                parts = [f"[MISSED] \"{case.get('title', '')}\""]
                if notes:
                    parts.append(f"mod notes: \"{notes}\"")
                if cat:
                    parts.append(f"({cat})")
                mod_feedback_rows.append("  " + " — ".join(parts))

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
            "moderator_feedback": mod_feedback_rows,
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
        "action": "remove | warn | continue"
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
        "action": "remove | warn | continue",
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
