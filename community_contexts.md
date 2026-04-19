# Community Context: Definition and Empirical Taxonomy

## What is Community Context?

Community context is a structured description of a community's **situation** — not rules about behavior, but facts about the environment that allow a moderation system to reason about appropriate calibration. The same rule text (e.g. "Be respectful") should produce different decision trees for a mental health support forum vs. a competitive gaming community, because the stakes, participants, and communication norms are fundamentally different.

Community context answers four questions:

1. **Purpose** — What is this space for? What do people come here to do?
2. **Participants** — Who is here? What expertise levels? Any vulnerability factors?
3. **Stakes** — What could go wrong with harmful content? What could go wrong with over-moderation?
4. **Tone** — What is the communication style? How formal? How much conflict is normal?

### Two-Layer Structure

Each dimension has two layers:

- **Prose layer** (source of truth): 2-3 sentence free-text description, moderator-editable. Maximally expressive. This is what the compiler reads to make judgment calls.
- **Structured layer** (categorical tags): Machine-readable tags drawn from a validated taxonomy. Used for systematic filtering, comparison, and UI display.

Example for r/legaladvice:
```json
{
  "purpose": {
    "prose": "A crowdsourced legal information resource where users post questions about US and Canadian law to receive guidance from community members with legal knowledge.",
    "tags": ["question_and_answer", "peer_support_and_advice"]
  },
  "participants": {
    "prose": "Comprises legal laypeople seeking advice, likely including vulnerable populations facing urgent legal problems, alongside volunteer responders with varying levels of actual legal expertise.",
    "tags": ["vulnerable_populations", "expertise_asymmetry", "general_audience"]
  },
  "stakes": {
    "prose": "Users may rely on incorrect legal advice for high-stakes decisions that could result in financial loss, legal liability, or safety harm. Over-moderation could prevent people from accessing legal information when they cannot afford attorneys.",
    "tags": ["financial_harm_risk", "misinformation_risk", "chilling_effect"]
  },
  "tone": {
    "prose": "Formal, professional tone with straightforward Q&A exchanges and emphasis on disclaimers and proper procedures.",
    "tags": ["formal", "low_humor", "civility_enforced"]
  }
}
```

---

## How We Derived the Taxonomy

The structured tags are not hand-designed. They were empirically discovered from 1,000 real subreddits through a four-step pipeline:

### Step 1: Crawl subreddit descriptions

**Script**: `scripts/crawl_subreddit_descriptions.py`

Crawled 1,000 subreddits via Reddit's public JSON API:
- `/subreddits/popular` (paginated, 10 pages of 100) for high-traffic communities
- `/subreddits/search` across 72 topic queries (mental health, legal advice, woodworking, gaming, memes, NSFW, etc.) for diversity

Collected per subreddit: `name`, `title`, `public_description`, `description` (full sidebar), `subscribers`, `over18`, `subreddit_type`, `created_utc`, rules (via `/about/rules.json`).

**Output**: `scripts/subreddit_descriptions.json` — 1,000 entries, median sidebar length ~3,000 chars.

**Limitations**: Skewed toward large subs (min 45k subscribers). No NSFW subs (Reddit filters them for unauthenticated requests).

### Step 2: LLM extraction (open-ended)

**Script**: `scripts/extract_community_context.py`

For each subreddit, prompted Claude Haiku with the sidebar description + rules and asked it to extract prose + free-form tags for all four dimensions. No predefined categories — the model described what it saw in its own words.

Prompt structure:
```
Given this subreddit metadata, extract:
PURPOSE: What is this space for? (prose + 3-5 tags)
PARTICIPANTS: Who is here? (prose + 3-5 tags)
STAKES: What could go wrong? (prose + 3-5 tags)
TONE: What's the communication style? (prose + 3-5 tags)
```

**Output**: `scripts/community_contexts_extracted.jsonl` — 1,000 entries, ~5,000 tags per dimension, ~2,000-2,700 unique tags per dimension.

### Step 3: Cluster tags into canonical categories

**Script**: `scripts/cluster_context_tags.py`

Two-pass LLM clustering using Sonnet:

1. **Discovery**: Send top 150 most frequent tags (per dimension) to the LLM, ask it to define 10-25 canonical categories with descriptions.
2. **Mapping**: Assign all ~2,000+ unique tags to canonical categories in batches of 200.

This resolves near-duplicates (e.g. `informal_casual`, `casual_informal`, `informal_conversational` → `informal_casual`) and absorbs the long tail of rare tags into meaningful groups.

**Output**:
- `scripts/context_taxonomy.json` — canonical categories with descriptions and example tags
- `scripts/context_tag_mapping.json` — full tag → category mapping (~9,000 tags)
- `scripts/community_contexts_clustered.jsonl` — original data with canonical tags

### Limitations of Description-Only Analysis

The taxonomy above was derived from subreddit descriptions and rules alone. Descriptions are good for purpose and participant profiles, but weak for:

- **Tone** — You can't learn that WSB normalizes "loss porn" and crude slang from the sidebar. Real post language is needed.
- **Stakes** — A post with dangerous medical advice sitting at +500 tells you something the sidebar never will.
- **The gap between stated norms and actual behavior** — A sub might say "be respectful" but routinely upvote savage takedowns.

The taxonomy categories are still valid (they describe *what dimensions exist*), but the per-community *values* for those dimensions should be grounded in actual community activity. See "Post-Informed Context Generation" in the Implementation Plan below.

### Step 4: Validation

**Script**: `scripts/validate_taxonomy.py`

Five automated checks:

| Check | Result |
|-------|--------|
| **"Other" bucket** | Purpose 1.1%, Participants 4.8%, Stakes 2.4%, Tone 2.2% — all under 5% |
| **Co-occurrence** | Some expected overlaps (informal_casual + low_conflict). `mixed_expertise_levels` and `content_permanence` are near-universal — may need to be made implicit |
| **Balance** | 7 rare participant categories (<1%) could be merged. No severely dominant categories |
| **Saturation** | All 4 dimensions fully saturated by 100-200 subs. No new categories after that |
| **Spot checks** | wallstreetbets → humor_heavy, dark_or_edgy_humor, financial_harm_risk. chess → evidence_analytical, civility_enforced. mentalhealth → supportive_encouraging, vulnerable_populations. All sensible |

Additionally, a **contrastive compilation test** (`scripts/test_contrastive_compile.py`) compiled the same or similar rules for communities with different contexts and verified meaningfully different outputs. See "Contrastive Compilation Evidence" below.

---

## The Taxonomy

### Purpose (20 categories)

| Category | Description |
|----------|-------------|
| `peer_support_and_advice` | Emotional support, personal advice, peer assistance, and help-seeking |
| `technical_support_and_troubleshooting` | Technical help, bug reporting, troubleshooting, problem-solving |
| `knowledge_and_information_sharing` | Sharing facts, resources, educational content |
| `news_and_current_events` | Aggregating, sharing, and discussing news and updates |
| `discussion_and_debate` | General discussion, debate, commentary, exchange of opinions |
| `content_aggregation_and_curation` | Collecting, curating, and surfacing links or content |
| `fan_community_and_franchise` | Fan communities around a specific franchise, IP, show, or game |
| `game_discussion_and_strategy` | Gameplay discussion, strategy, theorycrafting, build optimization |
| `humor_and_memes` | Meme creation/sharing, humor-focused content, jokes |
| `creative_expression_and_showcase` | Sharing original creative work, projects, art, photos |
| `question_and_answer` | Structured Q&A, recommendation-seeking, decision support |
| `media_sharing_and_consumption` | Sharing and consuming videos, clips, images, screenshots |
| `skill_development_and_learning` | Learning, skill-building, peer education, how-to guides |
| `career_and_professional_guidance` | Career advice, professional development, workplace guidance |
| `community_coordination_and_events` | Organizing events, coordination, scheduling, community logistics |
| `consumer_and_product_discussion` | Product reviews, purchasing advice, equipment discussion |
| `social_connection_and_community_building` | Building relationships, cultural exchange, social bonding |
| `civic_and_local_engagement` | Civic participation, local community hubs, public affairs |
| `entertainment_and_passive_consumption` | Entertainment for passive enjoyment with low interaction |
| `other` | Doesn't fit above categories |

