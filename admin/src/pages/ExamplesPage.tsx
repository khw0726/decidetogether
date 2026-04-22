import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2, Sparkles, ThumbsUp, ThumbsDown, Minus, Trash2 } from 'lucide-react'
import { showErrorToast } from '../components/Toast'
import {
  listCommunityExamples,
  listRules,
  updateExample,
  deleteExample,
  suggestRuleFromOverrides,
  acceptSuggestion,
  dismissSuggestion,
  getRulesHealthSummary,
  CommunityExample,
  Example,
  NewRuleSuggestion,
  RuleHealthSummary,
} from '../api/client'
import NewRuleSuggestionModal from '../components/NewRuleSuggestionModal'
import RuleHealthPanel from '../components/RuleHealthPanel'

interface ExamplesPageProps {
  communityId: string
}

const LABELS = ['compliant', 'violating', 'borderline'] as const
type Label = typeof LABELS[number]

const labelConfig: Record<Label, { badge: string; icon: React.ReactNode; color: string }> = {
  compliant: { badge: 'badge-green', icon: <ThumbsUp size={12} />, color: 'text-green-700' },
  violating: { badge: 'badge-red', icon: <ThumbsDown size={12} />, color: 'text-red-700' },
  borderline: { badge: 'badge-yellow', icon: <Minus size={12} />, color: 'text-yellow-700' },
}

