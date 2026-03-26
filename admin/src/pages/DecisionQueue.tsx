import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, ChevronUp, CheckCircle, XCircle, Flag, Filter, Inbox, Loader2,
} from 'lucide-react'
import { listDecisions, resolveDecision, Decision, listRules } from '../api/client'
import PostCard from '../components/PostCard'

interface DecisionQueueProps {
  communityId: string
}

const REASONING_CATEGORIES = [
  { value: 'agree', label: 'Agree with agent' },
  { value: 'rule_doesnt_apply', label: 'Rule doesn\'t apply here' },
  { value: 'edge_case_allow', label: 'Edge case — allowing' },
  { value: 'rule_needs_update', label: 'Rule needs updating' },
  { value: 'agent_wrong_interpretation', label: 'Agent misinterpreted' },
]

export default function DecisionQueue({ communityId }: DecisionQueueProps) {
  const [filter, setFilter] = useState<'pending' | 'resolved' | 'all'>('pending')
  const [verdictFilter, setVerdictFilter] = useState<string>('')

  const queryClient = useQueryClient()

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

  const resolveMutation = useMutation({
    mutationFn: ({
      decisionId,
      verdict,
      reasoningCategory,
      notes,
      ruleIds,
    }: {
      decisionId: string
      verdict: string
      reasoningCategory?: string
      notes?: string
      ruleIds?: string[]
    }) => resolveDecision(decisionId, { verdict, reasoning_category: reasoningCategory, notes, rule_ids: ruleIds }),
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
          <Filter size={14} className="text-gray-400" />
          <select
            className="text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none"
            value={verdictFilter}
            onChange={e => setVerdictFilter(e.target.value)}
          >
            <option value="">All verdicts</option>
            <option value="approve">Approve</option>
            <option value="remove">Remove</option>
            <option value="review">Review</option>
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
            onResolve={(verdict, reasoningCategory, notes, ruleIds) =>
              resolveMutation.mutate({
                decisionId: decision.id,
                verdict,
                reasoningCategory,
                notes,
                ruleIds,
              })
            }
            resolving={resolveMutation.isPending && resolveMutation.variables?.decisionId === decision.id}
          />
        ))}
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

function DecisionCard({
  decision,
  rulesMap,
  onResolve,
  resolving,
}: {
  decision: Decision
  rulesMap: Record<string, { title: string }>
  onResolve: (verdict: string, reasoningCategory?: string, notes?: string, ruleIds?: string[]) => void
  resolving: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [selectedVerdict, setSelectedVerdict] = useState<string | null>(null)
  const [reasoningCategory, setReasoningCategory] = useState('')
  const [notes, setNotes] = useState('')
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([])

  // Rule picker is needed when agent approved (no triggered rules) but moderator disagrees
  const agentApproved = decision.agent_verdict === 'approve'
  const needsRulePicker = agentApproved && selectedVerdict && selectedVerdict !== 'approve'

  const isPending = decision.moderator_verdict === 'pending'

  const verdictColors: Record<string, string> = {
    approve: 'bg-green-100 text-green-800 border-green-200',
    remove: 'bg-red-100 text-red-800 border-red-200',
    review: 'bg-amber-100 text-amber-800 border-amber-200',
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
      reasoningCategory || undefined,
      notes || undefined,
      needsRulePicker ? selectedRuleIds : undefined,
    )
    setSelectedVerdict(null)
    setSelectedRuleIds([])
  }

  return (
    <div className={`card overflow-hidden ${decision.was_override ? 'border-amber-200' : ''}`}>
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start gap-3">
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
                className={`btn-danger text-xs ${selectedVerdict === 'remove' ? 'ring-2 ring-red-500' : ''}`}
                onClick={() => setSelectedVerdict('remove')}
              >
                <XCircle size={13} />
                Remove
              </button>
              <button
                className={`btn text-xs bg-amber-500 text-white hover:bg-amber-600 ${selectedVerdict === 'review' ? 'ring-2 ring-amber-500' : ''}`}
                onClick={() => setSelectedVerdict('review')}
              >
                <Flag size={13} />
                Review
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
                  Agent did not trigger any rules — which rule(s) does this post violate?
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
            <select
              className="w-full text-xs border border-gray-300 rounded px-2 py-1.5 bg-white focus:outline-none"
              value={reasoningCategory}
              onChange={e => setReasoningCategory(e.target.value)}
            >
              <option value="">Select reasoning category (optional)</option>
              {REASONING_CATEGORIES.map(c => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
            <input
              className="w-full text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none"
              placeholder="Optional notes..."
              value={notes}
              onChange={e => setNotes(e.target.value)}
            />
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
          <div className="mt-3 space-y-2">
            {Object.entries(decision.agent_reasoning || {}).map(([ruleId, reasoning]) => {
              const r = reasoning as Record<string, unknown>
              return (
                <div key={ruleId} className="p-3 bg-gray-50 rounded border border-gray-200 text-xs">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium">{r.rule_title as string || ruleId}</span>
                    <span className={`badge ${(r.verdict as string) === 'approve' ? 'badge-green' : (r.verdict as string) === 'remove' ? 'badge-red' : 'badge-yellow'}`}>
                      {r.verdict as string}
                    </span>
                    <span className="text-gray-400">{Math.round(((r.confidence as number) || 0) * 100)}%</span>
                  </div>
                  {!!r.item_reasoning && (
                    <div className="space-y-1 mt-2">
                      <ItemReasoningTree
                        itemReasoning={r.item_reasoning as Record<string, unknown>}
                        parentId={null}
                        depth={0}
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
