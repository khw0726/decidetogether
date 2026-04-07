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
natural-language rule into a precise, structured decision tree that an automated system can execute.

Each node in the tree is a YES/NO question where YES = a potential violation is detected.

Each checklist item must have:
- description: A yes/no question framed so that YES = violation signal (e.g. "Does the post contain spam keywords?")
- rule_text_anchor: The exact phrase from the rule text this derives from (null if inferred)
- item_type: "deterministic" (regex), "structural" (metadata), or "subjective" (LLM judgment)
- logic: Type-specific schema (see below)
- action: "remove", "flag", or "continue"
  - Leaf nodes (no children): use "remove" or "flag" to set the consequence.
  - Non-leaf nodes (has children): MUST always be "continue". The verdict comes entirely from the children.
- children: Sub-items evaluated when this item says YES (empty list for leaf nodes)

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
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any"|"all", "negate": false}
  - negate=false: triggered when pattern IS found (e.g. spam keywords present)
  - negate=true: triggered when pattern is NOT found (e.g. required tag missing)
- structural: {"type": "structural", "checks": [{"field": "account_age_days"|"post_type"|"flair"|"karma", "operator": "<"|">"|"<="|">="|"=="|"!="|"in", "value": ...}], "match_mode": "all"|"any"}
  - triggered when the condition is true (e.g. account_age_days < 7 triggers for new accounts)
- subjective: {"type": "subjective", "prompt_template": "...", "rubric": "...", "threshold": 0.7, "examples_to_include": 5}

Keep trees shallow (2 levels max). Generate exactly 3 compliant examples (posts that follow the rule), one violating example, and one borderline example per top-level checklist item (so if you generate 3 checklist items, generate 3 violating + 3 borderline examples — one of each clearly targeting each item). Borderline examples are posts that reasonable moderators might genuinely disagree on — they sit at the gray area of the rule.

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
      "action": "flag",
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
      "action": "remove",
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
      "action": "flag",
      "children": []
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
      "label": "compliant",
      "content": {
        "id": "example-2",
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
      "action": "remove",
      "children": []
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
      "label": "compliant",
      "content": {
        "id": "example-4",
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
    rule_text: str,
    community_name: str,
    platform: str,
    other_rules_summary: str,
    existing_checklist: Optional[list] = None,
    existing_examples: Optional[list] = None,
    community_atmosphere: Optional[dict] = None,
    community_posts_sample: Optional[list] = None,
) -> str:
    import json

    existing_context = ""
    if existing_checklist:
        existing_context += f"\n\nExisting checklist (preserve user customizations where rule intent unchanged):\n{json.dumps(existing_checklist, indent=2)}"
    if existing_examples:
        existing_context += f"\n\nExisting examples:\n{json.dumps(existing_examples, indent=2)}"

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
        atmosphere_section = "\n\nCommunity atmosphere:\n" + "\n".join(lines)

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
                body = (c.get("body", "") or "")[:120]
                note = p.get("note", "")
                snippets.append(f'    - "{title}" — {body}{"..." if len(c.get("body",""))>120 else ""}' + (f' [{note}]' if note else ''))
            parts.append("  Acceptable posts:\n" + "\n".join(snippets))
        if unacceptable:
            snippets = []
            for p in unacceptable[:4]:
                c = p.get("content", {}).get("content", {})
                title = c.get("title", "")
                body = (c.get("body", "") or "")[:120]
                note = p.get("note", "")
                snippets.append(f'    - "{title}" — {body}{"..." if len(c.get("body",""))>120 else ""}' + (f' [{note}]' if note else ''))
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
{other_rules_summary if other_rules_summary else "No other rules yet."}{atmosphere_section}{posts_section}
{existing_context}

Rule to compile:
{rule_text}

Generate a checklist tree with 2-3 items (can have children), plus 3 compliant examples and one violating example per top-level checklist item.

Return JSON in exactly this format:
{{
  "checklist_tree": [...],
  "examples": [
    {{
      "label": "compliant" | "violating" | "borderline",
      "content": {{...post content object...}},
      "relevance_note": "Why this example relates to the rule",
      "related_checklist_item_description": "Exact description of the checklist item this example primarily tests, or null for compliant examples"
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
    borderline_examples: list[dict] | None = None,
) -> str:
    import json

    examples_str = ""
    if examples:
        examples_str = f"\n\nClear community examples (compliant/violating — use for calibration):\n{json.dumps(examples[:8], indent=2)}"
    if borderline_examples:
        examples_str += f"\n\nBorderline calibration examples (reasonable moderators disagree on these — use to understand edge cases):\n{json.dumps(borderline_examples[:4], indent=2)}"

    items_str = json.dumps(items_with_rubrics, indent=2)
    post_str = json.dumps(post_content, indent=2)

    return f"""Evaluate this post for the "{community_name}" community.

Post content:
{post_str}
{examples_str}

Evaluate the following checklist items. Each item is a yes/no question where YES = violation detected.

For each item:
- triggered: true means YES, the violation described by the question IS present
- triggered: false means NO, the post is fine for this criterion
- confidence: 0.0 to 1.0 (how confident you are in this judgment)

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
- deterministic: {"type": "deterministic", "patterns": [{"regex": "...", "case_sensitive": false}], "match_mode": "any", "negate": false}
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
- Only "add" when the rule text introduces a new concept not covered by any existing item.
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

For deterministic items: only propose a pattern update if the item's violating_example_count >= 3. When that threshold is met, analyze the literal text of those violating examples and propose a refined regex pattern in proposed_change (under the "patterns" key) that matches them but not the compliant examples.

For each suggestion:
- If suggestion_type is "rule_text": set proposed_text to the COMPLETE updated rule text (all paragraphs preserved — only modify the specific sentences that need changing, leave the rest intact). Do NOT return a partial snippet.
- If suggestion_type is "checklist": set proposed_change to the updated checklist item object.

Return JSON in exactly this format:
{{
  "suggestions": [
    {{
      "suggestion_type": "checklist" | "rule_text",
      "target": "item_id or null for new items or null for rule_text",
      "description": "What to change and why",
      "proposed_text": "For rule_text: the COMPLETE updated rule text",
      "proposed_change": {{...for checklist: the updated checklist item object...}},
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
      "content": {{...normalized post content...}},
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

For each checklist item provided, generate exactly one violating example — a post that clearly and unambiguously triggers that specific item. Make examples realistic and specific enough to be useful test cases.

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
      "content": {{...post content object...}},
      "relevance_note": "Why this example triggers the checklist item",
      "related_checklist_item_description": "Exact description of the checklist item this triggers"
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
