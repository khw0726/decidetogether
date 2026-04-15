import axios from 'axios'

const BASE_URL = '/api'

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

// ── Types ──────────────────────────────────────────────────────────────────────

export interface CommunityAtmosphere {
  tone: string
  typical_content: string
  what_belongs: string
  what_doesnt_belong: string
  moderation_style: string
}

export interface Community {
  id: string
  name: string
  platform: string
  platform_config: Record<string, unknown> | null
  atmosphere: CommunityAtmosphere | null
  created_at: string
}

export interface AtmosphereGenerateResponse {
  community: Community
}

export interface CommunitySamplePost {
  id: string
  community_id: string
  content: Record<string, unknown>
  label: 'acceptable' | 'unacceptable'
  note: string | null
  created_at: string
}

export interface Rule {
  id: string
  community_id: string
  title: string
  text: string
  priority: number
  is_active: boolean
  rule_type: string
  rule_type_reasoning: string | null
  override_count: number
  created_at: string
  updated_at: string
}

export interface ChecklistItem {
  id: string
  rule_id: string
  order: number
  parent_id: string | null
  description: string
  rule_text_anchor: string | null
  item_type: 'deterministic' | 'structural' | 'subjective'
  logic: Record<string, unknown>
  action: string
  atmosphere_influenced: boolean
  atmosphere_note: string | null
  updated_at: string
  children: ChecklistItem[]
}

export interface Example {
  id: string
  content: Record<string, unknown>
  label: 'compliant' | 'violating' | 'borderline'
  source: string
  moderator_reasoning: string | null
  checklist_item_id: string | null
  checklist_item_description: string | null
  created_at: string
  updated_at: string
}

export interface Decision {
  id: string
  community_id: string
  post_content: Record<string, unknown>
  post_platform_id: string
  agent_verdict: string
  agent_confidence: number
  agent_reasoning: Record<string, unknown>
  triggered_rules: string[]
  moderator_verdict: string
  moderator_reasoning_category: string | null
  moderator_notes: string | null
  moderator_tag: string | null
  was_override: boolean
  created_at: string
  resolved_at: string | null
}

export interface PreviewRecompileResult {
  operations: Array<{
    op: 'keep' | 'update' | 'delete' | 'add'
    existing_id?: string
    description?: string
    rule_text_anchor?: string | null
    item_type?: string
    action?: string
    atmosphere_influenced?: boolean
    atmosphere_note?: string | null
  }>
  example_verdicts: Array<{
    example_id: string
    label: string
    content_title: string
    may_change: boolean
    affected_checklist_items: string[]
  }>
  summary: {
    keep: number
    update: number
    delete: number
    add: number
    examples_may_change: number
  }
}

export interface DraftEvaluationResult {
  example_id: string
  old_label: 'compliant' | 'violating' | 'borderline'
  new_verdict: 'approve' | 'remove' | 'review' | 'error'
  new_confidence: number
}

export interface DecisionStats {
  total_decisions: number
  pending_decisions: number
  resolved_decisions: number
  override_rate: number
  verdicts_breakdown: Record<string, number>
  override_categories: Record<string, number>
}

export interface Suggestion {
  id: string
  rule_id: string | null
  checklist_item_id: string | null
  suggestion_type: string
  content: Record<string, unknown>
  status: string
  created_at: string
}

export interface NewRuleSuggestion {
  warning?: string
  suggestion: Suggestion
}

export interface PostContent {
  id?: string
  platform?: string
  author?: {
    username?: string
    account_age_days?: number
    platform_metadata?: Record<string, unknown>
  }
  content?: {
    title?: string
    body?: string
    media?: string[]
    links?: string[]
  }
  context?: {
    channel?: string
    post_type?: string
    flair?: string | null
    platform_metadata?: Record<string, unknown>
  }
  timestamp?: string
}

// ── Community ──────────────────────────────────────────────────────────────────

export const listCommunities = () =>
  api.get<Community[]>('/communities').then(r => r.data)

export const createCommunity = (data: { name: string; platform: string; platform_config?: Record<string, unknown> }) =>
  api.post<Community>('/communities', data).then(r => r.data)

