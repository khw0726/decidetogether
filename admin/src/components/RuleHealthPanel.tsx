import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, AlertTriangle, CheckCircle, ChevronDown, ChevronUp, Zap, Eye, Check } from 'lucide-react'
import { showErrorToast } from './Toast'
import {
  getRuleHealth,
  analyzeRuleHealth,
  listSuggestions,
  acceptRecompile,
  dismissSuggestion,
  previewFixes,
  reevaluateDecisions,
  ItemHealthMetrics,
  ExampleSummary,
  ErrorCase,
  Suggestion,
  ImpactPreviewResult,
  ImpactEvaluation,
} from '../api/client'

const ACTION_LABELS: Record<string, string> = {
  tighten_rubric: 'Tighten Rubric',
  adjust_threshold: 'Adjust Threshold',
  promote_to_deterministic: 'Promote to Deterministic',
  split_item: 'Split Item',
  add_item: 'Add Item',
}

const ACTION_COLORS: Record<string, string> = {
  tighten_rubric: 'bg-amber-100 text-amber-800',
  adjust_threshold: 'bg-blue-100 text-blue-800',
  promote_to_deterministic: 'bg-violet-100 text-violet-800',
  split_item: 'bg-orange-100 text-orange-800',
  add_item: 'bg-green-100 text-green-800',
}

const ITEM_TYPE_COLORS: Record<string, string> = {
  deterministic: 'bg-violet-100 text-violet-700',
  structural: 'bg-cyan-100 text-cyan-700',
  subjective: 'bg-orange-100 text-orange-700',
}

const LABEL_COLORS: Record<string, string> = {
  compliant: 'bg-green-100 text-green-800',
  violating: 'bg-red-100 text-red-800',
  borderline: 'bg-amber-100 text-amber-800',
}

const HEALTH_ACTIONS = new Set([
  'tighten_rubric',
  'adjust_threshold',
  'promote_to_deterministic',
  'split_item',
  'add_item',
])

function pct(n: number) {
  return `${(n * 100).toFixed(0)}%`
}

function conf(n: number | null) {
  return n != null ? n.toFixed(2) : '—'
}

function isUnhealthy(item: ItemHealthMetrics) {
  return item.false_positive_rate > 0.15 || item.false_negative_rate > 0.15
}

// ── ExampleRow ──────────────────────────────────────────────────────────────────