### Participants (23 categories)

| Category | Description |
|----------|-------------|
| `mixed_expertise_levels` | Wide range from beginners to experts, no dominant tier |
| `expertise_asymmetry` | Significant imbalance between expert and novice members |
| `casual_to_hardcore_spectrum` | Low-investment casual consumers to deeply committed enthusiasts |
| `broad_age_range` | Members across multiple generational cohorts |
| `youth_presence` | Meaningful proportion of minors, elevating age-related concerns |
| `adult_focused` | Primarily or explicitly oriented toward adult participants |
| `vulnerable_populations` | Members at elevated risk (financial, emotional, legal, developmental) |
| `misinformation_susceptibility` | Heightened risk of encountering or spreading false information |
| `global_audience` | Geographically distributed, multilingual, cross-cultural |
| `general_audience` | No specific demographic prerequisites; open to the broad public |
| `low_barrier_to_entry` | Minimal prior knowledge or credentials needed to participate |
| `enthusiast_community` | Strong shared interest or fandom, hobbyists to superfans |
| `professional_practitioners` | People with industry credentials, career expertise, vocational stakes |
| `competitive_players` | Ranked, tournament, or performance-oriented participants |
| `information_seekers` | Primarily present to find answers or receive advice |
| `career_stage_diversity` | Students, job seekers, career changers, and veterans |
| `large_scale_community` | High membership volume, increasing anonymity and moderation complexity |
| `parasocial_dynamics` | Relationships with public figures affecting member behavior |
| `mentorship_dynamic` | Experienced members guiding newer participants |
| `technical_literacy_variance` | Wide spread in technical or domain-specific content engagement |
| `internet_native` | Culturally fluent in internet norms, memes, platform behaviors |
| `geographically_local` | Anchored to a specific region, city, or locale |
| `other` | Doesn't fit above categories |

### Stakes (23 categories)

| Category | Description |
|----------|-------------|
| `misinformation_risk` | False or misleading information spreading and causing harm |
| `content_permanence` | Risks from content being permanently indexed and searchable |
| `harassment_risk` | Targeted harassment, bullying, doxxing, personal attacks |
| `chilling_effect` | Moderation suppressing legitimate participation or discourse |
| `hate_speech_risk` | Discriminatory or bigoted content targeting protected groups |
| `financial_harm_risk` | Scams, fraud, or bad financial advice causing monetary loss |
| `privacy_violation_risk` | Exposure of private personal information, doxxing |
| `spoiler_harm` | Unwanted plot/outcome exposure degrading member experience |
| `community_fragmentation` | Polarization, echo chambers, erosion of shared norms |
| `spam_and_exploitation` | Spam, commercial exploitation, malware, low-quality flooding |
| `health_and_safety_misinformation` | Medically or physically dangerous misinformation |
| `minor_protection_risk` | Exposing minors to inappropriate content or exploitation |
| `legal_and_liability_risk` | Content creating legal exposure (piracy, defamation, etc.) |
| `content_quality_degradation` | Declining quality through reposts, low-effort posts, noise |
| `psychological_harm_risk` | Content causing psychological distress or trauma |
| `toxicity_risk` | Hostile or unwelcoming atmosphere driving away members |
| `radicalization_and_normalization_risk` | Normalizing extremist views or shifting norms toward harm |
| `technical_harm_risk` | Bad technical advice causing hardware damage, data loss, etc. |
| `brigading_risk` | Coordinated cross-community attacks or manipulation |
| `trust_erosion` | Declining trust between members, moderators, or platform |
| `censorship_tension` | Tension between free expression and content removal |
| `newcomer_discouragement` | Norms or hostility deterring new members |
| `other` | Doesn't fit above categories |

### Tone (19 categories)

