import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, ChevronUp, CheckCircle, XCircle, AlertTriangle, Filter, Inbox, Loader2, Sparkles, Download, X,
} from 'lucide-react'
import { showErrorToast } from '../components/Toast'
import {
  listDecisions, resolveDecision, bulkResolveDecisions, Decision, listRules,
  suggestRuleFromDecisions, acceptSuggestion, dismissSuggestion, NewRuleSuggestion,
  importRedditPosts, RedditImportResponse, getCommunity,
} from '../api/client'
import PostCard from '../components/PostCard'
import NewRuleSuggestionModal from '../components/NewRuleSuggestionModal'

interface DecisionQueueProps {
  communityId: string
}

export default function DecisionQueue({ communityId }: DecisionQueueProps) {
  const [filter, setFilter] = useState<'pending' | 'resolved' | 'all'>('pending')
  const [verdictFilter, setVerdictFilter] = useState<string>('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState<NewRuleSuggestion | null>(null)
  const [showImportModal, setShowImportModal] = useState(false)

  const queryClient = useQueryClient()

  const toggleSelect = (id: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) { next.delete(id) } else { next.add(id) }
      return next
    })

  const acceptMutation = useMutation({
    mutationFn: (suggestionId: string) => acceptSuggestion(suggestionId),
    onSuccess: () => {
      setSuggestion(null)
      setSelectedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
    },
  })

  const dismissMutation = useMutation({
    mutationFn: (suggestionId: string) => dismissSuggestion(suggestionId),
    onSuccess: () => setSuggestion(null),
  })

  const handleSuggestRule = async () => {
    if (selectedIds.size === 0) return
    setSuggesting(true)
    try {
      const result = await suggestRuleFromDecisions(communityId, [...selectedIds])
      setSuggestion(result)
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : 'Failed to generate suggestion')
    } finally {
      setSuggesting(false)
    }
  }

  const { data: community } = useQuery({
    queryKey: ['community', communityId],
    queryFn: () => getCommunity(communityId),
    enabled: !!communityId,
  })

  const isReddit = community?.platform === 'reddit'

  const { data: decisions = [], isLoading } = useQuery({
    queryKey: ['decisions', communityId, filter, verdictFilter],
    queryFn: () =>
      listDecisions(communityId, {
        status: filter === 'all' ? undefined : filter,
        verdict: verdictFilter || undefined,
        limit: 50,
      }),
    enabled: !!communityId,
    refetchInterval: 30_000,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const rulesMap = Object.fromEntries(rules.map(r => [r.id, r]))

  const bulkResolveMutation = useMutation({
    mutationFn: ({ verdict }: { verdict: string }) =>
      bulkResolveDecisions(communityId, {
        decision_ids: [...selectedIds],
        verdict,
      }),
    onSuccess: () => {
      setSelectedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['decisions', communityId] })
      queryClient.invalidateQueries({ queryKey: ['stats', communityId] })
    },
  })

  const resolveMutation = useMutation({
    mutationFn: ({
      decisionId,
      verdict,
      reasoningCategory,
      notes,
      tag,
      ruleIds,
    }: {
      decisionId: string
      verdict: string
      reasoningCategory?: string
      notes?: string
      tag?: string
      ruleIds?: string[]
    }) => resolveDecision(decisionId, { verdict, reasoning_category: reasoningCategory, notes, tag, rule_ids: ruleIds }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['decisions', communityId] })
      queryClient.invalidateQueries({ queryKey: ['stats', communityId] })
    },
  })

  if (!communityId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <p>Select a community to view decisions.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-200 bg-white">
        <h1 className="font-semibold text-gray-900">Decision Queue</h1>
        <div className="flex items-center gap-2 ml-4">
          {(['pending', 'resolved', 'all'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                filter === f
                  ? 'bg-indigo-600 text-white border-indigo-600'
                  : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {selectedIds.size > 0 && (
            <>
              <button
                className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded border border-green-300 text-green-700 font-medium hover:bg-green-50 transition-colors"
                onClick={() => bulkResolveMutation.mutate({ verdict: 'approve' })}
                disabled={bulkResolveMutation.isPending}
              >
                {bulkResolveMutation.isPending && bulkResolveMutation.variables?.verdict === 'approve'
                  ? <Loader2 size={12} className="animate-spin" />
                  : <CheckCircle size={12} />}
                Approve All ({selectedIds.size})
              </button>
              <button
                className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded border border-red-300 text-red-700 font-medium hover:bg-red-50 transition-colors"
                onClick={() => bulkResolveMutation.mutate({ verdict: 'remove' })}
                disabled={bulkResolveMutation.isPending}
              >
                {bulkResolveMutation.isPending && bulkResolveMutation.variables?.verdict === 'remove'
                  ? <Loader2 size={12} className="animate-spin" />
                  : <XCircle size={12} />}
                Reject All ({selectedIds.size})
              </button>
              <button
                className="btn-primary text-xs flex items-center gap-1.5"
                onClick={handleSuggestRule}
                disabled={suggesting}
              >
                {suggesting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                Suggest Rule ({selectedIds.size})
              </button>
            </>
          )}
          {isReddit && (
            <button
              className="btn-secondary text-xs flex items-center gap-1.5"
              onClick={() => setShowImportModal(true)}
            >
              <Download size={12} />
              Import from Reddit
            </button>
          )}
          <Filter size={14} className="text-gray-400" />
          <select
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none"
            value={verdictFilter}
            onChange={e => setVerdictFilter(e.target.value)}
          >
            <option value="">All verdicts</option>
            <option value="approve">Approve</option>
            <option value="remove">Remove</option>
          </select>
        </div>
      </div>

      {/* Decision cards */}
      <div className="flex-1 overflow-auto p-6 space-y-4">
        {isLoading && (
          <div className="flex items-center justify-center py-12 text-gray-400">
            <Loader2 size={24} className="animate-spin mr-2" />
            Loading decisions...
          </div>
        )}
        {!isLoading && decisions.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-gray-400">
            <Inbox size={48} className="mb-4 opacity-30" />
            <p className="text-lg font-medium">No decisions</p>
            <p className="text-sm mt-1">
              {filter === 'pending' ? 'No pending decisions to review.' : 'No decisions found.'}
            </p>
          </div>
        )}
        {decisions.map(decision => (
          <DecisionCard
            key={decision.id}
            decision={decision}
            rulesMap={rulesMap}
            selected={selectedIds.has(decision.id)}
            onToggleSelect={() => toggleSelect(decision.id)}
            onResolve={(verdict, reasoningCategory, notes, ruleIds, tag) =>
              resolveMutation.mutate({
                decisionId: decision.id,
                verdict,
                reasoningCategory,
                notes,
                tag,
                ruleIds,
              })
            }
            resolving={resolveMutation.isPending && resolveMutation.variables?.decisionId === decision.id}
          />
        ))}

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
      {showImportModal && community && (
        <RedditImportModal
          communityId={communityId}
          communityName={community.name}
          onClose={() => setShowImportModal(false)}
          onImported={() => {
            queryClient.invalidateQueries({ queryKey: ['decisions', communityId] })
          }}
        />
      )}
      </div>
    </div>
  )
}

function RedditImportModal({
  communityId,
  communityName,
  onClose,
  onImported,
}: {
  communityId: string
  communityName: string
  onClose: () => void
  onImported: () => void
}) {
  // Derive subreddit name from community name (strip "r/" prefix if present)
  const defaultSubreddit = communityName.replace(/^r\//i, '')
  const [subreddit, setSubreddit] = useState(defaultSubreddit)
  const [limit, setLimit] = useState(25)
  const [timeFilter, setTimeFilter] = useState('month')
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<RedditImportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleImport = async () => {
    if (!subreddit.trim()) return
    setImporting(true)
    setError(null)
    try {
      const res = await importRedditPosts(communityId, {
        subreddit: subreddit.trim(),
        limit,
        time_filter: timeFilter,
      })
      setResult(res)
      onImported()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Import failed'
      const axiosMsg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosMsg || msg)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">Import Posts from Reddit</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {!result ? (
            <>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Subreddit</label>
                <div className="flex items-center gap-1">
                  <span className="text-sm text-gray-400">r/</span>
                  <input
                    className="flex-1 text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    value={subreddit}
                    onChange={e => setSubreddit(e.target.value)}
                    placeholder="subreddit name"
                  />
                </div>
              </div>

              <div className="flex gap-4">
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-700 mb-1">Posts to fetch</label>
                  <input
                    type="number"
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    value={limit}
                    onChange={e => setLimit(Math.max(1, Math.min(100, Number(e.target.value))))}
                    min={1}
                    max={100}
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-700 mb-1">Time filter</label>
                  <select
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    value={timeFilter}
                    onChange={e => setTimeFilter(e.target.value)}
                  >
                    <option value="hour">Past hour</option>
                    <option value="day">Past day</option>
                    <option value="week">Past week</option>
                    <option value="month">Past month</option>
                    <option value="year">Past year</option>
                    <option value="all">All time</option>
                  </select>
                </div>
              </div>

              {error && (
                <div className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
                <button
                  className="btn-primary text-xs flex items-center gap-1.5"
                  onClick={handleImport}
                  disabled={importing || !subreddit.trim()}
                >
                  {importing ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                  {importing ? 'Importing...' : 'Import & Evaluate'}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <CheckCircle size={16} className="text-green-500" />
                  <span className="font-medium">Import complete</span>
                </div>
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div className="bg-gray-50 rounded p-2">
                    <div className="text-lg font-semibold text-gray-900">{result.crawled_count}</div>
                    <div className="text-xs text-gray-500">Crawled</div>
                  </div>
                  <div className="bg-green-50 rounded p-2">
                    <div className="text-lg font-semibold text-green-700">{result.evaluated_count}</div>
                    <div className="text-xs text-gray-500">Evaluated</div>
                  </div>
                  <div className="bg-amber-50 rounded p-2">
                    <div className="text-lg font-semibold text-amber-700">{result.skipped_count}</div>
                    <div className="text-xs text-gray-500">Skipped</div>
                  </div>
                </div>
                <p className="text-xs text-gray-500">
                  Skipped posts were already in the decision queue.
                </p>
              </div>
              <div className="flex justify-end pt-2">
                <button className="btn-primary text-xs" onClick={onClose}>Done</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ItemReasoningTree({
  itemReasoning,
  parentId,
  depth,
}: {
  itemReasoning: Record<string, unknown>
  parentId: string | null
  depth: number
}) {
  const items = Object.entries(itemReasoning).filter(([, itemR]) => {
    const ir = itemR as Record<string, unknown>
    return (ir.parent_id ?? null) === parentId
  })

  if (items.length === 0) return null

  return (
    <>
      {items.map(([itemId, itemR]) => {
        const ir = itemR as Record<string, unknown>
        return (
          <div key={itemId} style={{ marginLeft: depth * 12 }}>
            <div className={`pl-3 border-l-2 ${ir.triggered ? 'border-red-300' : 'border-green-300'}`}>
              <span className="text-gray-500">{ir.description as string}: </span>
              <span className={ir.triggered ? 'text-red-700' : 'text-green-700'}>
                {ir.reasoning as string}
              </span>
            </div>
            <ItemReasoningTree
              itemReasoning={itemReasoning}
              parentId={itemId}
              depth={depth + 1}
            />
          </div>
        )
      })}
    </>
  )
}

function RuleReasoningBlock({
  ruleId,
  ruleTitle,
  verdict,
  confidence,
  itemReasoning,
  defaultOpen,
}: {
  ruleId: string
  ruleTitle?: string
  verdict: string
  confidence?: number
  itemReasoning?: Record<string, unknown>
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="rounded border border-gray-200 text-xs overflow-hidden">
      <button
        className={`w-full flex items-center gap-2 px-3 py-2 text-left ${open ? 'bg-gray-50' : 'bg-gray-50/50 hover:bg-gray-50'}`}
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronUp size={12} className="text-gray-400 flex-shrink-0" /> : <ChevronDown size={12} className="text-gray-400 flex-shrink-0" />}
        <span className="font-medium truncate">{ruleTitle || ruleId}</span>
        <span className={`badge ${verdict === 'approve' ? 'badge-green' : verdict === 'remove' ? 'badge-red' : 'badge-yellow'}`}>
          {verdict}
        </span>
        <span className="text-gray-400 ml-auto flex-shrink-0">{Math.round((confidence || 0) * 100)}%</span>
      </button>
      {open && itemReasoning && (
        <div className="px-3 py-2 space-y-1 border-t border-gray-100">
          <ItemReasoningTree
            itemReasoning={itemReasoning}
            parentId={null}
            depth={0}
          />
        </div>
      )}
    </div>
  )
}

function DecisionCard({
  decision,
  rulesMap,
  selected,
  onToggleSelect,
  onResolve,
  resolving,
}: {
  decision: Decision
  rulesMap: Record<string, { title: string }>
  selected: boolean
  onToggleSelect: () => void
  onResolve: (verdict: string, reasoningCategory?: string, notes?: string, ruleIds?: string[], tag?: string) => void
  resolving: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [selectedVerdict, setSelectedVerdict] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([])

  // Rule picker is needed when agent approved (no triggered rules) but moderator disagrees
  const agentApproved = decision.agent_verdict === 'approve'
  const needsRulePicker = agentApproved && selectedVerdict && selectedVerdict !== 'approve'
  const isOverride = selectedVerdict && selectedVerdict !== decision.agent_verdict

  const isPending = decision.moderator_verdict === 'pending'

  const verdictColors: Record<string, string> = {
    approve: 'bg-green-100 text-green-800 border-green-200',
    warn: 'bg-amber-100 text-amber-800 border-amber-200',
    remove: 'bg-red-100 text-red-800 border-red-200',
    review: 'bg-purple-100 text-purple-800 border-purple-200',
    pending: 'bg-gray-100 text-gray-700 border-gray-200',
  }

  const confidenceColor =
    decision.agent_confidence >= 0.85
      ? 'text-green-600'
      : decision.agent_confidence >= 0.6
      ? 'text-amber-600'
      : 'text-red-600'

  const handleResolve = () => {
    if (!selectedVerdict) return
    onResolve(
      selectedVerdict,
      undefined,
      isOverride ? (notes || undefined) : undefined,
      needsRulePicker ? selectedRuleIds : undefined,
    )
    setSelectedVerdict(null)
    setSelectedRuleIds([])
    setNotes('')
  }

  return (
    <div className={`card overflow-hidden ${selected ? 'ring-2 ring-indigo-400' : ''} ${decision.was_override ? 'border-amber-200' : ''}`}>
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            className="mt-1 flex-shrink-0 cursor-pointer"
            checked={selected}
            onChange={onToggleSelect}
          />
          <div className="flex-1 min-w-0">
            <PostCard post={decision.post_content} compact />
          </div>
          <div className="flex flex-col items-end gap-2 flex-shrink-0">
            <div className={`badge border ${verdictColors[decision.agent_verdict] || verdictColors.pending}`}>
              Agent: {decision.agent_verdict}
            </div>
            <div className={`text-xs font-mono ${confidenceColor}`}>
              {(decision.agent_confidence * 100).toFixed(0)}% confidence
            </div>
            {decision.was_override && (
              <span className="badge badge-yellow">Override</span>
            )}
          </div>
        </div>

        {/* Triggered rules */}
        {decision.triggered_rules.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {decision.triggered_rules.map(ruleId => (
              <span key={ruleId} className="badge badge-gray">
                {rulesMap[ruleId]?.title || ruleId}
              </span>
            ))}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 mt-3">
          {isPending ? (
            <>
              <button
                className={`btn-success text-xs ${selectedVerdict === 'approve' ? 'ring-2 ring-green-500' : ''}`}
                onClick={() => setSelectedVerdict('approve')}
              >
                <CheckCircle size={13} />
                Approve
              </button>
              <button
                className={`text-xs px-2.5 py-1.5 rounded-md font-medium inline-flex items-center gap-1.5 border border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 ${selectedVerdict === 'warn' ? 'ring-2 ring-amber-500' : ''}`}
                onClick={() => setSelectedVerdict('warn')}
              >
                <AlertTriangle size={13} />
                Warn
              </button>
              <button
                className={`btn-danger text-xs ${selectedVerdict === 'remove' ? 'ring-2 ring-red-500' : ''}`}
                onClick={() => setSelectedVerdict('remove')}
              >
                <XCircle size={13} />
                Remove
              </button>
            </>
          ) : (
            <div className={`badge border ${verdictColors[decision.moderator_verdict] || verdictColors.pending}`}>
              Resolved: {decision.moderator_verdict}
            </div>
          )}

          <div className="flex-1" />

          <button
            className="text-xs text-gray-400 hover:text-gray-700 flex items-center gap-1"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {expanded ? 'Less' : 'Reasoning'}
          </button>
        </div>

        {/* Resolution form */}
        {selectedVerdict && isPending && (
          <div className="mt-3 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
            {needsRulePicker && (
              <div>
                <p className="text-xs text-amber-700 font-medium mb-1">
                  Agent did not trigger any rules — which rule(s) does this post violate? (leave blank if no rule applies)
                </p>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {Object.entries(rulesMap).map(([id, rule]) => (
                    <label key={id} className="flex items-center gap-2 text-xs cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedRuleIds.includes(id)}
                        onChange={e => setSelectedRuleIds(prev =>
                          e.target.checked ? [...prev, id] : prev.filter(r => r !== id)
                        )}
                      />
                      {rule.title}
                    </label>
                  ))}
                </div>
              </div>
            )}
            {isOverride && (
              <input
                className="w-full text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none"
                placeholder="Quick note on why you're overriding (optional)"
                value={notes}
                onChange={e => setNotes(e.target.value)}
              />
            )}
            <div className="flex gap-2">
              <button
                className="btn-primary text-xs py-1"
                onClick={handleResolve}
                disabled={resolving}
              >
                {resolving ? <Loader2 size={12} className="animate-spin" /> : null}
                Confirm: {selectedVerdict}
              </button>
              <button
                className="btn-secondary text-xs py-1"
                onClick={() => setSelectedVerdict(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Expanded reasoning */}
        {expanded && (
          <div className="mt-3 space-y-1">
            {Object.entries(decision.agent_reasoning || {}).map(([ruleId, reasoning]) => {
              const r = reasoning as Record<string, unknown>
              const verdict = r.verdict as string
              return (
                <RuleReasoningBlock
                  key={ruleId}
                  ruleId={ruleId}
                  ruleTitle={r.rule_title as string}
                  verdict={verdict}
                  confidence={r.confidence as number}
                  itemReasoning={r.item_reasoning as Record<string, unknown> | undefined}
                  defaultOpen={verdict !== 'approve'}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