export default function ExamplesPage({ communityId }: ExamplesPageProps) {
  const [searchParams] = useSearchParams()
  const initialRuleId = searchParams.get('rule_id') || ''
  const [selectedRuleId, setSelectedRuleId] = useState<string>(initialRuleId)
  const [selectedUnlinkedIds, setSelectedUnlinkedIds] = useState<Set<string>>(new Set())
  const [suggestion, setSuggestion] = useState<NewRuleSuggestion | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const queryClient = useQueryClient()

  const { data: examples = [], isLoading } = useQuery({
    queryKey: ['community-examples', communityId],
    queryFn: () => listCommunityExamples(communityId, {}),
    enabled: !!communityId,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const { data: healthSummaries = [] } = useQuery({
    queryKey: ['rules-health-summary', communityId],
    queryFn: () => getRulesHealthSummary(communityId),
    enabled: !!communityId,
  })

  const healthByRule = Object.fromEntries(
    healthSummaries.map((h: RuleHealthSummary) => [h.rule_id, h])
  )

  const deleteMutation = useMutation({
    mutationFn: deleteExample,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['community-examples', communityId] }),
  })

  const resolveMutation = useMutation({
    mutationFn: ({ id, label }: { id: string; label: string }) =>
      updateExample(id, { label } as Partial<Example>),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['community-examples', communityId] }),
  })

  const acceptMutation = useMutation({
    mutationFn: (suggestionId: string) => acceptSuggestion(suggestionId),
    onSuccess: () => {
      setSuggestion(null)
      setSelectedUnlinkedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['community-examples', communityId] })
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
    },
  })

  const dismissMutation = useMutation({
    mutationFn: (suggestionId: string) => dismissSuggestion(suggestionId),
    onSuccess: () => setSuggestion(null),
  })

  const unlinked = examples.filter(e => e.rule_ids.length === 0)
  const activeRules = rules.filter(r => r.is_active).sort((a, b) => a.priority - b.priority)

  // Auto-select first rule if none selected
  const effectiveRuleId = selectedRuleId || activeRules[0]?.id || ''
  const selectedRuleExamples = examples.filter(e => e.rule_ids.includes(effectiveRuleId))

  const handleSuggest = async () => {
    if (selectedUnlinkedIds.size === 0) return
    setSuggesting(true)
    try {
      const result = await suggestRuleFromOverrides(communityId, [...selectedUnlinkedIds])
      setSuggestion(result)
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : 'Failed to generate suggestion')
    } finally {
      setSuggesting(false)
    }
  }

  const toggleUnlinkedSelect = (id: string) => {
    setSelectedUnlinkedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (!communityId) {
    return (
      <div className="p-8 text-center text-gray-400 text-sm">
        Select a community to view rule health.
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <div className={`flex-shrink-0 border-r border-gray-200 bg-white flex flex-col transition-all duration-200 ${sidebarOpen ? 'w-64' : 'w-8'}`}>
        {sidebarOpen ? (
          <>
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h2 className="text-sm font-semibold text-gray-700">Rules</h2>
              <button
                className="p-0.5 text-gray-400 hover:text-gray-600"
                onClick={() => setSidebarOpen(false)}
              >
                <ChevronLeft size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-auto">
              {activeRules.map(rule => {
                const isSelected = effectiveRuleId === rule.id
                const health = healthByRule[rule.id] as RuleHealthSummary | undefined
                const errorRate = health?.error_rate ?? 0
                const noData = !health || health.decision_count === 0

                const severityBadge = noData
                  ? 'bg-gray-100 text-gray-400'
                  : errorRate === 0
                    ? 'bg-green-100 text-green-700'
                    : errorRate < 0.2
                      ? 'bg-yellow-100 text-yellow-700'
                      : 'bg-red-100 text-red-700'

                const severityLabel = noData
                  ? '—'
                  : `${Math.round(errorRate * 100)}%`

                return (
                  <button
                    key={rule.id}
                    className={`w-full text-left px-4 py-2.5 border-l-2 transition-colors flex items-center gap-2 ${
                      isSelected
                        ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                        : 'border-transparent text-gray-600 hover:bg-gray-50 hover:text-gray-800'
                    }`}
                    onClick={() => setSelectedRuleId(rule.id)}
                  >
                    <span className="text-sm truncate flex-1">{rule.title}</span>
                    <span className={`text-xs rounded-full px-1.5 py-0.5 flex-shrink-0 font-medium ${severityBadge}`}
                      title={noData ? 'No decisions yet' : `${health.error_count} errors / ${health.decision_count} decisions`}
                    >
                      {severityLabel}
                    </span>
                  </button>
                )
              })}
              {unlinked.length > 0 && (
                <button
                  className={`w-full text-left px-4 py-2.5 border-l-2 transition-colors flex items-center gap-2 ${
                    effectiveRuleId === '__unlinked'
                      ? 'border-amber-500 bg-amber-50 text-amber-700'
                      : 'border-transparent text-amber-500 hover:bg-amber-50 hover:text-amber-700'
                  }`}
                  onClick={() => setSelectedRuleId('__unlinked')}
                >
                  <AlertTriangle size={12} className="flex-shrink-0" />
                  <span className="text-sm truncate flex-1">Unlinked</span>
                  <span className="text-xs bg-amber-100 text-amber-700 rounded-full px-1.5 py-0.5 flex-shrink-0">{unlinked.length}</span>
                </button>
              )}
            </div>
          </>
        ) : (
          <button
            className="mt-2 mx-auto p-1 text-gray-400 hover:text-gray-600"
            onClick={() => setSidebarOpen(true)}
          >
            <ChevronRight size={14} />
          </button>
        )}
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-white">
          <h1 className="text-lg font-semibold text-gray-900">Rule Health & Examples</h1>
          <p className="text-xs text-gray-500 mt-0.5">Review rule performance, analyze issues, and manage labeled examples</p>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto">
          {isLoading && (
            <div className="text-sm text-gray-400 text-center py-8">Loading...</div>
          )}

          {/* Unlinked tab content */}
          {!isLoading && effectiveRuleId === '__unlinked' && (
            <div className="p-6 space-y-4">
              <div className="border border-amber-200 rounded-lg bg-amber-50">
                <div className="flex items-center gap-2 px-4 py-3">
                  <AlertTriangle size={14} className="text-amber-600 flex-shrink-0" />
                  <span className="text-sm font-medium text-amber-900">
                    Unlinked Examples ({unlinked.length})
                  </span>
                  <span className="text-xs text-amber-700 ml-1">
                    — agent missed these, may need a new rule
                  </span>
                  <div className="flex-1" />
                  {selectedUnlinkedIds.size > 0 && selectedUnlinkedIds.size < 3 && (
                    <span className="text-xs text-amber-700 flex items-center gap-1 mr-2">
                      <AlertTriangle size={11} />
                      Fewer than 3 — may over-fit
                    </span>
                  )}
                  <button
                    className="btn-primary text-xs flex items-center gap-1.5"
                    disabled={selectedUnlinkedIds.size === 0 || suggesting}
                    onClick={handleSuggest}
                  >
                    {suggesting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                    Suggest Rule{selectedUnlinkedIds.size > 0 ? ` (${selectedUnlinkedIds.size})` : ''}
                  </button>
                </div>
                <div className="px-4 pb-4 space-y-2">
                  {unlinked.map(ex => (
                    <ExampleCard
                      key={ex.id}
                      example={ex}
                      selectable
                      selected={selectedUnlinkedIds.has(ex.id)}
                      onSelect={() => toggleUnlinkedSelect(ex.id)}
                      onDelete={() => deleteMutation.mutate(ex.id)}
                      onResolve={(label) => resolveMutation.mutate({ id: ex.id, label })}
                      resolving={resolveMutation.isPending}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Rule tab content: health panel + examples */}
          {!isLoading && effectiveRuleId && effectiveRuleId !== '__unlinked' && (
            <div className="flex flex-col">
              {/* Health panel */}
              <div className="border-b border-gray-200">
                <RuleHealthPanel ruleId={effectiveRuleId} />
              </div>

              {/* Examples for this rule */}
              <div className="p-6 space-y-2">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
                  Labeled Examples ({selectedRuleExamples.length})
                </p>
                {selectedRuleExamples.length === 0 && (
                  <div className="text-sm text-gray-400 text-center py-4 italic">
                    No examples linked to this rule yet.
                  </div>
                )}
                {selectedRuleExamples.map(ex => (
                  <ExampleCard
                    key={ex.id}
                    example={ex}
                    onDelete={() => deleteMutation.mutate(ex.id)}
                    onResolve={(label) => resolveMutation.mutate({ id: ex.id, label })}
                    resolving={resolveMutation.isPending}
                  />
                ))}
              </div>
            </div>
          )}

          {!isLoading && activeRules.length === 0 && unlinked.length === 0 && (
            <div className="text-sm text-gray-400 text-center py-8 italic">
              No rules or examples yet.
            </div>
          )}
        </div>
      </div>

      {suggestion && (
        <NewRuleSuggestionModal
          result={suggestion}
          onAccept={() => acceptMutation.mutate(suggestion.suggestion.id)}
          onDismiss={() => dismissMutation.mutate(suggestion.suggestion.id)}
          accepting={acceptMutation.isPending}
          dismissing={dismissMutation.isPending}
          onClose={() => setSuggestion(null)}
        />
      )}
    </div>
  )
}

function ExampleCard({
  example,
  selectable,
  selected,
  onSelect,
  onDelete,
  onResolve,
  resolving,
}: {
  example: CommunityExample
  selectable?: boolean
  selected?: boolean
  onSelect?: () => void
  onDelete: () => void
  onResolve: (label: string) => void
  resolving: boolean
}) {
  const content = example.content as Record<string, unknown>
  const postContent = (content.content as Record<string, unknown>) ?? content
  const title = (postContent.title as string) || ''
  const body = (postContent.body as string) || ''
  const label = example.label as Label

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white flex items-start gap-2">
      {selectable && (
        <input
          type="checkbox"
          className="mt-0.5 flex-shrink-0"
          checked={selected}
          onChange={onSelect}
        />
      )}
      <span className={`badge ${labelConfig[label]?.badge ?? 'badge-gray'} flex-shrink-0`}>
        {label}
      </span>
      <div className="flex-1 min-w-0">
        {title && <p className="text-sm font-medium truncate">{title}</p>}
        {body && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{body}</p>}
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-gray-400">Source: {example.source}</span>
        </div>
        {example.moderator_reasoning && (
          <p className="text-xs text-gray-600 mt-1 italic">{example.moderator_reasoning}</p>
        )}
        {label === 'borderline' && (
          <div className="flex gap-1 mt-1.5">
            <button
              className="btn-success text-xs py-0.5"
              onClick={() => onResolve('compliant')}
              disabled={resolving}
            >
              Compliant
            </button>
            <button
              className="btn-danger text-xs py-0.5"
              onClick={() => onResolve('violating')}
              disabled={resolving}
            >
              Violating
            </button>
          </div>
        )}
      </div>
      <button
        className="flex-shrink-0 p-1 text-gray-400 hover:text-red-600 rounded"
        onClick={onDelete}
        title="Delete example"
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}