| Category | Description |
|----------|-------------|
| `low_conflict` | Minimal interpersonal conflict, by norm, enforcement, or culture |
| `civility_enforced` | Respectful behavior actively moderated or explicitly required |
| `informal_casual` | Relaxed, everyday conversational register |
| `semi_formal` | Balancing accessibility with some structure or professionalism |
| `formal` | High formality, professional tone, or academic register |
| `humor_heavy` | Humor, memes, or comedy central to the culture |
| `low_humor` | Humor present but peripheral or restrained |
| `dark_or_edgy_humor` | Dark, self-deprecating, dry, or self-aware comedic styles |
| `collaborative_helpful` | Mutual aid, problem-solving, cooperative participation |
| `supportive_encouraging` | Emotional support, encouragement, positive reinforcement |
| `evidence_analytical` | Facts, evidence, data, or rigorous analysis prioritized |
| `debate_oriented` | Structured argumentation, debate, contested discussion |
| `technical` | Specialized or technical language with some accessibility |
| `respectful` | Broadly respectful tone not captured by formal enforcement |
| `enthusiastic_passionate` | High energy, excitement, strong emotional investment |
| `pragmatic_direct` | Straightforward, practical, no-nonsense communication |
| `conflict_present` | Some tension, frustration, or conflict is normal/accepted |
| `community_vernacular` | Highly specific in-group language, slang, or norms |
| `other` | Doesn't fit above categories |

---

## Contrastive Compilation Evidence

We compiled real rules from real subreddits to verify that context produces meaningfully different checklists. Three pairs tested:

### "Be respectful" — r/gaming vs r/mentalhealth

| | r/gaming | r/mentalhealth |
|---|---------|---------------|
| **Rule text** | "Posts and comments, whether in jest or with malice, that contain racist, sexist, homophobic, threats, or other toxic content will be removed" | "All posts and comments must be respectful and supportive. Do not insult, provoke, harass, or act disrespectfully" |
| **Key calibration** | Threshold 0.65 — "casual humor culture means some edgy-but-non-bigoted content exists legitimately" | Threshold 0.65 — but "dismissive or invalidating content — even if not overtly hostile — can cause real harm" |
| **Unique detection** | Coded language, dog whistles, sustained personal attacks | Dismissiveness toward suffering, invalidation of experiences |

### "No Bullshitting" vs "Must provide evidence" — r/wallstreetbets vs r/science

| | r/wallstreetbets | r/science |
|---|-----------------|-----------|
| **Rule text** | "Don't make shit up, and be responsible giving and taking advice. Nobody wants an ill-informed opinion." | "Comments that dispute well-established scientific concepts must be supported with peer-reviewed evidence." |
| **Structure** | 2 subjective items | 1 subjective + 2 deterministic children |
| **Key calibration** | Threshold 0.7, uninformed advice flagged (not removed) because "WSB normalizes bold, even reckless-sounding calls" | Threshold 0.6 (stricter), no-citation claims removed outright because "health misinformation can reach 34M+ subscribers" |
| **Action** | Flag for review — "removal is reserved for fabrication" | Remove — "complete absence of citation when disputing consensus warrants removal" |

### "No offensive content" — r/facepalm vs r/mentalhealth

| | r/facepalm | r/mentalhealth |
|---|-----------|---------------|
| **Rule text** | "Must be civil. Hate-speech and bigotry will result in permanent bans, as will misinformation, personal attacks or name calling." | "Posts should be appropriate for anyone 13 or older. We prohibit posts that provide too much detail about violence, abuse, or self-harm." |
| **# Items** | 4 items | 2 items |
| **Key calibration** | Misinformation threshold raised to 0.75 — "many legitimate facepalm posts contain misinformation as the subject being mocked" | Graphic detail threshold lowered to 0.6 — "even moderately graphic detail poses serious triggering risk" for "vulnerable, crisis-prone population" |
| **Unique detection** | Distinguishes mocking misinformation (allowed) from spreading it | Distinguishes mentioning self-harm (allowed) from providing graphic detail |

---

## Scripts Reference

| Script | Purpose | Output |
|--------|---------|--------|
| `scripts/crawl_subreddit_descriptions.py` | Crawl 1,000 subreddit descriptions + metadata | `subreddit_descriptions.json` |
| `scripts/extract_community_context.py` | LLM extraction of prose + tags per subreddit | `community_contexts_extracted.jsonl` |
| `scripts/cluster_context_tags.py` | Cluster free-form tags into canonical taxonomy | `context_taxonomy.json`, `context_tag_mapping.json`, `community_contexts_clustered.jsonl` |
| `scripts/validate_taxonomy.py` | Validate taxonomy coverage, balance, saturation | Terminal report |
| `scripts/test_contrastive_compile.py` | Compile same rule for different communities, compare | `contrastive_compile_results.json` |

