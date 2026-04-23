import { Gauge, FileText, Type, Zap, PlusCircle, Search, ArrowRight } from 'lucide-react'
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
  current: (AnyItem & { children: Row[] }) | null
  preview: (AnyItem & { children: Row[] }) | null
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
  for (const p of prevFlat) {
    prevByKey.set(matchKey(p), p)
  }
  const usedPreview = new Set<string>()

  const rows: Row[] = []
  for (const c of curFlat) {
    const key = matchKey(c)
    const p = prevByKey.get(key)
    if (p) {
      usedPreview.add(p.id)
      rows.push({
        status: diffStatus(c, p),
        current: { ...toAny(c), children: [] },
        preview: { ...toAny(p), children: [] },
      })
    } else {
      rows.push({
        status: 'removed',
        current: { ...toAny(c), children: [] },
        preview: null,
      })
    }
  }
  for (const p of prevFlat) {
    if (usedPreview.has(p.id)) continue
    rows.push({
      status: 'added',
      current: null,
      preview: { ...toAny(p), children: [] },
    })
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
    <div className="inline-flex items-center gap-0.5">
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
    </div>
  )
}

function ItemCard({
  item,
  status,
  side,
}: {
  item: AnyItem | null
  status: DiffStatus
  side: 'current' | 'preview'
}) {
  if (!item) {
    const tone =
      side === 'current'
        ? 'border-emerald-200 bg-emerald-50/40 text-emerald-700'
        : 'border-red-200 bg-red-50/40 text-red-700'
    const label = side === 'current' ? 'will be added' : 'will be removed'
    return (
      <div className={`border border-dashed rounded px-2 py-1.5 text-[11px] italic ${tone}`}>
        {label}
      </div>
    )
  }
  const logic = item.logic as Record<string, unknown>
  const threshold = typeof logic?.threshold === 'number' ? (logic.threshold as number) : null
  const rubric = typeof logic?.rubric === 'string' ? (logic.rubric as string) : null
  const tone =
    status === 'unchanged'
      ? 'border-gray-200 bg-white'
      : status === 'modified'
      ? 'border-blue-200 bg-blue-50/40'
      : status === 'added'
      ? 'border-emerald-200 bg-emerald-50/40'
      : 'border-red-200 bg-red-50/40'
  return (
    <div className={`border rounded px-2 py-1.5 text-xs ${tone}`}>
      <div className="flex items-start gap-1.5 flex-wrap">
        <TypeBadge type={item.item_type} />
        <span className="font-medium text-gray-800 leading-snug flex-1 min-w-0">
          {item.description}
        </span>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <ActionBadge action={item.action} />
        {threshold !== null && (
          <span className="text-[10px] font-mono text-gray-500">thr {threshold.toFixed(2)}</span>
        )}
        {side === 'preview' && <ChangeChips types={item.context_change_types} />}
      </div>
      {rubric && (
        <p className="mt-1 text-[11px] text-gray-500 leading-snug line-clamp-2">{rubric}</p>
      )}
    </div>
  )
}

function StatusLabel({ status }: { status: DiffStatus }) {
  const map: Record<DiffStatus, { text: string; cls: string }> = {
    unchanged: { text: 'unchanged', cls: 'text-gray-400' },
    modified: { text: 'modified', cls: 'text-blue-600' },
    added: { text: 'added', cls: 'text-emerald-600' },
    removed: { text: 'removed', cls: 'text-red-600' },
  }
  const { text, cls } = map[status]
  return <span className={`text-[10px] uppercase tracking-wider font-semibold ${cls}`}>{text}</span>
}

interface Props {
  current: ChecklistItem[]
  preview: PreviewChecklistItem[]
  summary?: string[] | null
}

export default function ChecklistDiff({ current, preview, summary }: Props) {
  const rows = buildRows(current, preview)
  const counts = rows.reduce(
    (acc, r) => {
      acc[r.status] += 1
      return acc
    },
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
      <div className="flex items-center gap-2 text-[11px] text-gray-500">
        <span>
          <span className="font-semibold text-blue-600">{counts.modified}</span> modified
        </span>
        <span>·</span>
        <span>
          <span className="font-semibold text-emerald-600">{counts.added}</span> added
        </span>
        <span>·</span>
        <span>
          <span className="font-semibold text-red-600">{counts.removed}</span> removed
        </span>
        <span>·</span>
        <span>
          <span className="font-semibold text-gray-400">{counts.unchanged}</span> unchanged
        </span>
      </div>
      <div className="grid grid-cols-[1fr_auto_1fr] gap-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-1">
        <span>Current</span>
        <span className="w-3" />
        <span>Preview</span>
      </div>
      <div className="space-y-1.5">
        {rows.map((row, i) => (
          <div key={i} className="space-y-0.5">
            <div className="flex items-center gap-1.5 px-1">
              <StatusLabel status={row.status} />
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] gap-1 items-stretch">
              <ItemCard item={row.current} status={row.status} side="current" />
              <div className="flex items-center justify-center text-gray-300">
                <ArrowRight size={12} />
              </div>
              <ItemCard item={row.preview} status={row.status} side="preview" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
