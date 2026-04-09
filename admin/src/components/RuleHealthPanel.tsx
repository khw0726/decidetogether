import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, AlertTriangle, CheckCircle, ChevronDown, ChevronUp, Zap } from 'lucide-react'
import {
  getRuleHealth,
  analyzeRuleHealth,
  listSuggestions,
  acceptRecompile,
  dismissSuggestion,
  ItemHealthMetrics,
  ExampleSummary,
  Suggestion,
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

// ── DiagnosisCard ───────────────────────────────────────────────────────────────

function DiagnosisCard({
  suggestion,
  ruleId,
  onApplied,
}: {
  suggestion: Suggestion
  ruleId: string
  onApplied: () => void
}) {
  const queryClient = useQueryClient()
  const content = suggestion.content as Record<string, unknown>
  const action = content.action as string
  const reasoning = content.reasoning as string
  const confidence = content.confidence as string | undefined
  const operations = content.operations as Array<Record<string, unknown>> | undefined
  const proposed = operations?.[0] as Record<string, unknown> | undefined

  const proposedDescription = proposed?.description as string | undefined

  const applyMutation = useMutation({
    mutationFn: () => acceptRecompile(ruleId, suggestion.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
      onApplied()
    },
  })

  const dismissMutation = useMutation({
    mutationFn: () => dismissSuggestion(suggestion.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
    },
  })

  return (
    <div className="mt-3 border border-gray-200 rounded-lg p-3 bg-gray-50">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ACTION_COLORS[action] || 'bg-gray-100 text-gray-700'}`}>
              {ACTION_LABELS[action] || action}
            </span>
            {confidence && (
              <span className="text-xs text-gray-400">{confidence} confidence</span>
            )}
          </div>
          <p className="text-xs text-gray-700 mb-2">{reasoning}</p>
          {proposedDescription && (
            <div className="text-xs bg-white border border-gray-200 rounded p-2 text-gray-600">
              <span className="font-medium">Proposed: </span>
              {proposedDescription}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1 flex-shrink-0">
          <button
            className="btn-success text-xs py-1 px-2"
            onClick={() => applyMutation.mutate()}
            disabled={applyMutation.isPending || dismissMutation.isPending}
          >
            {applyMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : null}
            Apply fix
          </button>
          <button
            className="btn-secondary text-xs py-1 px-2"
            onClick={() => dismissMutation.mutate()}
            disabled={applyMutation.isPending || dismissMutation.isPending}
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}

// ── ItemHealthCard ──────────────────────────────────────────────────────────────

function ItemHealthCard({
  item,
  diagnosis,
  ruleId,
}: {
  item: ItemHealthMetrics
  diagnosis: Suggestion | undefined
  ruleId: string
}) {
  const [expanded, setExpanded] = useState(isUnhealthy(item))
  const unhealthy = isUnhealthy(item)
  const totalExamples = [
    ...item.examples.compliant,
    ...item.examples.violating,
    ...item.examples.borderline,
  ]

  return (
    <div className={`border rounded-lg ${unhealthy ? 'border-amber-200' : 'border-gray-200'}`}>
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 p-3 text-left hover:bg-gray-50 rounded-t-lg"
        onClick={() => setExpanded(e => !e)}
      >
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
              <p className="text-xs text-red-500 font-semibold">False Positives</p>
              <p className="text-base font-bold text-red-700">{pct(item.false_positive_rate)}</p>
              <p className="text-xs text-red-400">{item.false_positive_count}/{item.decision_count}</p>
              {item.avg_confidence_errors != null && (
                <p className="text-xs text-red-400 mt-0.5">conf: {conf(item.avg_confidence_errors)}</p>
              )}
            </div>
            <div className="bg-amber-50 border border-amber-100 rounded p-2 text-center">
              <p className="text-xs text-amber-600 font-semibold">False Negatives</p>
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

          {/* Diagnosis */}
          {diagnosis && (
            <DiagnosisCard
              suggestion={diagnosis}
              ruleId={ruleId}
              onApplied={() => setExpanded(false)}
            />
          )}
          {!diagnosis && unhealthy && (
            <p className="text-xs text-gray-400 italic">
              Run "Analyze" to get a diagnosis for this item.
            </p>
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

  // Map from checklist_item_id → suggestion
  const diagnosisByItemId = new Map<string, Suggestion>()
  for (const s of healthSuggestions) {
    if (s.checklist_item_id) {
      diagnosisByItemId.set(s.checklist_item_id, s)
    }
  }

  // add_item suggestions (no checklist_item_id)
  const addItemSuggestions = healthSuggestions.filter(s => !s.checklist_item_id)

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

      {/* Item cards */}
      {items.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Item Health</p>
          {items.map(item => (
            <ItemHealthCard
              key={item.item_id}
              item={item}
              diagnosis={diagnosisByItemId.get(item.item_id)}
              ruleId={ruleId}
            />
          ))}
        </div>
      )}

      {/* add_item suggestions */}
      {addItemSuggestions.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Suggested New Items</p>
          {addItemSuggestions.map(s => (
            <DiagnosisCard
              key={s.id}
              suggestion={s}
              ruleId={ruleId}
              onApplied={() => {}}
            />
          ))}
        </div>
      )}

      {/* Uncovered violations */}
      <UncoveredViolations violations={uncovered_violations} />
    </div>
  )
}