---

## Implementation Plan

### Phase 1: Data Model + API + UI

#### 1a. Data model

**`src/automod/db/models.py`** — On `Community`:
- Add `community_context` JSON column (structure as above)
- Keep `atmosphere` column, migrate its data into `community_context.atmosphere`
- Add backward-compat property so `community.atmosphere` reads from `community_context["atmosphere"]`

**`src/automod/db/models.py`** — On `ChecklistItem`:
- Rename `atmosphere_influenced` → `context_influenced`
- Rename `atmosphere_note` → `context_note`

#### 1b. Pydantic schemas

**`src/automod/models/schemas.py`** — Add:
- `CommunityContextPurpose`, `CommunityContextParticipants`, `CommunityContextStakes`, `CommunityContextTone` — each with `prose: str` and `tags: list[str]`
- `CommunityContextData` — wraps the four sections + atmosphere + timestamps
- `CommunityContextUpdate` — for moderator edits (can update prose, tags, or both per section)
- Update `CommunityRead` to include `community_context`
- Update `ChecklistItemRead` for renamed fields

#### 1c. API endpoints

**`src/automod/api/communities.py`**:
- `GET /communities/{id}/context` — returns full community context
- `PUT /communities/{id}/context` — moderator updates prose and/or tags for any section
- `POST /communities/{id}/context/generate` — auto-generate context using all available signals (see Post-Informed Context Generation below). Generates both prose and structured tags. Preserves moderator edits to tags they've manually changed.
- Keep `POST /communities/{id}/atmosphere/generate` working (writes to `community_context.atmosphere`)

#### 1e. Post-Informed Context Generation

The `/context/generate` endpoint uses **activity-based post sampling** to automatically gather representative posts from the community, then feeds them (along with metadata) to the LLM for context generation. This is a smarter replacement for manually providing sample posts — instead of a moderator hand-picking examples, the system samples posts that reveal what the community actually values, contests, and ignores.

**Two inputs to the LLM:**

1. **Community metadata** (always available)
   - Subreddit description/sidebar, public description, rules
   - Subscriber count, over18 flag, creation date

2. **Activity-sampled posts** (fetched from Reddit, the key improvement)

   The sampling strategy prioritizes **typical content** (hot) over **exceptional content** (top), since atmosphere should reflect what the community looks like day-to-day, not its highlight reel.

   | Category | Source | Limit | Why |
   |----------|--------|-------|-----|
   | **Typical content** | `hot` | 25 | The current front page — what a regular visitor sees |
   | **Celebrated content** | `top(month)` | 10 | What the community especially values (smaller sample, outlier-prone) |
   | **Contested content** | `controversial(month)` | 10 | Where norms are unclear or disagreed-upon |
   | **Ignored content** | `new`, score ≤ 1, ≥12h old | 20 | What's unwelcome without being rule-violating |
   | **Tone sample** | Top 5 comments from 10 hot posts | 50 | Actual language, humor, formality from everyday threads |

   For Reddit, fetch via PRAW:
   ```python
   # What the community looks like day-to-day (primary sample)
   hot_posts = list(subreddit.hot(limit=25))

   # What the community especially celebrates (secondary, outlier-prone)
   top_posts = list(subreddit.top(time_filter="month", limit=10))

   # Where norms are contested
   controversial = list(subreddit.controversial(time_filter="month", limit=10))

   # What gets ignored (must be old enough to have had a chance at votes)
   from datetime import datetime, timedelta
   min_age = datetime.utcnow() - timedelta(hours=12)
   ignored = [p for p in subreddit.new(limit=100)
              if p.score <= 1 and datetime.utcfromtimestamp(p.created_utc) < min_age][:20]

   # Actual language and tone — from hot posts, not top (everyday conversation)
   for post in hot_posts[:10]:
       post.comments.sort_by = "top"
       top_comments = post.comments[:5]
   ```

   For non-Reddit platforms, the equivalent signals come from whatever the platform adapter provides.

**Generation prompt structure:**

