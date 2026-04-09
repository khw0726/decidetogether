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
  if (action === 'flag') return <span className="badge badge-yellow">FLAG</span>
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

function PreviewNode({ item, op, depth, forceDelete }: PreviewNodeProps) {
  const indent = depth * 16
  const effectiveOp = forceDelete ? 'delete' : op.op

  // For updates, show the compiler's new description; otherwise keep existing
  const description =
    effectiveOp === 'update' && op.description ? op.description : item.description
  const displayType =
    effectiveOp === 'update' && op.item_type ? op.item_type : item.item_type
  const displayAction =
    effectiveOp === 'update' && op.action ? op.action : item.action

  const opMeta = OP_LABEL[effectiveOp]
  const nodeClass = NODE_CLASSES[effectiveOp] ?? NODE_CLASSES.keep
  const descClass =
    effectiveOp === 'delete'
      ? 'line-through text-red-700'
      : effectiveOp === 'keep'
      ? 'text-gray-500'
      : 'text-gray-800'

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
              <span className={`text-sm font-medium ${descClass}`}>{description}</span>
            </div>
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