export const getCommunity = (id: string) =>
  api.get<Community>(`/communities/${id}`).then(r => r.data)

export const deleteCommunity = (id: string) =>
  api.delete(`/communities/${id}`)

export const generateAtmosphere = (communityId: string) =>
  api.post<AtmosphereGenerateResponse>(`/communities/${communityId}/atmosphere/generate`).then(r => r.data)

export const listSamplePosts = (communityId: string) =>
  api.get<CommunitySamplePost[]>(`/communities/${communityId}/sample-posts`).then(r => r.data)

export const addSamplePost = (
  communityId: string,
  data: { content: Record<string, unknown>; label: 'acceptable' | 'unacceptable'; note?: string }
) => api.post<CommunitySamplePost>(`/communities/${communityId}/sample-posts`, data).then(r => r.data)

export const deleteSamplePost = (communityId: string, postId: string) =>
  api.delete(`/communities/${communityId}/sample-posts/${postId}`)

export const importSamplePostFromUrl = (
  communityId: string,
  data: { url: string; label: 'acceptable' | 'unacceptable'; note?: string }
) =>
  api
    .post<CommunitySamplePost>(`/communities/${communityId}/sample-posts/import-url`, data)
    .then(r => r.data)

// ── Setup Status ──────────────────────────────────────────────────────────────

export interface BorderlineItem {
  suggestion_id: string
  rule_id: string
  rule_title: string
  content: Record<string, unknown>
  relevance_note: string
}

export interface SetupStatus {
  actionable_total: number
  compiled_count: number
  borderline_examples: BorderlineItem[]
}

export const getSetupStatus = (communityId: string) =>
  api.get<SetupStatus>(`/communities/${communityId}/setup-status`).then(r => r.data)

// ── Rules ──────────────────────────────────────────────────────────────────────

export const listRules = (communityId: string) =>
  api.get<Rule[]>(`/communities/${communityId}/rules`).then(r => r.data)

export const createRule = (communityId: string, data: { title: string; text: string; priority?: number }) =>
  api.post<Rule>(`/communities/${communityId}/rules`, data).then(r => r.data)

export const updateRule = (ruleId: string, data: Partial<Rule>) =>
  api.put<Rule>(`/rules/${ruleId}`, data).then(r => r.data)

export const updateRulePriority = (ruleId: string, priority: number) =>
  api.put<Rule>(`/rules/${ruleId}/priority`, { priority }).then(r => r.data)

export const overrideRuleType = (ruleId: string, ruleType: string, reasoning?: string) =>
  api.put<Rule>(`/rules/${ruleId}/rule-type`, { rule_type: ruleType, reasoning }).then(r => r.data)

export const deactivateRule = (ruleId: string) =>
  api.delete(`/rules/${ruleId}`)

export interface BatchImportRuleItem {
  title: string
  text: string
  priority?: number
}

export interface BatchImportResult {
  rule: Rule
  triage_error: string | null
}

export interface BatchImportResponse {
  imported: BatchImportResult[]
  total: number
  actionable_count: number
  skipped_count: number
}

export const batchImportRules = (communityId: string, rules: BatchImportRuleItem[]) =>
  api.post<BatchImportResponse>(`/communities/${communityId}/rules/batch`, { rules }).then(r => r.data)

export interface RedditRulesResponse {
  rules: { title: string; text: string }[]
  subreddit: string
}

export const fetchRedditRules = (subreddit: string) =>
  api.get<RedditRulesResponse>(`/reddit-rules/${encodeURIComponent(subreddit)}`).then(r => r.data)

// ── Checklist ──────────────────────────────────────────────────────────────────

export const getChecklist = (ruleId: string) =>
  api.get<ChecklistItem[]>(`/rules/${ruleId}/checklist`).then(r => r.data)

export const createChecklistItem = (ruleId: string, data: {
  description: string
  item_type?: string
  action?: string
  parent_id?: string | null
  rule_text_anchor?: string | null
}) => api.post<ChecklistItem>(`/rules/${ruleId}/checklist-items`, data).then(r => r.data)

