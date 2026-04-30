import { ChecklistItem, PreviewRecompileResult } from '../api/client'

interface ChecklistPreviewProps {
  operations: PreviewRecompileResult['operations']
  existingItems: ChecklistItem[]
}

type Op = PreviewRecompileResult['operations'][number]

function TypeBadge({ itemType }: { itemType: string | undefined }) {
  if (itemType === 'deterministic') return <span className="badge badge-blue">DETERMINISTIC</span>
  if (itemType === 'structural') return <span className="badge badge-yellow">STRUCTURAL</span>
  if (itemType === 'subjective') return <span className="badge badge-purple">SUBJECTIVE</span>
  return itemType ? <span className="badge badge-gray">{itemType}</span> : null
}

function ActionBadge({ action }: { action: string | undefined }) {
  if (action === 'remove') return <span className="badge badge-red">REMOVE</span>
  if (action === 'warn') return <span className="badge badge-yellow">WARN</span>
  if (action === 'continue') return <span className="badge badge-gray">continue</span>
  return null
}

const OP_LABEL: Record<string, { text: string; cls: string }> = {
  update: { text: '✎ updated', cls: 'text-amber-700 bg-amber-100' },
  delete: { text: '✕ removed', cls: 'text-red-700 bg-red-100' },
  add:    { text: '+ new',     cls: 'text-green-700 bg-green-100' },
}

const NODE_CLASSES: Record<string, string> = {
  keep:   'border-gray-200 bg-white',
  update: 'border-amber-300 bg-amber-50',
  delete: 'border-red-300 bg-red-50',
  add:    'border-green-300 bg-green-50',
}

interface PreviewNodeProps {
  item: ChecklistItem
  op: Op
  depth: number
  forceDelete?: boolean
}

function ConfidenceBar({ from, to }: { from: number; to: number }) {
  // Render a single bar with the prior threshold marker (gray) and the new
  // threshold marker (indigo). Useful when an `update` op moves the confidence
  // gate up or down — the diff direction is what matters.
  const clamp = (v: number) => Math.max(0, Math.min(1, v))
  const a = clamp(from), b = clamp(to)
  const lo = Math.min(a, b), hi = Math.max(a, b)
  const dir = b >= a ? 'up' : 'down'
  return (
    <div className="flex items-center gap-1.5 text-[10px]">
      <span className="text-gray-400">conf</span>
      <div className="relative w-24 h-1.5 bg-gray-200 rounded">
        <div
          className={`absolute top-0 h-1.5 ${dir === 'up' ? 'bg-emerald-300' : 'bg-rose-300'}`}
          style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }}
        />
        <div
          className="absolute -top-0.5 w-0.5 h-2.5 bg-gray-500"
          style={{ left: `${a * 100}%` }}
          title={`old ${a.toFixed(2)}`}
        />
        <div
          className="absolute -top-0.5 w-0.5 h-2.5 bg-indigo-700"
          style={{ left: `${b * 100}%` }}
          title={`new ${b.toFixed(2)}`}
        />
      </div>
      <span className="text-gray-500 font-mono">
        {a.toFixed(2)} → <span className="text-indigo-700 font-semibold">{b.toFixed(2)}</span>
      </span>
    </div>
  )
}

function pickThreshold(logic: unknown): number | null {
  if (!logic || typeof logic !== 'object') return null
  const l = logic as Record<string, unknown>
  for (const k of ['threshold', 'confidence_threshold', 'min_confidence']) {
    const v = l[k]
    if (typeof v === 'number') return v
  }
  return null
}

