import axios from 'axios'

const BASE_URL = '/api'

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

// ── Types ──────────────────────────────────────────────────────────────────────

export interface CommunityContextNote {
  text: string
  tag: string
}

export interface CommunityContextDimension {
  notes: CommunityContextNote[]
  manually_edited?: boolean
}

export interface CommunityContext {
  purpose?: CommunityContextDimension
  participants?: CommunityContextDimension
  stakes?: CommunityContextDimension
  tone?: CommunityContextDimension
}

export interface ContextSamplePost {
  title?: string
  body?: string
  score?: number
  num_comments?: number
}

export interface ContextSamples {
  hot: ContextSamplePost[]
  top: ContextSamplePost[]
  controversial: ContextSamplePost[]
  ignored: ContextSamplePost[]
  comments: { body: string; score: number }[]
}

export interface Community {
  id: string
  name: string
  platform: string
  platform_config: Record<string, unknown> | null
  community_context: CommunityContext | null
  context_samples: ContextSamples | null
  created_at: string
}

export interface CommunitySamplePost {
  id: string
  community_id: string
  content: Record<string, unknown>
  label: 'acceptable' | 'unacceptable'
  note: string | null
  created_at: string
}

export interface RuleContextTag {
  dimension: string  // "purpose" | "participants" | "stakes" | "tone"
  tag: string
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
  applies_to: string  // "posts" | "comments" | "both"
  override_count: number
  base_checklist_json: Record<string, unknown> | null
  context_adjustment_summary: string[] | null
  relevant_context: RuleContextTag[] | null
  custom_context_notes: CommunityContextNote[]
  pending_checklist_json: Array<Record<string, unknown>> | null
  pending_context_adjustment_summary: string[] | null
  pending_relevant_context: { value: RuleContextTag[] | null } | null
  pending_custom_context_notes: CommunityContextNote[] | null
  pending_generated_at: string | null
  created_at: string
  updated_at: string
}

export interface PreviewChecklistItem {
  id: string
  order: number
  parent_id: string | null
  description: string
  rule_text_anchor: string | null
  item_type: 'deterministic' | 'structural' | 'subjective'
  logic: Record<string, unknown>
  action: string
  context_influenced: boolean
  context_note: string | null
  context_change_types: string[] | null
  base_description: string | null
  context_pinned: boolean
  context_override_note: string | null
  pinned_tags: RuleContextTag[] | null
  children: PreviewChecklistItem[]
}

