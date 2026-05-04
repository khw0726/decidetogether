import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ExternalLink, Inbox, Loader2 } from 'lucide-react'
import {
  Decision,
  DecisionPreviewResult,
  listDecisions,
} from '../api/client'
import PostCard from './PostCard'
import RuleReasoningBlock from './RuleReasoningBlock'

interface DecisionsPanelProps {
  communityId: string
  ruleId: string | null
  checklistItemId: string | null
  previewResults: DecisionPreviewResult[] | null
  previewLoading: boolean
  testSetIds?: string[]
  useTestSet?: boolean
  onToggleUseTestSet?: (next: boolean) => void
  onToggleTestSetMember?: (decisionId: string) => void
  // Click-through filter from the rule-wide health panel. When set, only decisions
  // whose IDs appear in errorDecisionIds are shown.
  errorTypeFilter?: 'wrongly_flagged' | 'missed' | null
  errorDecisionIds?: string[] | null
  onClearErrorFilter?: () => void
}

const VERDICT_BADGE: Record<string, string> = {
  approve: 'bg-green-50 text-green-700 border-green-200',
  warn: 'bg-amber-50 text-amber-700 border-amber-200',
  remove: 'bg-red-50 text-red-700 border-red-200',
  review: 'bg-purple-50 text-purple-700 border-purple-200',
  pending: 'bg-gray-50 text-gray-600 border-gray-200',
}