function ExampleRow({ ex }: { ex: ExampleSummary }) {
  return (
    <div className="flex items-center gap-2 py-1 border-b border-gray-100 last:border-0">
      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0 ${LABEL_COLORS[ex.label] || 'bg-gray-100 text-gray-700'}`}>
        {ex.label}
      </span>
      <span className="text-xs text-gray-600 truncate">{ex.title}</span>
    </div>
  )
}

// ── ErrorCaseList ───────────────────────────────────────────────────────────────

const ERROR_CASE_COLORS: Record<string, { border: string; bg: string; label: string; text: string }> = {
  red:   { border: 'border-red-100', bg: 'bg-red-50', label: 'text-red-600', text: 'text-red-500' },
  amber: { border: 'border-amber-100', bg: 'bg-amber-50', label: 'text-amber-600', text: 'text-amber-500' },
}

function ErrorCaseList({
  label,
  sublabel,
  cases,
  color,
}: {
  label: string
  sublabel: string
  cases: ErrorCase[]
  color: 'red' | 'amber'
}) {
  const c = ERROR_CASE_COLORS[color]
  return (
    <div>
      <p className={`text-xs font-medium mb-0.5 ${c.label}`}>{label}</p>
      <p className={`text-[10px] mb-1 ${c.text}`}>{sublabel}</p>
      <div className={`border ${c.border} rounded ${c.bg} px-2 py-1 max-h-28 overflow-y-auto`}>
        {cases.map(cs => (
          <div key={cs.decision_id} className="py-1 border-b border-gray-100/50 last:border-0">
            <div className="flex items-center gap-2">
              <span className={`text-[10px] font-mono ${c.text} flex-shrink-0`}>
                {(cs.confidence * 100).toFixed(0)}%
              </span>
              <span className="text-xs text-gray-700 truncate">{cs.title}</span>
            </div>
            {cs.moderator_notes && (
              <p className="text-[10px] text-gray-400 italic ml-8 mt-0.5 truncate">
                {cs.moderator_reasoning_category && <span className="not-italic font-medium text-gray-500">{cs.moderator_reasoning_category}: </span>}
                {cs.moderator_notes}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Suggested Fixes Panel ──────────────────────────────────────────────────────

function FixCard({
  suggestion,
  ruleId,
  targetDescription,
  acceptingAll,
}: {
  suggestion: Suggestion
  ruleId: string
  targetDescription: string
  acceptingAll: boolean
}) {
  const queryClient = useQueryClient()
  const [applyPhase, setApplyPhase] = useState<null | 'accepting' | 'reevaluating'>(null)
  const content = suggestion.content as Record<string, unknown>
  const action = content.action as string
  const reasoning = content.reasoning as string
  const confidence = content.confidence as string | undefined
  const operations = content.operations as Array<Record<string, unknown>> | undefined
  const proposed = operations?.[0] as Record<string, unknown> | undefined
  const proposedDescription = proposed?.description as string | undefined

  const applyMutation = useMutation({
    mutationFn: async () => {
      setApplyPhase('accepting')
      await acceptRecompile(ruleId, suggestion.id)
      setApplyPhase('reevaluating')
      await reevaluateDecisions(ruleId)
    },
    onSuccess: () => {
      setApplyPhase(null)
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
    },
    onError: () => setApplyPhase(null),
  })

  const dismissMutation = useMutation({
    mutationFn: () => dismissSuggestion(suggestion.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
    },
  })

  const busy = applyMutation.isPending || dismissMutation.isPending || acceptingAll

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Action badge + confidence */}
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ACTION_COLORS[action] || 'bg-gray-100 text-gray-700'}`}>
              {ACTION_LABELS[action] || action}
            </span>
            {confidence && (
              <span className="text-xs text-gray-400">{confidence} confidence</span>
            )}
          </div>

          {/* Target item */}
          <p className="text-xs text-gray-500">
            <span className="font-medium text-gray-600">Target: </span>
            {targetDescription}
          </p>

          {/* Reasoning */}
          <p className="text-xs text-gray-700">{reasoning}</p>

          {/* Proposed change */}
          {proposedDescription && (
            <div className="text-xs bg-gray-50 border border-gray-100 rounded p-2 text-gray-600">
              <span className="font-medium">Proposed: </span>
              {proposedDescription}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-1 flex-shrink-0">
          <button
            className="btn-success text-xs py-1 px-2"
            onClick={() => applyMutation.mutate()}
            disabled={busy}
          >
            {applyMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : null}
            {applyPhase === 'accepting' ? 'Applying...' : applyPhase === 'reevaluating' ? 'Updating...' : 'Accept'}
          </button>
          <button
            className="btn-secondary text-xs py-1 px-2"
            onClick={() => dismissMutation.mutate()}
            disabled={busy}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}

function ImpactRow({ ev }: { ev: ImpactEvaluation }) {
  const icon = ev.fixed ? '✓' : ev.regressed ? '✗' : '—'
  const color = ev.fixed ? 'text-green-600' : ev.regressed ? 'text-red-600' : 'text-gray-400'
  const errorLabel = ev.error_type === 'wrongly_flagged' ? 'FP' : 'FN'

  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className={`text-xs font-bold w-4 text-center ${color}`}>{icon}</span>
      <span className="text-[10px] font-mono text-gray-400 flex-shrink-0">{errorLabel}</span>
      <span className="text-xs text-gray-700 truncate flex-1">{ev.title}</span>
      <span className="text-[10px] text-gray-400 flex-shrink-0">
        {ev.old_verdict} → {ev.new_verdict}
      </span>
    </div>
  )
}

function SuggestedFixesPanel({
  suggestions,
  ruleId,
  items,
}: {
  suggestions: Suggestion[]
  ruleId: string
  items: ItemHealthMetrics[]
}) {
  const queryClient = useQueryClient()
  const [acceptAllPhase, setAcceptAllPhase] = useState<null | 'accepting' | 'reevaluating'>(null)
  const [previewResult, setPreviewResult] = useState<ImpactPreviewResult | null>(null)

  const previewMutation = useMutation({
    mutationFn: () => previewFixes(ruleId),
    onSuccess: (data) => setPreviewResult(data),
  })

  // Build item description lookup
  const itemDescById = new Map<string, string>()
  for (const item of items) {
    itemDescById.set(item.item_id, item.description)
  }

  const handleAcceptAll = async () => {
    setAcceptAllPhase('accepting')
    try {
      for (const s of suggestions) {
        await acceptRecompile(ruleId, s.id)
      }
      setAcceptAllPhase('reevaluating')
      await reevaluateDecisions(ruleId)
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : 'Failed to accept fixes')
    } finally {
      setAcceptAllPhase(null)
      setPreviewResult(null)
    }
  }

  const acceptingAll = acceptAllPhase !== null

  return (
    <div className="border border-indigo-200 rounded-lg bg-indigo-50/30 p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Suggested Fixes ({suggestions.length})
        </p>
        <div className="flex gap-1.5">
          <button
            className="btn-secondary text-xs flex items-center gap-1.5 py-1 px-2"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || acceptingAll}
            title="Preview how applying all fixes would affect error cases"
          >
            {previewMutation.isPending
              ? <Loader2 size={10} className="animate-spin" />
              : <Eye size={10} />
            }
            {previewMutation.isPending ? 'Previewing...' : 'Preview'}
          </button>
          <button
            className="btn-success text-xs flex items-center gap-1.5 py-1 px-2"
            onClick={handleAcceptAll}
            disabled={acceptingAll}
          >
            {acceptingAll
              ? <Loader2 size={10} className="animate-spin" />
              : <Check size={10} />
            }
            {acceptAllPhase === 'accepting' ? 'Applying fixes...' : acceptAllPhase === 'reevaluating' ? 'Updating metrics...' : 'Accept all'}
          </button>
        </div>
      </div>

      {/* Fix cards */}
      <div className="space-y-2">
        {suggestions.map(s => {
          const targetDesc = s.checklist_item_id
            ? itemDescById.get(s.checklist_item_id) || 'Unknown item'
            : 'New item'
          return (
            <FixCard
              key={s.id}
              suggestion={s}
              ruleId={ruleId}
              targetDescription={targetDesc}
              acceptingAll={acceptingAll}
            />
          )
        })}
      </div>

      {/* Impact preview */}
      {previewResult && (
        <div className="border border-gray-200 rounded-lg p-3 bg-white space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
              Predicted Impact
            </p>
            <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => setPreviewResult(null)}>
              Close
            </button>
          </div>

          {previewResult.evaluations.length === 0 ? (
            <p className="text-xs text-gray-400 italic">No error cases to evaluate.</p>
          ) : (
            <>
              <div className="flex items-center gap-3 text-xs">
                {previewResult.summary.would_fix > 0 && (
                  <span className="text-green-600 font-semibold">
                    {previewResult.summary.would_fix}/{previewResult.summary.total_error_cases} fixed
                  </span>
                )}
                {previewResult.summary.would_remain > 0 && (
                  <span className="text-gray-500">
                    {previewResult.summary.would_remain} unchanged
                  </span>
                )}
                {previewResult.summary.would_regress > 0 && (
                  <span className="text-red-600 font-semibold">
                    {previewResult.summary.would_regress} regressed
                  </span>
                )}
              </div>
              <div className="max-h-48 overflow-y-auto space-y-0.5">
                {previewResult.evaluations.map(ev => (
                  <ImpactRow key={ev.decision_id} ev={ev} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── ItemHealthCard ──────────────────────────────────────────────────────────────

function ItemHealthCard({
  item,
  depth = 0,
}: {
  item: ItemHealthMetrics
  depth?: number
}) {
  const [expanded, setExpanded] = useState(isUnhealthy(item))
  const unhealthy = isUnhealthy(item)
  const totalExamples = [
    ...item.examples.compliant,
    ...item.examples.violating,
    ...item.examples.borderline,
  ]

  return (
    <div
      className={`border rounded-lg ${unhealthy ? 'border-amber-200' : 'border-gray-200'} ${depth > 0 ? 'border-l-2 border-l-gray-300' : ''}`}
      style={depth > 0 ? { marginLeft: depth * 20 } : undefined}
    >
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 p-3 text-left hover:bg-gray-50 rounded-t-lg"
        onClick={() => setExpanded(e => !e)}
      >
        {depth > 0 && <span className="text-gray-300 text-xs flex-shrink-0">↳</span>}
        {unhealthy
          ? <AlertTriangle size={14} className="text-amber-500 flex-shrink-0" />
          : <CheckCircle size={14} className="text-green-500 flex-shrink-0" />
        }
        <span className="text-sm font-medium text-gray-800 flex-1 min-w-0 truncate">
          {item.description}
        </span>
        <span className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${ITEM_TYPE_COLORS[item.item_type] || 'bg-gray-100 text-gray-600'}`}>
          {item.item_type}
        </span>
        {expanded ? <ChevronUp size={14} className="text-gray-400 flex-shrink-0" /> : <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-gray-100">
          {/* Metrics table */}
          <div className="grid grid-cols-3 gap-2 mt-3">
            <div className="bg-red-50 border border-red-100 rounded p-2 text-center">
              <p className="text-xs text-red-500 font-semibold">Wrongly Flagged</p>
              <p className="text-[10px] text-red-400">Triggered, but mod approved</p>
              <p className="text-base font-bold text-red-700">{pct(item.false_positive_rate)}</p>
              <p className="text-xs text-red-400">{item.false_positive_count}/{item.decision_count}</p>
              {item.avg_confidence_errors != null && (
                <p className="text-xs text-red-400 mt-0.5">conf: {conf(item.avg_confidence_errors)}</p>
              )}
            </div>
            <div className="bg-amber-50 border border-amber-100 rounded p-2 text-center">
              <p className="text-xs text-amber-600 font-semibold">Missed</p>
              <p className="text-[10px] text-amber-500">Didn't trigger, but mod removed</p>
              <p className="text-base font-bold text-amber-700">{pct(item.false_negative_rate)}</p>
              <p className="text-xs text-amber-500">{item.false_negative_count}/{item.decision_count}</p>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded p-2 text-center">
              <p className="text-xs text-gray-500 font-semibold">Decisions</p>
              <p className="text-base font-bold text-gray-700">{item.decision_count}</p>
              {item.avg_confidence_correct != null && (
                <p className="text-xs text-gray-400 mt-0.5">correct conf: {conf(item.avg_confidence_correct)}</p>
              )}
            </div>
          </div>

          {/* Wrongly flagged cases */}
          {item.wrongly_flagged.length > 0 && (
            <ErrorCaseList
              label="Wrongly Flagged"
              sublabel="Rule triggered but moderator approved these posts"
              cases={item.wrongly_flagged}
              color="red"
            />
          )}

          {/* Missed violation cases */}
          {item.missed_violations.length > 0 && (
            <ErrorCaseList
              label="Missed Violations"
              sublabel="Rule didn't trigger but moderator removed these posts"
              cases={item.missed_violations}
              color="amber"
            />
          )}

          {/* Linked examples */}
          {totalExamples.length > 0 && (
            <div>
              <p className="text-xs text-gray-400 font-medium mb-1">Linked examples</p>
              <div className="border border-gray-100 rounded bg-white px-2 py-1 max-h-28 overflow-y-auto">
                {totalExamples.slice(0, 8).map(ex => (
                  <ExampleRow key={ex.example_id} ex={ex} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── UncoveredViolations ─────────────────────────────────────────────────────────

function UncoveredViolations({ violations }: { violations: ExampleSummary[] }) {
  if (!violations.length) return null
  return (
    <div className="border border-gray-200 rounded-lg p-3">
      <p className="text-xs font-semibold text-gray-500 mb-2">
        Uncovered Violations ({violations.length}) — removed by moderators, no checklist item matches
      </p>
      <div className="border border-gray-100 rounded bg-white px-2 py-1">
        {violations.slice(0, 8).map(ex => (
          <ExampleRow key={ex.example_id} ex={ex} />
        ))}
      </div>
    </div>
  )
}

// ── Tree helpers ───────────────────────────────────────────────────────────────

function renderItemTree(items: ItemHealthMetrics[]) {
  const childrenOf = new Map<string | null, ItemHealthMetrics[]>()
  for (const item of items) {
    const pid = item.parent_id ?? null
    if (!childrenOf.has(pid)) childrenOf.set(pid, [])
    childrenOf.get(pid)!.push(item)
  }

  const elements: JSX.Element[] = []

  function walk(parentId: string | null, depth: number) {
    const children = childrenOf.get(parentId)
    if (!children) return
    for (const item of children) {
      elements.push(
        <ItemHealthCard key={item.item_id} item={item} depth={depth} />
      )
      walk(item.item_id, depth + 1)
    }
  }

  walk(null, 0)
  return elements
}

// ── RuleHealthPanel ─────────────────────────────────────────────────────────────

export default function RuleHealthPanel({ ruleId }: { ruleId: string }) {
  const queryClient = useQueryClient()

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['rule-health', ruleId],
    queryFn: () => getRuleHealth(ruleId),
  })

  const { data: suggestions } = useQuery({
    queryKey: ['suggestions', ruleId],
    queryFn: () => listSuggestions(ruleId, 'pending'),
  })

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeRuleHealth(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
    },
  })

  // Filter to health-type suggestions (by action in content)
  const healthSuggestions = (suggestions || []).filter(s => {
    const action = (s.content as Record<string, unknown>).action as string | undefined
    return s.suggestion_type === 'checklist' && action && HEALTH_ACTIONS.has(action)
  })

  if (healthLoading) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <Loader2 size={16} className="animate-spin text-gray-400" />
      </div>
    )
  }

  if (!health) {
    return (
      <div className="p-4 text-sm text-gray-500">Could not load health data.</div>
    )
  }

  const { overall, items, uncovered_violations } = health
  const unhealthyCount = items.filter(isUnhealthy).length

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      {/* Overall summary bar */}
      <div className="flex items-center gap-4 p-3 bg-gray-50 border border-gray-200 rounded-lg text-sm">
        <div className="flex-1 space-y-0.5">
          <div className="flex items-center gap-3 text-xs text-gray-600">
            <span><span className="font-semibold text-gray-800">{overall.total_decisions}</span> decisions</span>
            <span><span className="font-semibold text-gray-800">{pct(overall.override_rate)}</span> override rate</span>
            <span><span className="font-semibold text-gray-800">{pct(overall.covered_by_examples)}</span> items with examples</span>
            {unhealthyCount > 0 && (
              <span className="text-amber-600 font-semibold flex items-center gap-1">
                <AlertTriangle size={12} />
                {unhealthyCount} item{unhealthyCount !== 1 ? 's' : ''} need attention
              </span>
            )}
            {unhealthyCount === 0 && overall.total_decisions > 0 && (
              <span className="text-green-600 font-semibold flex items-center gap-1">
                <CheckCircle size={12} />
                All items healthy
              </span>
            )}
          </div>
        </div>
        <button
          className="btn-primary text-xs flex items-center gap-1.5 flex-shrink-0"
          onClick={() => analyzeMutation.mutate()}
          disabled={analyzeMutation.isPending}
          title="Run LLM analysis to diagnose issues and generate fix suggestions"
        >
          {analyzeMutation.isPending
            ? <Loader2 size={12} className="animate-spin" />
            : <Zap size={12} />
          }
          {analyzeMutation.isPending ? 'Analyzing...' : 'Analyze'}
        </button>
      </div>

      {overall.total_decisions === 0 && (
        <p className="text-sm text-gray-400 text-center py-4">
          No resolved decisions yet. Evaluate some posts and resolve them to see health metrics.
        </p>
      )}

      {/* Suggested Fixes panel */}
      {healthSuggestions.length > 0 && (
        <SuggestedFixesPanel
          suggestions={healthSuggestions}
          ruleId={ruleId}
          items={items}
        />
      )}

      {/* Item cards — rendered hierarchically */}
      {items.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Item Health</p>
          {renderItemTree(items)}
        </div>
      )}

      {/* Uncovered violations */}
      <UncoveredViolations violations={uncovered_violations} />
    </div>
  )
}