The LLM receives metadata + sampled posts and produces context grounded in *observed behavior*, not just *stated intent*:

```
You are analyzing a community to generate structured context for a moderation system.

COMMUNITY METADATA:
  Name: {name} | Platform: {platform} | Subscribers: {subscribers}
  Description: {description}
  Rules: {rules}

SAMPLED POSTS — What the community actually does:

  Hot posts (current front page — typical day-to-day content):
    {hot_posts with titles, bodies, scores, comment counts}

  Celebrated posts (top of last month — what gets especially rewarded):
    {top_posts with titles, bodies, scores, comment counts}

  Controversial posts (last month — where norms are contested):
    {controversial_posts with titles, bodies, scores}

  Ignored posts (score ≤ 1, at least 12h old — unwelcome but not rule-breaking):
    {ignored_posts with titles, bodies}

  Top comments (from popular threads):
    {top_comments — actual text showing real language, tone, humor}

Based on ALL of the above, generate community context.
For each dimension, write prose that reflects observed behavior (not just stated rules),
then assign tags from the taxonomy.

Pay special attention to:
- TONE: Use the actual comments from hot posts to characterize everyday language, humor, formality — not the sidebar's aspirations
- STAKES: Use the contrast between hot/celebrated and ignored posts to identify what actually matters here
- The gap between stated rules and actual behavior (e.g. rules say "be civil" but hot post comments are savage)
```

**Caching:** Sampled posts are fetched live on each `/context/generate` call. Could be cached with a TTL (e.g. refresh daily) to avoid repeated API calls.

**Implementation:**

**`src/automod/compiler/compiler.py`** — Add `generate_community_context()`:
- Accepts: community metadata, sampled posts (grouped by category: top/controversial/ignored/comments)
- Calls LLM with structured prompt
- Returns: `CommunityContextData` with prose + tags per dimension
- Uses taxonomy from `scripts/context_taxonomy.json` to constrain tag assignment

**`src/automod/api/communities.py`** — In the `/context/generate` handler:
- Fetch activity-sampled posts from Reddit (or platform adapter)
- Gather community metadata from DB
- Call `compiler.generate_community_context()`
- Merge with existing context (preserve manually-edited fields)
- Save to `community.community_context`

#### 1d. Admin UI

**`admin/src/pages/CommunitySettings.tsx`** — New "Community Context" section:
- Four expandable cards: "Purpose", "Participants", "Stakes", "Tone"
- Each card shows the prose (editable textarea) and structured tags (editable chips/dropdowns using taxonomy values)
- "Generate" button calls `/context/generate` to auto-fill from posts
- "Regenerate" preserves manually-edited tags, regenerates the rest
- Existing atmosphere section stays below, labeled "Communication Patterns (auto-generated)"

---

### Phase 2: Context-Aware Compilation

#### 2a. Contrastive few-shot example

**`src/automod/compiler/prompts.py`** — Add a third example to `COMPILE_FEW_SHOT_EXAMPLES`:
- Same rule ("Be respectful") compiled for two communities with different situational contexts
- Community A: casual social space, general public, low stakes → higher thresholds, colloquial rubric
- Community B: professional advice forum, vulnerable participants, high stakes → lower thresholds, precise rubric
- Each item shows `context_influenced: true` with `context_note` explaining the reasoning chain (situation → calibration decision)

#### 2b. Compilation instructions

**`src/automod/compiler/prompts.py`** — Add to `COMPILE_SYSTEM`:

```
COMMUNITY CONTEXT CALIBRATION:
When community context is provided, reason from the situation to your calibration choices:
- Read the PURPOSE to understand what "off-topic" or "low quality" means here.
- Read the PARTICIPANTS to understand who might be harmed and how. --> why harm only??
- Read the STAKES to calibrate how aggressive vs. conservative your thresholds should be.
- Read the TONE to match rubric language and example tone to the community's actual communication style.
For every item where context shaped your choice, set context_influenced=true and write a context_note
that traces your reasoning: "[situational fact] → [calibration decision]".
```

#### 2c. Restructured context in compile prompt

**`src/automod/compiler/prompts.py`** — Update `build_compile_prompt()`:

```
Community context for "{community_name}" ({platform}):

PURPOSE:
  {purpose.prose}
  [Tags: {purpose.tags}]

PARTICIPANTS:
  {participants.prose}
  [Tags: {participants.tags}]

STAKES:
  {stakes.prose}
  [Tags: {stakes.tags}]

TONE:
  {tone.prose}
  [Tags: {tone.tags}]

COMMUNICATION PATTERNS (auto-inferred from posts):
  Tone: ... | Typical content: ... | What belongs: ... | What doesn't: ... | Moderation style: ...

Representative posts: ...
```

#### 2d. Compiler interface changes

**`src/automod/compiler/compiler.py`**:
- `compile_rule()`: accept `community_context: dict | None` instead of `community_atmosphere`
- `_COMPILE_TOOL` schema: rename `atmosphere_influenced`/`atmosphere_note` → `context_influenced`/`context_note`

**`src/automod/api/rules.py`**:
- `_compile_rule_background()`: pass `community.community_context` (with fallback to `{"atmosphere": community.atmosphere}`)

---

### Phase 3: Context Through Alignment Flows

#### 3a–3c. Thread context through all alignment endpoints

**`src/automod/api/alignment.py`** — For each of these flows, pass `community.community_context`:
- `suggest_from_examples` → compiler generates context-calibrated checklist suggestions
- `suggest_from_checklist` → compiler generates examples matching community tone and content style
- `preview_recompile` / `recompile_with_diff` → recompilation is context-aware

**`src/automod/compiler/compiler.py`** + **`prompts.py`** — Each corresponding method and prompt builder accepts and renders community context.

#### 3d. Evaluation pipeline (future)

- Thread context to `SubjectiveEvaluator.evaluate_batch()` in `src/automod/core/subjective.py`
- Update `build_subjective_eval_prompt()` to include context
- Update community norms check in `src/automod/core/engine.py` to use full context instead of just atmosphere

---

### Phase 4: UI — Context Visibility

#### 4a. ChecklistTree context indicators

**`admin/src/components/ChecklistTree.tsx`**:
- Rename atmosphere badge → context badge
- Use `context_influenced` / `context_note` fields
- Tooltip on click shows full `context_note` (which now includes reasoning chain)
- Subtle left-border tint on context-influenced items

#### 4b. RuleEditor context summary

**`admin/src/pages/RuleEditor.tsx`**:
- Banner: "X of Y checklist items were influenced by community context"
- Warning when context has been updated since last compilation

---

### Files to Modify

| File | Changes |
|------|---------|
| `src/automod/db/models.py` | Add `community_context` column; rename `atmosphere_influenced`/`atmosphere_note` on ChecklistItem |
| `src/automod/models/schemas.py` | Add context schemas; update CommunityRead, ChecklistItemRead |
| `src/automod/api/communities.py` | Add context CRUD + generate endpoints |
| `src/automod/api/rules.py` | Pass full context to compiler |
| `src/automod/api/alignment.py` | Thread context through all alignment flows |
| `src/automod/compiler/compiler.py` | Accept `community_context`; rename fields in tool schema |
| `src/automod/compiler/prompts.py` | Contrastive few-shot; calibration instructions; context rendering; rename fields |
| `src/automod/core/engine.py` | Pass full context to norms check (Phase 3d) |
| `src/automod/core/subjective.py` | Accept context in evaluate_batch (Phase 3d) |
| `admin/src/pages/CommunitySettings.tsx` | Community context editor (4 cards + atmosphere) |
| `admin/src/components/ChecklistTree.tsx` | Context badges and indicators |
| `admin/src/pages/RuleEditor.tsx` | Context summary banner |
| `admin/src/api/client.ts` | New API calls for context CRUD + generate |

### Verification

1. **Data model**: Create community → set context via API → verify persistence and backward compat
2. **Context generation**: Add sample posts → call `/context/generate` → verify prose + tags are reasonable
3. **Compilation diff**: Compile same rule for two communities with different contexts → verify meaningfully different checklists with traced `context_note` reasoning
4. **Alignment**: Run suggest_from_examples with context → verify suggestions reference community situation
5. **UI**: Context editor saves; atmosphere generation works; context badges visible on checklist items; summary banner in rule editor
