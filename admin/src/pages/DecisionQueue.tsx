import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, ChevronUp, CheckCircle, XCircle, AlertTriangle, Filter, Inbox, Loader2, MessageSquare, Sparkles, Download, X,
} from 'lucide-react'
import { showErrorToast } from '../components/Toast'
import {
  listDecisions, resolveDecision, bulkResolveDecisions, Decision, listRules,
  suggestRuleFromDecisions, acceptSuggestion, dismissSuggestion, NewRuleSuggestion,
  importRedditPosts, RedditImportResponse, getCommunity,
  scenarioImportNext, scenarioImportStatus,
} from '../api/client'
import PostCard from '../components/PostCard'
import NewRuleSuggestionModal from '../components/NewRuleSuggestionModal'
import RuleIntentChat from '../components/RuleIntentChat'
import RuleReasoningBlock from '../components/RuleReasoningBlock'
import { useImportProgress } from '../contexts/ImportProgress'
import { logEvent } from '../telemetry'

interface DecisionQueueProps {
  communityId: string
}

export default function DecisionQueue({ communityId }: DecisionQueueProps) {
  const [searchParams] = useSearchParams()
  const [filter, setFilter] = useState<'pending' | 'resolved' | 'all'>(
    () => {
      const s = searchParams.get('status')
      return s === 'resolved' || s === 'all' ? s : 'pending'
    },
  )
  const [verdictFilter, setVerdictFilter] = useState<string>('')
  const [ruleFilter, setRuleFilter] = useState<string>(() => searchParams.get('rule_id') ?? '')
  const [contentTypeFilter, setContentTypeFilter] = useState<'' | 'post' | 'comment'>('')
  const highlightDecisionId = searchParams.get('decision_id') ?? null
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState<NewRuleSuggestion | null>(null)
  const [showImportModal, setShowImportModal] = useState(false)
  // Import progress lives in a global context so polling and the loader survive
  // page changes (and even reloads, via sessionStorage).
  const { importInFlight, arrivedCount, startImport } = useImportProgress()

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
  const isHypothetical = community?.platform === 'hypothetical'

  const { data: scenarioStatus } = useQuery({
    queryKey: ['scenario-import-status', communityId],
    queryFn: () => scenarioImportStatus(communityId),
    enabled: !!communityId && community?.platform === 'hypothetical',
    // Refetch while an import is being evaluated so the count reflects new arrivals.
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return false
      return importInFlight ? 3_000 : false
    },
  })

  const scenarioImportMutation = useMutation({
    mutationFn: () => scenarioImportNext(communityId),
    onSuccess: (resp) => {
      queryClient.invalidateQueries({ queryKey: ['scenario-import-status', communityId] })
      if (resp.imported_count === 0) {
        showErrorToast('No more queue posts left to import for this scenario.')
        return
      }
      // Decisions appear as the background task evaluates them. Latch into the
      // global "evaluating" state so the loader stays up until they actually
      // arrive — and survives page changes.
      startImport({
        communityId,
        baselineCount: decisions.length,
        expected: resp.imported_count,
      })
      queryClient.invalidateQueries({ queryKey: ['decisions', communityId] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(detail || (e instanceof Error ? e.message : 'Failed to import next batch'))
    },
  })

  const { data: decisions = [], isLoading } = useQuery({
    queryKey: ['decisions', communityId, filter, verdictFilter, ruleFilter, contentTypeFilter],
    queryFn: () =>
      listDecisions(communityId, {
        status: filter === 'all' ? undefined : filter,
        verdict: verdictFilter || undefined,
        rule_id: ruleFilter || undefined,
        content_type: contentTypeFilter || undefined,
        limit: 50,
      }),
    enabled: !!communityId,
    // Poll fast while a scenario import is being evaluated so the queue
    // reflects new arrivals quickly. The context also polls in the background
    // so the latch clears even when this page isn't mounted.
    refetchInterval: importInFlight ? 2_000 : 30_000,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const rulesMap = Object.fromEntries(rules.map(r => [r.id, r]))

  // Reeval status (banner + auto-refetch on completion) is now driven globally
  // by ReevalStatusProvider — no per-page polling needed.

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
                data-log="decision.bulk.approve"
                className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded border border-green-300 text-green-700 font-medium hover:bg-green-50 transition-colors"
                onClick={() => {
                  logEvent('decision.bulk.approve', { count: selectedIds.size, decision_ids: [...selectedIds] })
                  bulkResolveMutation.mutate({ verdict: 'approve' })
                }}
                disabled={bulkResolveMutation.isPending}
              >
                {bulkResolveMutation.isPending && bulkResolveMutation.variables?.verdict === 'approve'
                  ? <Loader2 size={12} className="animate-spin" />
                  : <CheckCircle size={12} />}
                Approve All ({selectedIds.size})
              </button>
              <button
                data-log="decision.bulk.remove"
                className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded border border-red-300 text-red-700 font-medium hover:bg-red-50 transition-colors"
                onClick={() => {
                  logEvent('decision.bulk.remove', { count: selectedIds.size, decision_ids: [...selectedIds] })
                  bulkResolveMutation.mutate({ verdict: 'remove' })
                }}
                disabled={bulkResolveMutation.isPending}
              >
                {bulkResolveMutation.isPending && bulkResolveMutation.variables?.verdict === 'remove'
                  ? <Loader2 size={12} className="animate-spin" />
                  : <XCircle size={12} />}
                Reject All ({selectedIds.size})
              </button>
              <button
                data-log="rule.suggest-from-decisions"
                className="btn-primary text-xs flex items-center gap-1.5"
                onClick={() => {
                  logEvent('rule.suggest-from-decisions', { count: selectedIds.size, decision_ids: [...selectedIds] })
                  handleSuggestRule()
                }}
                disabled={suggesting}
              >
                {suggesting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                Suggest Rule ({selectedIds.size})
              </button>
            </>
          )}
          {isReddit && (
            <button
              data-log="reddit.import.open-modal"
              className="btn-secondary text-xs flex items-center gap-1.5"
              onClick={() => setShowImportModal(true)}
            >
              <Download size={12} />
              Import from Reddit
            </button>
          )}
          {isHypothetical && (() => {
            const exhausted = !!scenarioStatus && scenarioStatus.remaining_count === 0
            const remaining = scenarioStatus?.remaining_count ?? null
            const total = scenarioStatus?.total_count ?? null
            const nextBatch = remaining != null ? Math.min(25, remaining) : 25
            return (
              <button
                data-log="scenario.import-next-batch"
                data-log-context={JSON.stringify({ remaining, total, next_batch: nextBatch })}
                className="btn-secondary text-xs flex items-center gap-1.5"
                disabled={scenarioImportMutation.isPending || !!importInFlight || exhausted}
                onClick={() => scenarioImportMutation.mutate()}
                title={
                  exhausted && total != null
                    ? `All ${total} scenario posts have been imported.`
                    : remaining != null
                      ? `${remaining} of ${total} scenario posts remaining.`
                      : ''
                }
              >
                {(scenarioImportMutation.isPending || importInFlight)
                  ? <Loader2 size={12} className="animate-spin" />
                  : <Download size={12} />}
                {importInFlight
                  ? `Evaluating ${Math.min(arrivedCount, importInFlight.expected)}/${importInFlight.expected}…`
                  : exhausted && total != null
                    ? `All ${total} imported`
                    : remaining != null
                      ? `Load ${nextBatch} more (${remaining} left)`
                      : 'Load 25 more'}
              </button>
            )
          })()}
          <Filter size={14} className="text-gray-400" />
          <span className="text-xs text-gray-500">Agent verdict:</span>
          <select
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none"
            value={verdictFilter}
            onChange={e => setVerdictFilter(e.target.value)}
          >
            <option value="">All</option>
            <option value="approve">Approve</option>
            <option value="remove">Remove</option>
            <option value="review">Review</option>
          </select>
          <span className="text-xs text-gray-500">Content:</span>
          <select
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none"
            value={contentTypeFilter}
            onChange={e => setContentTypeFilter(e.target.value as '' | 'post' | 'comment')}
            title="Show only posts or only comments"
          >
            <option value="">Posts & comments</option>
            <option value="post">Posts only</option>
            <option value="comment">Comments only</option>
          </select>
          <span className="text-xs text-gray-500">Violated rule:</span>
          <select
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none max-w-[180px]"
            value={ruleFilter}
            onChange={e => setRuleFilter(e.target.value)}
            title="Show only decisions where this rule was violated (agent triggered or moderator linked)"
          >
            <option value="">Any rule</option>
            {rules.filter(r => r.rule_type === 'actionable').map(r => (
              <option key={r.id} value={r.id}>{r.title}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Decision cards */}
      <div className="flex-1 overflow-auto p-6 space-y-4 max-w-5xl w-full mx-auto">
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
            highlighted={decision.id === highlightDecisionId}
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
  const [sort, setSort] = useState<'new' | 'top'>('new')
  const [timeFilter, setTimeFilter] = useState('month')
  const [includeComments, setIncludeComments] = useState(true)
  const [commentsLimit, setCommentsLimit] = useState(25)
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
        sort,
        time_filter: timeFilter,
        include_comments: includeComments,
        comments_limit: commentsLimit,
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
                  <label className="block text-xs font-medium text-gray-700 mb-1">Sort</label>
                  <select
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    value={sort}
                    onChange={e => setSort(e.target.value as 'new' | 'top')}
                  >
                    <option value="new">New</option>
                    <option value="top">Top</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-700 mb-1">Posts to fetch</label>
                  <input
                    type="number"
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    value={limit}
                    onChange={e => setLimit(Math.max(0, Math.min(100, Number(e.target.value))))}
                    min={0}
                    max={100}
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-700 mb-1">Time filter</label>
                  <select
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:bg-gray-50 disabled:text-gray-400"
                    value={timeFilter}
                    onChange={e => setTimeFilter(e.target.value)}
                    disabled={sort !== 'top'}
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

              <div className="flex items-end gap-4">
                <label className="flex items-center gap-2 text-xs font-medium text-gray-700 flex-1">
                  <input
                    type="checkbox"
                    className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    checked={includeComments}
                    onChange={e => setIncludeComments(e.target.checked)}
                  />
                  Include recent comments
                </label>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-700 mb-1">Comments to fetch</label>
                  <input
                    type="number"
                    className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:bg-gray-50 disabled:text-gray-400"
                    value={commentsLimit}
                    onChange={e => setCommentsLimit(Math.max(0, Math.min(100, Number(e.target.value))))}
                    min={0}
                    max={100}
                    disabled={!includeComments}
                  />
                </div>
              </div>

              {error && (
                <div className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
                <button
                  data-log="reddit.import.start"
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

function DecisionCard({
  decision,
  rulesMap,
  selected,
  highlighted,
  onToggleSelect,
  onResolve,
  resolving,
}: {
  decision: Decision
  rulesMap: Record<string, { title: string }>
  selected: boolean
  highlighted?: boolean
  onToggleSelect: () => void
  onResolve: (verdict: string, reasoningCategory?: string, notes?: string, ruleIds?: string[], tag?: string) => void
  resolving: boolean
}) {
  const [expanded, setExpanded] = useState(!!highlighted)
  const cardRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (highlighted) {
      cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [highlighted])
  const [selectedVerdict, setSelectedVerdict] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([])
  // The rule whose chat thread is open inline on this decision card.
  // Anchored to decision.id so the translator sees the post as context.
  const [chatRuleId, setChatRuleId] = useState<string | null>(null)

  // Rule picker is needed when the agent did not attribute the post to any rule
  // (verdict approve, or review = community-norms flag) but the moderator removes/warns.
  const agentMissedViolation = decision.agent_verdict === 'approve' || decision.agent_verdict === 'review'
  const needsRulePicker = agentMissedViolation && selectedVerdict && selectedVerdict !== 'approve'
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

  const handleVerdictClick = (verdict: string) => {
    if (resolving) return
    // If user matches the agent's verdict, no override form needed — resolve immediately.
    if (verdict === decision.agent_verdict) {
      onResolve(verdict, undefined, undefined, undefined)
      return
    }
    setSelectedVerdict(verdict)
  }

  return (
    <div
      ref={cardRef}
      data-log-context={JSON.stringify({ decision_id: decision.id, agent_verdict: decision.agent_verdict })}
      className={`card overflow-hidden ${selected ? 'ring-2 ring-indigo-400' : ''} ${highlighted ? 'ring-2 ring-yellow-400' : ''} ${decision.was_override ? 'border-amber-200' : ''}`}
    >
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            data-log="decision.toggle-select"
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
              <span
                key={ruleId}
                className="badge badge-gray inline-flex items-center gap-1"
                data-log-context={JSON.stringify({ rule_id: ruleId })}
              >
                {rulesMap[ruleId]?.title || ruleId}
                <button
                  type="button"
                  data-log="decision.rule-chat.toggle"
                  className={`ml-1 rounded p-0.5 transition-colors ${
                    chatRuleId === ruleId
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'text-gray-400 hover:text-indigo-600 hover:bg-indigo-50'
                  }`}
                  title="Think out loud about this rule (anchored to this post)"
                  onClick={e => {
                    e.stopPropagation()
                    setChatRuleId(prev => (prev === ruleId ? null : ruleId))
                  }}
                >
                  <MessageSquare size={10} />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Inline rule-intent chat (anchored to this post) */}
        {chatRuleId && (
          <div className="mt-3 border border-indigo-200 rounded-lg overflow-hidden bg-white">
            <div className="flex items-center justify-between px-3 py-1.5 bg-indigo-50 border-b border-indigo-200">
              <span className="text-xs font-semibold text-indigo-700">
                Chatting about: {rulesMap[chatRuleId]?.title || chatRuleId}
              </span>
              <button
                type="button"
                className="text-indigo-500 hover:text-indigo-800"
                onClick={() => setChatRuleId(null)}
              >
                <X size={12} />
              </button>
            </div>
            <div className="h-72 flex flex-col">
              <RuleIntentChat ruleId={chatRuleId} decisionId={decision.id} compact />
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 mt-3">
          {isPending ? (
            <>
              <button
                data-log="decision.verdict.approve"
                className={`btn-success text-xs ${selectedVerdict === 'approve' ? 'ring-2 ring-green-500' : ''}`}
                onClick={() => handleVerdictClick('approve')}
                disabled={resolving}
              >
                {resolving && decision.agent_verdict === 'approve' ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
                Approve
              </button>
              <button
                data-log="decision.verdict.warn"
                className={`text-xs px-2.5 py-1.5 rounded-md font-medium inline-flex items-center gap-1.5 border border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 disabled:opacity-50 ${selectedVerdict === 'warn' ? 'ring-2 ring-amber-500' : ''}`}
                onClick={() => handleVerdictClick('warn')}
                disabled={resolving}
              >
                {resolving && decision.agent_verdict === 'warn' ? <Loader2 size={13} className="animate-spin" /> : <AlertTriangle size={13} />}
                Warn
              </button>
              <button
                data-log="decision.verdict.remove"
                className={`btn-danger text-xs ${selectedVerdict === 'remove' ? 'ring-2 ring-red-500' : ''}`}
                onClick={() => handleVerdictClick('remove')}
                disabled={resolving}
              >
                {resolving && decision.agent_verdict === 'remove' ? <Loader2 size={13} className="animate-spin" /> : <XCircle size={13} />}
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
                  {decision.agent_verdict === 'review'
                    ? 'Agent flagged this as a community-norms issue with no specific rule — which rule(s) does this post violate? (leave blank if no rule applies)'
                    : 'Agent did not trigger any rules — which rule(s) does this post violate? (leave blank if no rule applies)'}
                </p>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {Object.entries(rulesMap).map(([id, rule]) => (
                    <label
                      key={id}
                      className="flex items-center gap-2 text-xs cursor-pointer"
                      data-log-context={JSON.stringify({ rule_id: id })}
                    >
                      <input
                        type="checkbox"
                        data-log="decision.rule-picker.toggle"
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
                data-log="decision.override.notes"
                className="w-full text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none"
                placeholder="Quick note on why you're overriding the agent's decision (optional)"
                value={notes}
                onChange={e => setNotes(e.target.value)}
              />
            )}
            <div className="flex gap-2">
              <button
                data-log={isOverride ? 'decision.override.confirm' : 'decision.resolve.confirm'}
                data-log-context={JSON.stringify({ verdict: selectedVerdict, is_override: !!isOverride, rule_ids: needsRulePicker ? selectedRuleIds : undefined })}
                className="btn-primary text-xs py-1"
                onClick={() => {
                  logEvent(isOverride ? 'decision.override.confirm' : 'decision.resolve.confirm', {
                    decision_id: decision.id,
                    verdict: selectedVerdict,
                    agent_verdict: decision.agent_verdict,
                    is_override: !!isOverride,
                    rule_ids: needsRulePicker ? selectedRuleIds : undefined,
                    notes: isOverride ? notes : undefined,
                  })
                  handleResolve()
                }}
                disabled={resolving}
              >
                {resolving ? <Loader2 size={12} className="animate-spin" /> : null}
                Confirm: {selectedVerdict}
              </button>
              <button
                data-log="decision.resolve.cancel"
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