export interface ContextPreviewResponse {
  preview_items: PreviewChecklistItem[]
  summary: string[] | null
  generated_at: string
  current_items: ChecklistItem[]
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
  context_influenced: boolean
  context_note: string | null
  context_change_types: string[] | null
  base_description: string | null
  context_pinned: boolean
  context_override_note: string | null
  pinned_tags: RuleContextTag[] | null
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
    context_influenced?: boolean
    context_note?: string | null
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
  new_verdict: 'approve' | 'warn' | 'remove' | 'review' | 'error'
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

export const getCommunityContext = (communityId: string) =>
  api.get<CommunityContext>(`/communities/${communityId}/context`).then(r => r.data)

export const updateCommunityContext = (communityId: string, data: Partial<CommunityContext>) =>
  api.put<CommunityContext>(`/communities/${communityId}/context`, data).then(r => r.data)

export const generateCommunityContext = (communityId: string) =>
  api.post<{ community_context: CommunityContext }>(`/communities/${communityId}/context/generate`).then(r => r.data)

export const reapplyContext = (communityId: string) =>
  api.post<{ rules_updated: number; summaries: Record<string, string> }>(`/communities/${communityId}/reapply-context`).then(r => r.data)

export interface ContextPreviewImpact {
  rules_affected: number
  impacts: Array<{ rule_id: string; rule_title: string; adjustment_summary: string[] | string }>
}

export const previewContextImpact = (communityId: string, draftContext: Partial<CommunityContext>) =>
  api.post<ContextPreviewImpact>(`/communities/${communityId}/context/preview-impact`, draftContext).then(r => r.data)

export type ContextTaxonomy = Record<string, Record<string, string>>

export const getContextTaxonomy = () =>
  api.get<ContextTaxonomy>('/communities/context-taxonomy').then(r => r.data)

export const getContextSamples = (communityId: string) =>
  api.get<{ context_samples: ContextSamples }>(`/communities/${communityId}/context-samples`).then(r => r.data)

export const crawlContextSamples = (communityId: string) =>
  api.post<{ context_samples: ContextSamples }>(`/communities/${communityId}/context-samples/crawl`).then(r => r.data)

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

export const previewContextAdjustment = (ruleId: string) =>
  api.post<ContextPreviewResponse>(`/rules/${ruleId}/context-preview`).then(r => r.data)

export const commitContextAdjustment = (ruleId: string) =>
  api.post<Rule>(`/rules/${ruleId}/context-commit`).then(r => r.data)

export const discardContextPreview = (ruleId: string) =>
  api.delete<Rule>(`/rules/${ruleId}/context-preview`).then(r => r.data)

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

export const setContextOverride = (
  itemId: string,
  pinned: boolean,
  overrideNote?: string,
  pinnedTags?: RuleContextTag[],
) =>
  api.patch<ChecklistItem>(`/checklist-items/${itemId}/context-override`, {
    pinned,
    override_note: overrideNote,
    pinned_tags: pinnedTags,
  }).then(r => r.data)

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

export const listSuggestions = (ruleId: string, status?: string) =>
  api.get<Suggestion[]>(`/rules/${ruleId}/suggestions`, { params: status ? { status } : {} }).then(r => r.data)

export const acceptSuggestion = (suggestionId: string) =>
  api.post<Suggestion>(`/suggestions/${suggestionId}/accept`).then(r => r.data)

export const acceptSuggestionWithLabel = (suggestionId: string, labelOverride?: string) =>
  api.post<Suggestion>(
    `/suggestions/${suggestionId}/accept`,
    labelOverride ? { label_override: labelOverride } : {},
  ).then(r => r.data)

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

export interface BulkResolveResponse {
  resolved_count: number
  failed_ids: string[]
}

export const bulkResolveDecisions = (communityId: string, data: { decision_ids: string[]; verdict: string; notes?: string; tag?: string }) =>
  api.put<BulkResolveResponse>(`/communities/${communityId}/decisions/bulk-resolve`, data).then(r => r.data)

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
  moderator_notes?: string
  moderator_reasoning_category?: string
}

export interface ItemHealthMetrics {
  item_id: string
  parent_id: string | null
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

export interface RuleHealthSummary {
  rule_id: string
  decision_count: number
  error_count: number
  error_rate: number
}

export const getRulesHealthSummary = (communityId: string) =>
  api.get<RuleHealthSummary[]>(`/communities/${communityId}/rules-health-summary`).then(r => r.data)

export const analyzeRuleHealth = (ruleId: string) =>
  api.post<Suggestion[]>(`/rules/${ruleId}/analyze-health`).then(r => r.data)

export const reevaluateDecisions = (ruleId: string) =>
  api.post<{ reevaluated: number }>(`/rules/${ruleId}/reevaluate`).then(r => r.data)

// ── Impact Preview ─────────────────────────────────────────────────────────────

export interface ImpactEvaluation {
  decision_id: string
  title: string
  error_type: 'wrongly_flagged' | 'missed_violation'
  source_item_id: string
  moderator_verdict: string
  old_verdict: string
  new_verdict: string
  new_confidence: number
  fixed: boolean
  regressed: boolean
}

export interface ImpactPreviewResult {
  evaluations: ImpactEvaluation[]
  summary: {
    total_error_cases: number
    would_fix: number
    would_remain: number
    would_regress: number
  }
}

export const previewFixes = (ruleId: string) =>
  api.post<ImpactPreviewResult>(`/rules/${ruleId}/preview-fixes`).then(r => r.data)

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
