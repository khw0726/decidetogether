import axios from 'axios'

const BASE_URL = '/api'

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

// ── Types ──────────────────────────────────────────────────────────────────────

export interface Community {
  id: string
  name: string
  platform: string
  platform_config: Record<string, unknown> | null
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
  updated_at: string
  children: ChecklistItem[]
}

export interface Example {
  id: string
  content: Record<string, unknown>
  label: 'positive' | 'negative' | 'borderline'
  source: string
  moderator_reasoning: string | null
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
  was_override: boolean
  created_at: string
  resolved_at: string | null
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
    media?: unknown[]
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
  api.post<{ suggestion_id: string; diff: Record<string, unknown> }>(`/rules/${ruleId}/recompile`).then(r => r.data)

export const acceptRecompile = (ruleId: string, suggestionId: string) =>
  api.post(`/rules/${ruleId}/recompile/accept`, null, { params: { suggestion_id: suggestionId } }).then(r => r.data)

// ── Examples ───────────────────────────────────────────────────────────────────

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

export const dismissSuggestion = (suggestionId: string) =>
  api.post<Suggestion>(`/suggestions/${suggestionId}/dismiss`).then(r => r.data)

// ── Decisions ──────────────────────────────────────────────────────────────────

export const listDecisions = (communityId: string, params?: { status?: string; verdict?: string; limit?: number; offset?: number }) =>
  api.get<Decision[]>(`/communities/${communityId}/decisions`, { params }).then(r => r.data)

export const resolveDecision = (decisionId: string, data: { verdict: string; reasoning_category?: string; notes?: string; rule_ids?: string[] }) =>
  api.put<Decision>(`/decisions/${decisionId}/resolve`, data).then(r => r.data)

export const getDecisionStats = (communityId: string) =>
  api.get<DecisionStats>(`/communities/${communityId}/decisions/stats`).then(r => r.data)

// ── Evaluation ─────────────────────────────────────────────────────────────────

export const evaluatePost = (communityId: string, post_content: PostContent) =>
  api.post<{ decision: Decision }>(`/communities/${communityId}/evaluate`, { post_content }).then(r => r.data)

export const evaluateBatch = (communityId: string, posts: PostContent[]) =>
  api.post<{ decisions: Decision[] }>(`/communities/${communityId}/evaluate/batch`, { posts }).then(r => r.data)