export const updateChecklistItem = (itemId: string, data: Partial<ChecklistItem>) =>
  api.put<ChecklistItem>(`/checklist-items/${itemId}`, data).then(r => r.data)

export const deleteChecklistItem = (itemId: string) =>
  api.delete(`/checklist-items/${itemId}`)

export const recompileRule = (ruleId: string) =>
  api.post<{ suggestion_id: string | null; diff: Record<string, unknown> & { no_changes?: boolean } }>(`/rules/${ruleId}/recompile`).then(r => r.data)

export const acceptRecompile = (ruleId: string, suggestionId: string) =>
  api.post(`/rules/${ruleId}/recompile/accept`, null, { params: { suggestion_id: suggestionId } }).then(r => r.data)

export const previewRecompile = (ruleId: string, ruleText: string) =>
  api.post<PreviewRecompileResult>(`/rules/${ruleId}/preview-recompile`, { rule_text: ruleText }).then(r => r.data)

export const evaluateExamplesWithDraft = (ruleId: string, ruleText: string) =>
  api.post<DraftEvaluationResult[]>(`/rules/${ruleId}/evaluate-examples-with-draft`, { rule_text: ruleText }).then(r => r.data)

// ── Examples ───────────────────────────────────────────────────────────────────

export interface CommunityExample extends Example {
  rule_ids: string[]
  rule_titles: string[]
}

export const listCommunityExamples = (
  communityId: string,
  params?: { rule_id?: string; label?: string; source?: string },
) => api.get<CommunityExample[]>(`/communities/${communityId}/examples`, { params }).then(r => r.data)

export const listExamples = (ruleId: string, label?: string) =>
  api.get<Example[]>(`/rules/${ruleId}/examples`, { params: label ? { label } : {} }).then(r => r.data)

export const addExample = (ruleId: string, data: { content: Record<string, unknown>; label: string; source?: string; relevance_note?: string }) =>
  api.post<Example>(`/rules/${ruleId}/examples`, data).then(r => r.data)

export const updateExample = (exampleId: string, data: Partial<Example>) =>
  api.put<Example>(`/examples/${exampleId}`, data).then(r => r.data)

export const deleteExample = (exampleId: string) =>
  api.delete(`/examples/${exampleId}`)

// ── Alignment ──────────────────────────────────────────────────────────────────

export const suggestFromExamples = (ruleId: string) =>
  api.post<Suggestion[]>(`/rules/${ruleId}/suggest-from-examples`).then(r => r.data)

export const suggestFromChecklist = (ruleId: string) =>
  api.post<Suggestion[]>(`/rules/${ruleId}/suggest-from-checklist`).then(r => r.data)

export const listSuggestions = (ruleId: string, status?: string) =>
  api.get<Suggestion[]>(`/rules/${ruleId}/suggestions`, { params: status ? { status } : {} }).then(r => r.data)

export const acceptSuggestion = (suggestionId: string) =>
  api.post<Suggestion>(`/suggestions/${suggestionId}/accept`).then(r => r.data)

export const acceptSuggestionWithLabel = (suggestionId: string, labelOverride?: string) =>
  api.post<Suggestion>(
    `/suggestions/${suggestionId}/accept`,
    labelOverride ? { label_override: labelOverride } : {},
  ).then(r => r.data)

export const refreshSuggestions = (ruleId: string) =>
  api.post<Suggestion[]>(`/rules/${ruleId}/suggest-from-examples`).then(r => r.data)

export const dismissSuggestion = (suggestionId: string) =>
  api.post<Suggestion>(`/suggestions/${suggestionId}/dismiss`).then(r => r.data)

export const listUnlinkedOverrides = (communityId: string) =>
  api.get<Example[]>(`/communities/${communityId}/unlinked-overrides`).then(r => r.data)

export const suggestRuleFromOverrides = (communityId: string, exampleIds: string[]) =>
  api.post<NewRuleSuggestion>(
    `/communities/${communityId}/suggest-rule-from-overrides`,
    { example_ids: exampleIds },
  ).then(r => r.data)

