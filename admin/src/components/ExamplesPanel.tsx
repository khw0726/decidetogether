import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ThumbsUp, ThumbsDown, Minus, Lightbulb } from 'lucide-react'
import { listExamples, addExample, deleteExample, updateExample, Example } from '../api/client'

interface ExamplesPanelProps {
  ruleId: string
  filterItemId?: string | null
  onItemHighlight?: (itemId: string | null) => void
  onSuggest?: () => void
  isSuggesting?: boolean
}

const LABELS = ['compliant', 'violating', 'borderline'] as const
type Label = typeof LABELS[number]

const labelConfig: Record<Label, { badge: string; icon: React.ReactNode; color: string }> = {
  compliant: {
    badge: 'badge-green',
    icon: <ThumbsUp size={12} />,
    color: 'text-green-700',
  },
  violating: {
    badge: 'badge-red',
    icon: <ThumbsDown size={12} />,
    color: 'text-red-700',
  },
  borderline: {
    badge: 'badge-yellow',
    icon: <Minus size={12} />,
    color: 'text-yellow-700',
  },
}

export default function ExamplesPanel({ ruleId, filterItemId, onItemHighlight, onSuggest, isSuggesting }: ExamplesPanelProps) {
  const [activeTab, setActiveTab] = useState<Label>('compliant')
  const [showAdd, setShowAdd] = useState(false)

  const queryClient = useQueryClient()

  const { data: examples = [], isLoading } = useQuery({
    queryKey: ['examples', ruleId],
    queryFn: () => listExamples(ruleId),
    enabled: !!ruleId,
  })

  const deleteMutation = useMutation({
    mutationFn: deleteExample,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['examples', ruleId] }),
  })

  const resolveMutation = useMutation({
    mutationFn: ({ id, label }: { id: string; label: string }) =>
      updateExample(id, { label } as Partial<Example>),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['examples', ruleId] }),
  })

  const byLabel = examples.filter((e: Example) => e.label === activeTab)
  const filtered = filterItemId
    ? byLabel.filter((e: Example) => e.checklist_item_id === filterItemId)
    : byLabel

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex flex-col gap-1 mb-3">
        {/* Tabs row */}
        <div className="flex items-center gap-0.5">
          {LABELS.map(label => {
            const count = examples.filter((e: Example) => e.label === label && (!filterItemId || e.checklist_item_id === filterItemId)).length
            const cfg = labelConfig[label]
            return (
              <button
                key={label}
                onClick={() => setActiveTab(label)}
                className={`flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                  activeTab === label
                    ? 'bg-gray-200 text-gray-800'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {cfg.icon}
                {label}
                <span className="ml-1 bg-gray-300 text-gray-700 rounded-full px-1.5 py-0.5 text-xs">
                  {count}
                </span>
              </button>
            )
          })}
        </div>
        {/* Actions row */}
        <div className="flex items-center gap-1">
          {onSuggest && (
            <button
              className="btn-secondary text-xs"
              onClick={onSuggest}
              disabled={isSuggesting}
              title="Generate checklist/rule suggestions from these examples"
            >
              <Lightbulb size={12} />
              {isSuggesting ? 'Analyzing...' : 'Analyze'}
            </button>
          )}
          <div className="flex-1" />
          <button className="btn-secondary text-xs" onClick={() => setShowAdd(true)}>
            <Plus size={12} />
            Add
          </button>
        </div>
      </div>

      {/* Active checklist item filter */}
      {filterItemId && (
        <div className="mb-2 px-1 flex items-center gap-1.5 text-xs text-indigo-700">
          <span className="bg-indigo-50 border border-indigo-200 rounded px-2 py-0.5 truncate max-w-[220px]">
            Filtered by checklist item
          </span>
          <span className="text-gray-400">(click item again to clear)</span>
        </div>
      )}

      {/* Examples list */}
      <div className="flex-1 overflow-auto space-y-2">
        {isLoading && <div className="text-sm text-gray-400 text-center py-4">Loading...</div>}
        {!isLoading && filtered.length === 0 && (
          <div className="text-sm text-gray-400 text-center py-4 italic">
            No {activeTab} examples yet.
          </div>
        )}
        {filtered.map((ex: Example) => {
          const content = ex.content as Record<string, unknown>
          const title = (content.title as string) || ''
          const body = (content.body as string) || ''
          return (
            <div
              key={ex.id}
              className="border border-gray-200 rounded-lg p-3 bg-white"
              onMouseEnter={() => ex.checklist_item_id && onItemHighlight?.(ex.checklist_item_id)}
              onMouseLeave={() => onItemHighlight?.(null)}
            >
              <div className="flex items-start gap-2">
                <span className={`badge ${labelConfig[ex.label].badge} flex-shrink-0`}>
                  {ex.label}
                </span>
                <div className="flex-1 min-w-0">
                  {title && <p className="text-sm font-medium truncate">{title}</p>}
                  {body && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{body}</p>}
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-gray-400">Source: {ex.source}</span>
                  </div>
                  {ex.checklist_item_description && (
                    <span className="text-xs text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded mt-1 inline-block">
                      {ex.checklist_item_description}
                    </span>
                  )}
                  {ex.moderator_reasoning && (
                    <p className="text-xs text-gray-600 mt-1 italic">{ex.moderator_reasoning}</p>
                  )}
                  {ex.label === 'borderline' && (
                    <div className="flex gap-1 mt-1.5">
                      <button
                        className="btn-success text-xs py-0.5"
                        onClick={() => resolveMutation.mutate({ id: ex.id, label: 'compliant' })}
                        disabled={resolveMutation.isPending}
                        title="Mark as compliant"
                      >
                        Compliant
                      </button>
                      <button
                        className="btn-danger text-xs py-0.5"
                        onClick={() => resolveMutation.mutate({ id: ex.id, label: 'violating' })}
                        disabled={resolveMutation.isPending}
                        title="Mark as violating"
                      >
                        Violating
                      </button>
                    </div>
                  )}
                </div>
                <button
                  className="flex-shrink-0 p-1 text-gray-400 hover:text-red-600 rounded"
                  onClick={() => deleteMutation.mutate(ex.id)}
                  title="Delete example"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Add example modal */}
      {showAdd && (
        <AddExampleModal
          ruleId={ruleId}
          defaultLabel={activeTab}
          onClose={() => setShowAdd(false)}
        />
      )}
    </div>
  )
}

function AddExampleModal({
  ruleId,
  defaultLabel,
  onClose,
}: {
  ruleId: string
  defaultLabel: Label
  onClose: () => void
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [label, setLabel] = useState<Label>(defaultLabel)
  const [reasoning, setReasoning] = useState('')
  const [loading, setLoading] = useState(false)
  const queryClient = useQueryClient()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() && !body.trim()) return
    setLoading(true)
    try {
      await addExample(ruleId, {
        content: { title: title.trim(), body: body.trim() },
        label,
        source: 'manual',
      })
      await queryClient.invalidateQueries({ queryKey: ['examples', ruleId] })
      onClose()
    } catch {
      // Handle error silently
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-md">
        <h3 className="font-semibold mb-4">Add Example</h3>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1">Title</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="Post title"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Body</label>
            <textarea
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              rows={3}
              value={body}
              onChange={e => setBody(e.target.value)}
              placeholder="Post body (optional)"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Label</label>
            <select
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={label}
              onChange={e => setLabel(e.target.value as Label)}
            >
              <option value="compliant">Compliant (follows rule)</option>
              <option value="violating">Violating (violates rule)</option>
              <option value="borderline">Borderline</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Reasoning (optional)</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={reasoning}
              onChange={e => setReasoning(e.target.value)}
              placeholder="Why is this a good/bad example?"
            />
          </div>
          <div className="flex gap-2 justify-end">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={loading || (!title.trim() && !body.trim())}>
              {loading ? 'Adding...' : 'Add Example'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
