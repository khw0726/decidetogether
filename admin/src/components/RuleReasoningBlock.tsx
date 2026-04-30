import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'

export function ItemReasoningTree({
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

export default function RuleReasoningBlock({
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