function PreviewNode({ item, op, depth, forceDelete }: PreviewNodeProps) {
  const indent = depth * 16
  const effectiveOp = forceDelete ? 'delete' : op.op

  // For updates, show the compiler's new description; otherwise keep existing
  const newDescription =
    effectiveOp === 'update' && op.description ? op.description : null
  const displayDescription = newDescription ?? item.description
  const newType =
    effectiveOp === 'update' && op.item_type ? op.item_type : null
  const displayType = newType ?? item.item_type
  const newAction =
    effectiveOp === 'update' && op.action ? op.action : null
  const displayAction = newAction ?? item.action

  const oldThreshold = pickThreshold(item.logic)
  const newThreshold = effectiveOp === 'update' ? pickThreshold(op.logic) : null

  const opMeta = OP_LABEL[effectiveOp]
  const nodeClass = NODE_CLASSES[effectiveOp] ?? NODE_CLASSES.keep
  const descClass =
    effectiveOp === 'delete'
      ? 'line-through text-red-700'
      : effectiveOp === 'keep'
      ? 'text-gray-500'
      : 'text-gray-800'

  // Build a list of changed-field rows for `update` ops so the moderator can see
  // what specifically changed without diffing the JSON in their head.
  const updateRows: { label: string; node: React.ReactNode }[] = []
  if (effectiveOp === 'update') {
    if (newDescription && newDescription !== item.description) {
      updateRows.push({
        label: 'description',
        node: (
          <div className="text-[11px] leading-snug">
            <span className="line-through text-red-500/80">{item.description}</span>
            {' → '}
            <span className="text-emerald-700 font-medium">{newDescription}</span>
          </div>
        ),
      })
    }
    if (newType && newType !== item.item_type) {
      updateRows.push({
        label: 'type',
        node: (
          <div className="flex items-center gap-1 text-[11px]">
            <TypeBadge itemType={item.item_type} />
            <span className="text-gray-400">→</span>
            <TypeBadge itemType={newType} />
          </div>
        ),
      })
    }
    if (newAction && newAction !== item.action) {
      updateRows.push({
        label: 'verdict',
        node: (
          <div className="flex items-center gap-1 text-[11px]">
            <ActionBadge action={item.action} />
            <span className="text-gray-400">→</span>
            <ActionBadge action={newAction} />
          </div>
        ),
      })
    }
    if (newThreshold != null && oldThreshold != null && Math.abs(newThreshold - oldThreshold) > 1e-6) {
      updateRows.push({
        label: 'threshold',
        node: <ConfidenceBar from={oldThreshold} to={newThreshold} />,
      })
    } else if (newThreshold != null && oldThreshold == null) {
      updateRows.push({
        label: 'threshold',
        node: <ConfidenceBar from={0} to={newThreshold} />,
      })
    }
    if (op.children) {
      updateRows.push({
        label: 'children',
        node: (
          <span className="text-[11px] text-amber-700">
            {(op.children as unknown[]).length} child item(s) replaced
          </span>
        ),
      })
    }
  }

  return (
    <div style={{ marginLeft: indent }}>
      <div className={`border rounded-lg p-3 ${nodeClass} ${effectiveOp === 'keep' ? 'opacity-60' : ''}`}>
        <div className="flex items-start gap-2">
          {/* spacer matching ChecklistTree's expand toggle */}
          <div className="w-4 flex-shrink-0" />

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <TypeBadge itemType={displayType} />
              {opMeta && (
                <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${opMeta.cls}`}>
                  {opMeta.text}
                </span>
              )}
              <span className={`text-sm font-medium ${descClass}`}>{displayDescription}</span>
            </div>
            {updateRows.length > 0 && (
              <div className="mt-2 space-y-1 border-t border-amber-200/70 pt-1.5">
                {updateRows.map(({ label, node }) => (
                  <div key={label} className="flex items-start gap-2">
                    <span className="text-[10px] uppercase tracking-wider text-amber-700/80 w-20 flex-shrink-0 mt-0.5">
                      {label}
                    </span>
                    <div className="flex-1 min-w-0">{node}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex-shrink-0 w-16 flex flex-col items-end text-xs text-gray-400">
            <span>if yes →</span>
            <ActionBadge action={displayAction} />
          </div>
        </div>
      </div>

      {item.children.length > 0 && (
        <div className="mt-1 space-y-1">
          {item.children.map(child => (
            <PreviewNode
              key={child.id}
              item={child}
              op={{ op: 'keep' }}
              depth={depth + 1}
              forceDelete={effectiveOp === 'delete'}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function ChecklistPreview({ operations, existingItems }: ChecklistPreviewProps) {
  const opByExistingId = new Map(
    operations.filter(op => op.existing_id).map(op => [op.existing_id!, op])
  )
  const addOps = operations.filter(op => op.op === 'add')

  if (existingItems.length === 0 && addOps.length === 0) {
    return <div className="text-xs text-gray-400 italic p-1">No checklist items.</div>
  }

  return (
    <div className="space-y-1">
      {existingItems.map(item => {
        const op = opByExistingId.get(item.id) ?? { op: 'keep' as const }
        return <PreviewNode key={item.id} item={item} op={op} depth={0} />
      })}

      {addOps.map((op, i) => (
        <div key={`add-${i}`} className="border border-green-300 bg-green-50 rounded-lg p-3">
          <div className="flex items-start gap-2">
            <div className="w-4 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <TypeBadge itemType={op.item_type} />
                <span className="text-xs px-1.5 py-0.5 rounded font-medium text-green-700 bg-green-100">
                  + new
                </span>
                <span className="text-sm font-medium text-gray-800">{op.description}</span>
              </div>
            </div>
            <div className="flex-shrink-0 w-16 flex flex-col items-end text-xs text-gray-400">
              <span>if yes →</span>
              <ActionBadge action={op.action} />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
