import { Gauge, FileText, Type, Zap, PlusCircle, Search } from 'lucide-react'
import type { ChecklistItem, PreviewChecklistItem } from '../api/client'

const CHANGE_TYPE_META: Record<string, { icon: typeof Gauge; label: string; color: string }> = {
  threshold: { icon: Gauge, label: 'Threshold', color: 'text-teal-600' },
  rubric: { icon: FileText, label: 'Rubric', color: 'text-blue-600' },
  description: { icon: Type, label: 'Description', color: 'text-violet-600' },
  action: { icon: Zap, label: 'Action', color: 'text-orange-600' },
  new_item: { icon: PlusCircle, label: 'New item', color: 'text-emerald-600' },
  pattern: { icon: Search, label: 'Pattern', color: 'text-indigo-600' },
  check: { icon: Search, label: 'Check', color: 'text-indigo-600' },
}

type DiffStatus = 'unchanged' | 'modified' | 'added' | 'removed'

type AnyItem = {
  id: string
  description: string
  item_type: string
  action: string
  logic: Record<string, unknown>
  context_change_types: string[] | null
  base_description: string | null
  parent_id: string | null
}

interface Row {
  status: DiffStatus
  current: AnyItem | null
  preview: AnyItem | null
}

function flatten<T extends { children?: T[] | null }>(items: T[]): T[] {
  const out: T[] = []
  const visit = (list: T[]) => {
    for (const item of list) {
      out.push(item)
      if (item.children && item.children.length) visit(item.children as T[])
    }
  }
  visit(items)
  return out
}

function matchKey(item: { base_description: string | null; description: string }): string {
  return (item.base_description || item.description || '').toLowerCase().trim()
}

function toAny<T extends AnyItem>(x: T): AnyItem {
  return {
    id: x.id,
    description: x.description,
    item_type: x.item_type,
    action: x.action,
    logic: x.logic,
    context_change_types: x.context_change_types,
    base_description: x.base_description,
    parent_id: x.parent_id,
  }
}

function buildRows(
  current: ChecklistItem[],
  preview: PreviewChecklistItem[],
): Row[] {
  const curFlat = flatten(current)
  const prevFlat = flatten(preview)
  const prevByKey = new Map<string, PreviewChecklistItem>()
  for (const p of prevFlat) prevByKey.set(matchKey(p), p)
  const usedPreview = new Set<string>()

  const rows: Row[] = []
  for (const c of curFlat) {
    const key = matchKey(c)
    const p = prevByKey.get(key)
    if (p) {
      usedPreview.add(p.id)
      rows.push({
        status: diffStatus(c, p),
        current: toAny(c),
        preview: toAny(p),
      })
    } else {
      rows.push({ status: 'removed', current: toAny(c), preview: null })
    }
  }
  for (const p of prevFlat) {
    if (usedPreview.has(p.id)) continue
    rows.push({ status: 'added', current: null, preview: toAny(p) })
  }
  return rows
}

function diffStatus(c: ChecklistItem, p: PreviewChecklistItem): DiffStatus {
  if (p.context_change_types && p.context_change_types.length > 0) return 'modified'
  if (c.description !== p.description) return 'modified'
  if (c.action !== p.action) return 'modified'
  if (JSON.stringify(c.logic) !== JSON.stringify(p.logic)) return 'modified'
  return 'unchanged'
}

function TypeBadge({ type }: { type: string }) {
  const cls = {
    deterministic: 'badge badge-blue',
    structural: 'badge badge-yellow',
    subjective: 'badge badge-purple',
  }[type] ?? 'badge badge-gray'
  return <span className={cls}>{type.toUpperCase()}</span>
}

function ActionBadge({ action }: { action: string }) {
  const cls = {
    remove: 'badge badge-red',
    warn: 'badge badge-yellow',
    continue: 'badge badge-gray',
  }[action] ?? 'badge badge-gray'
  return <span className={cls}>{action.toUpperCase()}</span>
}

function ChangeChips({ types }: { types: string[] | null }) {
  if (!types || types.length === 0) return null
  return (
    <span className="inline-flex items-center gap-0.5">
      {types.map(t => {
        const meta = CHANGE_TYPE_META[t]
        if (!meta) return null
        const Icon = meta.icon
        return (
          <span key={t} className={`${meta.color} inline-flex items-center`} title={meta.label}>
            <Icon size={10} />
          </span>
        )
      })}
    </span>
  )
}