export const suggestRuleFromDecisions = (communityId: string, decisionIds: string[]) =>
  api.post<NewRuleSuggestion>(
    `/communities/${communityId}/suggest-rule-from-decisions`,
    { decision_ids: decisionIds },
  ).then(r => r.data)

// ── Decisions ──────────────────────────────────────────────────────────────────

export const listDecisions = (communityId: string, params?: { status?: string; verdict?: string; limit?: number; offset?: number }) =>
  api.get<Decision[]>(`/communities/${communityId}/decisions`, { params }).then(r => r.data)

export const resolveDecision = (decisionId: string, data: { verdict: string; reasoning_category?: string; notes?: string; tag?: string; rule_ids?: string[] }) =>
  api.put<Decision>(`/decisions/${decisionId}/resolve`, data).then(r => r.data)

export const getDecisionStats = (communityId: string) =>
  api.get<DecisionStats>(`/communities/${communityId}/decisions/stats`).then(r => r.data)

// ── Health ─────────────────────────────────────────────────────────────────────

export interface ExampleSummary {
  example_id: string
  label: 'compliant' | 'violating' | 'borderline'
  title: string
}

export interface ErrorCase {
  decision_id: string
  title: string
  confidence: number
}

export interface ItemHealthMetrics {
  item_id: string
  description: string
  item_type: 'deterministic' | 'structural' | 'subjective'
  action: string
  sort_score: number
  false_positive_rate: number
  false_positive_count: number
  false_negative_rate: number
  false_negative_count: number
  avg_confidence_correct: number | null
  avg_confidence_errors: number | null
  decision_count: number
  examples: {
    compliant: ExampleSummary[]
    violating: ExampleSummary[]
    borderline: ExampleSummary[]
  }
  wrongly_flagged: ErrorCase[]
  missed_violations: ErrorCase[]
}

export interface RuleHealth {
  rule_id: string
  overall: {
    total_decisions: number
    override_rate: number
    covered_by_examples: number
  }
  items: ItemHealthMetrics[]
  uncovered_violations: ExampleSummary[]
}

export const getRuleHealth = (ruleId: string) =>
  api.get<RuleHealth>(`/rules/${ruleId}/health`).then(r => r.data)

export const analyzeRuleHealth = (ruleId: string) =>
  api.post<Suggestion[]>(`/rules/${ruleId}/analyze-health`).then(r => r.data)

// ── Evaluation ─────────────────────────────────────────────────────────────────

export const evaluatePost = (communityId: string, post_content: PostContent) =>
  api.post<{ decision: Decision }>(`/communities/${communityId}/evaluate`, { post_content }).then(r => r.data)

export const evaluateBatch = (communityId: string, posts: PostContent[]) =>
  api.post<{ decisions: Decision[] }>(`/communities/${communityId}/evaluate/batch`, { posts }).then(r => r.data)

// ── Sample Post Crawl ─────────────────────────────────────────────────────────

export interface CrawlSamplePostsResponse {
  posts: CommunitySamplePost[]
  crawled_count: number
}

export const crawlSamplePosts = (communityId: string) =>
  api.post<CrawlSamplePostsResponse>(`/communities/${communityId}/sample-posts/crawl`).then(r => r.data)

// ── Populate Queue ────────────────────────────────────────────────────────────

export interface PopulateQueueResponse {
  message: string
  task_started: boolean
}

export const populateQueue = (communityId: string) =>
  api.post<PopulateQueueResponse>(`/communities/${communityId}/populate-queue`).then(r => r.data)

// ── Reddit Import ─────────────────────────────────────────────────────────────

export interface RedditImportRequest {
  subreddit: string
  limit?: number
  time_filter?: string
}

export interface RedditImportResponse {
  decisions: Decision[]
  crawled_count: number
  evaluated_count: number
  skipped_count: number
}

export const importRedditPosts = (communityId: string, data: RedditImportRequest) =>
  api.post<RedditImportResponse>(`/communities/${communityId}/import-reddit`, data).then(r => r.data)
