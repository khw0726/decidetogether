import { useQuery } from '@tanstack/react-query'
import { Inbox, Loader2 } from 'lucide-react'
import {
  Decision,
  DecisionPreviewResult,
  listDecisions,
} from '../api/client'
import PostCard from './PostCard'

interface DecisionsPanelProps {
  communityId: string
  ruleId: string | null
  checklistItemId: string | null
  previewResults: DecisionPreviewResult[] | null
  previewLoading: boolean
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

function DeltaChip({ kind }: { kind: 'fixed' | 'regressed' | 'unchanged' }) {
  const styles: Record<typeof kind, string> = {
    fixed: 'text-green-700 bg-green-50 border-green-200',
    regressed: 'text-red-700 bg-red-50 border-red-200',
    unchanged: 'text-gray-500 bg-gray-50 border-gray-200',
  }
  const icon: Record<typeof kind, string> = {
    fixed: '✓',
    regressed: '✗',
    unchanged: '—',
  }
  const label: Record<typeof kind, string> = {
    fixed: 'fixed',
    regressed: 'regressed',
    unchanged: 'no change',
  }
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider border rounded px-1.5 py-0.5 ${styles[kind]}`}>
      <span>{icon[kind]}</span>
      <span>{label[kind]}</span>
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
}: DecisionsPanelProps) {
  const { data: decisions = [], isLoading } = useQuery({
    queryKey: ['decisions-for-rule', communityId, ruleId, checklistItemId],
    queryFn: () =>
      listDecisions(communityId, {
        status: 'resolved',
        rule_id: ruleId ?? undefined,
        checklist_item_id: checklistItemId ?? undefined,
        limit: 50,
      }),
    enabled: !!communityId && !!ruleId,
  })

  if (!ruleId) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-gray-400 italic">
        Select a rule to see its decisions.
      </div>
    )
  }

  if (previewResults) {
    return (
      <div className="flex-1 overflow-auto p-3">
        <PreviewList items={previewResults} />
      </div>
    )
  }

  if (previewLoading) {
    return (
      <div className="flex-1 flex items-center justify-center gap-2 text-xs text-gray-400">
        <Loader2 size={14} className="animate-spin" />
        Previewing effect on past decisions…
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
        {checklistItemId
          ? 'No resolved decisions matched this checklist item.'
          : 'No resolved decisions for this rule yet.'}
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-3 space-y-3">
      {decisions.map(d => (
        <DecisionRow key={d.id} decision={d} />
      ))}
    </div>
  )
}

function DecisionRow({ decision }: { decision: Decision }) {
  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white">
      <div className="flex items-center gap-2 mb-2">
        <VerdictBadge label="Agent" verdict={decision.agent_verdict} />
        <VerdictBadge label="Mod" verdict={decision.moderator_verdict} />
        <span className="text-[10px] text-gray-400 ml-auto">
          {Math.round(decision.agent_confidence * 100)}% confidence
        </span>
      </div>
      <PostCard post={decision.post_content} compact />
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
      <div className="flex items-center gap-3 text-xs bg-indigo-50 border border-indigo-200 rounded-lg p-2 sticky top-0">
        <span className="font-semibold text-indigo-700 uppercase tracking-wide text-[10px]">Preview</span>
        {summary.fixed > 0 && <span className="text-green-700 font-semibold">{summary.fixed} fixed</span>}
        {summary.unchanged > 0 && <span className="text-gray-500">{summary.unchanged} unchanged</span>}
        {summary.regressed > 0 && <span className="text-red-700 font-semibold">{summary.regressed} regressed</span>}
      </div>
      {items.map(ev => (
        <div key={ev.decision_id} className="border border-gray-200 rounded p-2 bg-white text-xs">
          <div className="flex items-center gap-2 mb-1">
            <DeltaChip kind={classifyPreview(ev)} />
            <VerdictBadge label="Mod" verdict={ev.moderator_verdict} />
            <span className="ml-auto text-[10px] text-gray-400">
              {ev.old_verdict} ({Math.round(ev.old_confidence * 100)}%) → {ev.new_verdict} ({Math.round(ev.new_confidence * 100)}%)
            </span>
          </div>
          <p className="text-gray-700 font-medium truncate">{ev.post_title}</p>
        </div>
      ))}
    </div>
  )
}