const STATUS_META: Record<DiffStatus, { wrapper: string; pill: string; pillText: string }> = {
  unchanged: {
    wrapper: 'border-gray-200 bg-white opacity-60',
    pill: 'bg-gray-100 text-gray-500',
    pillText: 'unchanged',
  },
  modified: {
    wrapper: 'border-amber-300 bg-amber-50',
    pill: 'bg-amber-100 text-amber-800',
    pillText: '✎ modified',
  },
  added: {
    wrapper: 'border-emerald-300 bg-emerald-50',
    pill: 'bg-emerald-100 text-emerald-800',
    pillText: '+ added',
  },
  removed: {
    wrapper: 'border-red-300 bg-red-50',
    pill: 'bg-red-100 text-red-800',
    pillText: '✕ removed',
  },
}

function InlineRow({ row }: { row: Row }) {
  const meta = STATUS_META[row.status]
  const item = row.preview ?? row.current!
  const before = row.current
  const after = row.preview
  const logic = (after?.logic ?? item.logic) as Record<string, unknown>
  const threshold = typeof logic?.threshold === 'number' ? (logic.threshold as number) : null
  const rubric = typeof logic?.rubric === 'string' ? (logic.rubric as string) : null

  // Inline before→after for description and action changes
  const descChanged = !!(before && after && before.description !== after.description)
  const actionChanged = !!(before && after && before.action !== after.action)
  const logicChanged = !!(before && after && JSON.stringify(before.logic) !== JSON.stringify(after.logic))
  const beforeThreshold =
    before && typeof (before.logic as Record<string, unknown>)?.threshold === 'number'
      ? ((before.logic as Record<string, unknown>).threshold as number)
      : null
  const thresholdChanged = beforeThreshold !== null && threshold !== null && beforeThreshold !== threshold

  const descClass =
    row.status === 'removed' ? 'line-through text-red-700'
    : row.status === 'unchanged' ? 'text-gray-500'
    : 'text-gray-800'

  return (
    <div className={`border rounded-lg p-3 ${meta.wrapper}`}>
      <div className="flex items-start gap-2">
        <div className="w-4 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <TypeBadge type={item.item_type} />
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${meta.pill}`}>
              {meta.pillText}
            </span>
            {row.status === 'modified' && <ChangeChips types={after?.context_change_types ?? null} />}
            <span className={`text-sm font-medium ${descClass}`}>
              {after?.description ?? item.description}
            </span>
          </div>
          {descChanged && before && (
            <p className="mt-1 text-[11px] text-gray-400 line-through leading-snug">
              was: {before.description}
            </p>
          )}
          {(thresholdChanged || logicChanged) && before && (
            <p className="mt-1 text-[11px] text-gray-500 leading-snug">
              {thresholdChanged && (
                <span className="font-mono mr-2">
                  thr {beforeThreshold!.toFixed(2)} → {threshold!.toFixed(2)}
                </span>
              )}
            </p>
          )}
          {rubric && (
            <p className="mt-1 text-[11px] text-gray-500 leading-snug line-clamp-2">{rubric}</p>
          )}
        </div>
        <div className="flex-shrink-0 w-20 flex flex-col items-end text-xs text-gray-400">
          <span>if yes →</span>
          <div className="flex items-center gap-1">
            {actionChanged && before && (
              <span className="text-[10px] line-through text-gray-400">{before.action}</span>
            )}
            <ActionBadge action={after?.action ?? item.action} />
          </div>
          {threshold !== null && !thresholdChanged && (
            <span className="text-[10px] font-mono mt-0.5">thr {threshold.toFixed(2)}</span>
          )}
        </div>
      </div>
    </div>
  )
}

interface Props {
  current: ChecklistItem[]
  preview: PreviewChecklistItem[]
  summary?: string[] | null
}

export default function ChecklistDiff({ current, preview, summary }: Props) {
  const rows = buildRows(current, preview)
  const counts = rows.reduce(
    (acc, r) => { acc[r.status] += 1; return acc },
    { unchanged: 0, modified: 0, added: 0, removed: 0 } as Record<DiffStatus, number>,
  )

  return (
    <div className="space-y-2">
      {summary && summary.length > 0 && (
        <div className="bg-teal-50 border border-teal-200 rounded-lg px-3 py-2 text-xs text-teal-800">
          <p className="font-semibold mb-1">What the preview changes</p>
          <ul className="space-y-0.5">
            {summary.map((bullet, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-teal-400 flex-shrink-0">•</span>
                <span>{bullet}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex items-center gap-2 text-[11px] text-gray-500 px-1">
        <span><span className="font-semibold text-amber-700">{counts.modified}</span> modified</span>
        <span>·</span>
        <span><span className="font-semibold text-emerald-700">{counts.added}</span> added</span>
        <span>·</span>
        <span><span className="font-semibold text-red-700">{counts.removed}</span> removed</span>
        <span>·</span>
        <span><span className="font-semibold text-gray-400">{counts.unchanged}</span> unchanged</span>
      </div>
      <div className="space-y-1.5">
        {rows.map((row, i) => (
          <InlineRow key={i} row={row} />
        ))}
      </div>
    </div>
  )
}