function VerdictBadge({ label, verdict }: { label: string; verdict: string }) {
  const cls = VERDICT_BADGE[verdict] || VERDICT_BADGE.pending
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider border rounded px-1.5 py-0.5 ${cls}`}>
      <span className="opacity-60 font-normal normal-case tracking-normal">{label}</span>
      <span>{verdict}</span>
    </span>
  )
}

function classifyPreview(ev: DecisionPreviewResult): 'fixed' | 'regressed' | 'unchanged' {
  const modActed = ev.moderator_verdict === 'remove' || ev.moderator_verdict === 'warn'
  const oldActed = ev.old_verdict !== 'approve'
  const newActed = ev.new_verdict !== 'approve' && ev.new_verdict !== 'error'

  const oldAligned = oldActed === modActed
  const newAligned = newActed === modActed

  if (!oldAligned && newAligned) return 'fixed'
  if (oldAligned && !newAligned) return 'regressed'
  return 'unchanged'
}

export default function DecisionsPanel({
  communityId,
  ruleId,
  checklistItemId,
  previewResults,
  previewLoading,
  testSetIds = [],
  useTestSet = false,
  onToggleUseTestSet,
  onToggleTestSetMember,
  errorTypeFilter = null,
  errorDecisionIds = null,
  onClearErrorFilter,
}: DecisionsPanelProps) {
  const navigate = useNavigate()
  const testSetSet = new Set(testSetIds)
  // When an error filter is active, bump the limit so the requested IDs are
  // present in the fetched window (some may be older than the default 50 most
  // recent). The downstream filter narrows back to the requested set.
  const fetchLimit = errorTypeFilter ? 500 : 50
  const { data: allDecisions = [], isLoading } = useQuery({
    queryKey: ['decisions-for-rule', communityId, ruleId, checklistItemId, fetchLimit],
    queryFn: () =>
      listDecisions(communityId, {
        status: 'resolved',
        rule_id: ruleId ?? undefined,
        checklist_item_id: checklistItemId ?? undefined,
        limit: fetchLimit,
      }),
    enabled: !!communityId && !!ruleId,
  })
  const decisions = errorTypeFilter && errorDecisionIds
    ? allDecisions.filter(d => errorDecisionIds.includes(d.id))
    : allDecisions

  if (!ruleId) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-gray-400 italic">
        Select a rule to see its decisions.
      </div>
    )
  }

  if (previewLoading || previewResults) {
    return (
      <div className="relative flex-1 overflow-auto p-3">
        {previewResults ? (
          <PreviewList items={previewResults} />
        ) : (
          <div className="flex items-center justify-center gap-2 text-xs text-gray-400 py-6">
            <Loader2 size={14} className="animate-spin" />
            Previewing effect on past decisions…
          </div>
        )}
        {previewLoading && previewResults && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70 backdrop-blur-[1px]">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-indigo-200 shadow-sm text-xs text-indigo-700">
              <Loader2 size={12} className="animate-spin" />
              Re-evaluating decisions…
            </div>
          </div>
        )}
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-xs text-gray-400">
        <Loader2 size={14} className="animate-spin" />
        Loading decisions…
      </div>
    )
  }

  if (decisions.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-gray-400 italic gap-2">
        <Inbox size={14} />
        {errorTypeFilter
          ? `No ${errorTypeFilter === 'wrongly_flagged' ? 'wrongly flagged' : 'missed'} decisions to show.`
          : checklistItemId
            ? 'No resolved decisions matched this checklist item.'
            : 'No resolved decisions for this rule yet.'}
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-3 space-y-3">
      {errorTypeFilter && (
        <div className={`flex items-center gap-2 text-[11px] border rounded px-2 py-1.5 sticky top-0 z-10 ${
          errorTypeFilter === 'wrongly_flagged'
            ? 'bg-red-50 border-red-200 text-red-700'
            : 'bg-amber-50 border-amber-200 text-amber-800'
        }`}>
          <span className="font-semibold uppercase tracking-wide">
            Filter: {errorTypeFilter === 'wrongly_flagged' ? 'Wrongly flagged' : 'Missed'}
          </span>
          <span className="opacity-70">{decisions.length} decision{decisions.length === 1 ? '' : 's'}</span>
          {onClearErrorFilter && (
            <button
              type="button"
              data-log="decisions.error-filter.clear"
              className="ml-auto text-[11px] underline hover:opacity-80"
              onClick={onClearErrorFilter}
            >
              Clear
            </button>
          )}
        </div>
      )}
      {onToggleTestSetMember && (
        <div className="flex items-center gap-3 text-[11px] bg-gray-50 border border-gray-200 rounded px-2 py-1.5 sticky top-0 z-10">
          <span className="font-semibold text-gray-600 uppercase tracking-wide">Test set</span>
          <span className="text-gray-500">{testSetIds.length} pinned</span>
          <label className="ml-auto flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              data-log="decisions.test-set.toggle-use"
              checked={useTestSet}
              onChange={e => onToggleUseTestSet?.(e.target.checked)}
              disabled={testSetIds.length === 0}
            />
            <span>Use only pinned for live preview</span>
          </label>
        </div>
      )}
      {decisions.map(d => (
        <DecisionRow
          key={d.id}
          decision={d}
          ruleId={ruleId}
          isPinned={testSetSet.has(d.id)}
          onTogglePinned={onToggleTestSetMember}
          onOpenInQueue={() => {
            const params = new URLSearchParams({
              status: 'resolved',
              decision_id: d.id,
            })
            if (ruleId) params.set('rule_id', ruleId)
            navigate(`/decisions?${params.toString()}`)
          }}
        />
      ))}
    </div>
  )
}

function DecisionRow({
  decision,
  ruleId,
  isPinned,
  onTogglePinned,
  onOpenInQueue,
}: {
  decision: Decision
  ruleId: string
  isPinned?: boolean
  onTogglePinned?: (decisionId: string) => void
  onOpenInQueue?: () => void
}) {
  const ruleReasoning = (decision.agent_reasoning || {})[ruleId] as
    | { verdict?: string; confidence?: number; rule_title?: string; item_reasoning?: Record<string, unknown> }
    | undefined
  return (
    <div
      className={`border rounded-lg p-3 bg-white ${isPinned ? 'border-indigo-300 ring-1 ring-indigo-100' : 'border-gray-200'}`}
      data-log-context={JSON.stringify({ decision_id: decision.id, rule_id: ruleId })}
    >
      <div className="flex items-center gap-2 mb-2">
        {onTogglePinned && (
          <input
            type="checkbox"
            data-log="decisions.test-set.toggle-pin"
            checked={!!isPinned}
            onChange={() => onTogglePinned(decision.id)}
            title="Pin to live test set"
            className="cursor-pointer"
          />
        )}
        <VerdictBadge label="Agent" verdict={decision.agent_verdict} />
        <VerdictBadge label="Mod" verdict={decision.moderator_verdict} />
        <span className="text-[10px] text-gray-400 ml-auto">
          {Math.round(decision.agent_confidence * 100)}% confidence
        </span>
        {onOpenInQueue && (
          <button
            type="button"
            data-log="decision.open-in-queue"
            onClick={onOpenInQueue}
            className="text-[10px] text-indigo-500 hover:text-indigo-700 inline-flex items-center gap-0.5"
            title="Open this decision in the moderation queue (resolved view)"
          >
            <ExternalLink size={11} /> open in queue
          </button>
        )}
      </div>
      <PostCard post={decision.post_content} compact />
      {ruleReasoning && (
        <div className="mt-2">
          <RuleReasoningBlock
            ruleId={ruleId}
            ruleTitle={ruleReasoning.rule_title}
            verdict={ruleReasoning.verdict || 'approve'}
            confidence={ruleReasoning.confidence}
            itemReasoning={ruleReasoning.item_reasoning}
            defaultOpen={false}
          />
        </div>
      )}
      {decision.moderator_notes && (
        <p className="text-xs text-gray-500 mt-2 italic border-t border-gray-100 pt-2">
          {decision.moderator_reasoning_category && (
            <span className="not-italic font-medium text-gray-600">{decision.moderator_reasoning_category}: </span>
          )}
          {decision.moderator_notes}
        </p>
      )}
    </div>
  )
}

function PreviewList({ items }: { items: DecisionPreviewResult[] }) {
  if (items.length === 0) {
    return (
      <p className="text-xs text-gray-400 italic text-center py-4">
        No past decisions to re-evaluate for this rule.
      </p>
    )
  }

  const summary = items.reduce(
    (acc, ev) => {
      const kind = classifyPreview(ev)
      acc[kind] += 1
      return acc
    },
    { fixed: 0, regressed: 0, unchanged: 0 },
  )

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs bg-indigo-100 border border-indigo-200 rounded-lg p-2 sticky top-0 z-10 shadow-sm">
        <span className="font-semibold text-indigo-700 uppercase tracking-wide text-[10px]">Preview</span>
        {summary.fixed > 0 && <span className="text-green-700 font-semibold">{summary.fixed} fixed</span>}
        {summary.unchanged > 0 && <span className="text-gray-500">{summary.unchanged} unchanged</span>}
        {summary.regressed > 0 && <span className="text-red-700 font-semibold">{summary.regressed} regressed</span>}
      </div>
      {items.map(ev => (
        <div key={ev.decision_id} className="border border-gray-200 rounded p-2 bg-white text-xs">
          <div className="flex items-center gap-2 mb-1">
            <VerdictBadge label="Old" verdict={ev.old_verdict} />
            <VerdictBadge label="New" verdict={ev.new_verdict} />
            <VerdictBadge label="Mod" verdict={ev.moderator_verdict} />
            <span className="ml-auto text-[10px] text-gray-400">
              {Math.round(ev.old_confidence * 100)}% → {Math.round(ev.new_confidence * 100)}%
            </span>
          </div>
          <p className="text-gray-700 font-medium truncate">{ev.post_title}</p>
        </div>
      ))}
    </div>
  )
}
