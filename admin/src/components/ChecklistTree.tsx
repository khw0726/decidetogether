import { useState } from 'react'
import { ChevronRight, ChevronDown, Edit2, Check, X, Code, Trash2, Plus, Sparkles } from 'lucide-react'
import { ChecklistItem, createChecklistItem, updateChecklistItem, deleteChecklistItem } from '../api/client'
import { useMutation, useQueryClient } from '@tanstack/react-query'

interface ChecklistTreeProps {
  items: ChecklistItem[]
  ruleId: string
  onAnchorHover?: (anchor: string | null) => void
  selectedItemId?: string | null
  onItemSelect?: (itemId: string | null) => void
  highlightedItemId?: string | null
}

export default function ChecklistTree({ items, ruleId, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId }: ChecklistTreeProps) {
  const [adding, setAdding] = useState(false)
  return (
    <div className="space-y-1">
      {items.length === 0 && !adding && (
        <div className="text-sm text-gray-400 italic py-4 text-center">
          No checklist items yet. Compile the rule to generate them, or add one manually.
        </div>
      )}
      {items.map(item => (
        <ChecklistNode key={item.id} item={item} ruleId={ruleId} depth={0} onAnchorHover={onAnchorHover} selectedItemId={selectedItemId} onItemSelect={onItemSelect} highlightedItemId={highlightedItemId} />
      ))}
      {adding
        ? <AddItemForm ruleId={ruleId} parentId={null} onDone={() => setAdding(false)} />
        : (
          <button
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-indigo-600 px-1 py-1 mt-1"
            onClick={() => setAdding(true)}
          >
            <Plus size={13} /> Add root item
          </button>
        )
      }
    </div>
  )
}

// ── Logic Inspector ───────────────────────────────────────────────────────────

function LogicInspector({ item }: { item: ChecklistItem }) {
  const logic = item.logic as Record<string, unknown>

  if (item.item_type === 'deterministic') {
    const patterns = (logic.patterns as Array<{ regex: string; case_sensitive?: boolean }>) ?? []
    const matchMode = (logic.match_mode as string) ?? 'any'
    const negate = Boolean(logic.negate)
    return (
      <div className="mt-2 text-xs space-y-2">
        <div className="flex gap-4 text-gray-500">
          <span>Match mode: <span className="font-mono text-gray-700">{matchMode}</span></span>
          <span>Negate: <span className="font-mono text-gray-700">{negate ? 'true' : 'false'}</span></span>
        </div>
        <div>
          <p className="text-gray-500 mb-1">Patterns ({patterns.length}):</p>
          <div className="space-y-1">
            {patterns.map((p, i) => (
              <div key={i} className="flex items-center gap-2 bg-gray-50 rounded px-2 py-1 border border-gray-200">
                <code className="flex-1 text-indigo-700 break-all">{p.regex}</code>
                {p.case_sensitive && (
                  <span className="text-gray-400 whitespace-nowrap">case-sensitive</span>
                )}
              </div>
            ))}
            {patterns.length === 0 && <span className="text-gray-400 italic">No patterns defined</span>}
          </div>
        </div>
      </div>
    )
  }

  if (item.item_type === 'structural') {
    const checks = (logic.checks as Array<{ field: string; operator: string; value: unknown }>) ?? []
    const matchMode = (logic.match_mode as string) ?? 'all'
    return (
      <div className="mt-2 text-xs space-y-2">
        <div className="text-gray-500">
          Match mode: <span className="font-mono text-gray-700">{matchMode}</span>
        </div>
        <div>
          <p className="text-gray-500 mb-1">Checks ({checks.length}):</p>
          <div className="space-y-1">
            {checks.map((c, i) => (
              <div key={i} className="flex items-center gap-1.5 bg-gray-50 rounded px-2 py-1 border border-gray-200 font-mono">
                <span className="text-amber-700">{c.field}</span>
                <span className="text-gray-500">{c.operator}</span>
                <span className="text-green-700">{JSON.stringify(c.value)}</span>
              </div>
            ))}
            {checks.length === 0 && <span className="text-gray-400 italic">No checks defined</span>}
          </div>
        </div>
      </div>
    )
  }

  if (item.item_type === 'subjective') {
    const prompt = (logic.prompt_template as string) ?? ''
    const rubric = (logic.rubric as string) ?? ''
    const threshold = logic.threshold as number ?? 0.7
    const examplesCount = logic.examples_to_include as number ?? 5
    return (
      <div className="mt-2 text-xs space-y-2">
        <div className="flex gap-4 text-gray-500">
          <span>Confidence threshold: <span className="font-mono text-gray-700">{threshold}</span></span>
          <span>Examples to include: <span className="font-mono text-gray-700">{examplesCount}</span></span>
        </div>
        {prompt && (
          <div>
            <p className="text-gray-500 mb-1">Prompt template:</p>
            <p className="bg-gray-50 border border-gray-200 rounded px-2 py-1.5 leading-relaxed text-gray-700 whitespace-pre-wrap">{prompt}</p>
          </div>
        )}
        {rubric && (
          <div>
            <p className="text-gray-500 mb-1">Rubric:</p>
            <p className="bg-gray-50 border border-gray-200 rounded px-2 py-1.5 leading-relaxed text-gray-700 whitespace-pre-wrap">{rubric}</p>
          </div>
        )}
      </div>
    )
  }

  // Fallback: raw JSON
  return (
    <pre className="mt-2 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1.5 overflow-auto text-gray-700 whitespace-pre-wrap">
      {JSON.stringify(logic, null, 2)}
    </pre>
  )
}

// ── AddItemForm ───────────────────────────────────────────────────────────────

function AddItemForm({ ruleId, parentId, onDone }: { ruleId: string; parentId: string | null; onDone: () => void }) {
  const [description, setDescription] = useState('')
  const [action, setAction] = useState('flag')
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => createChecklistItem(ruleId, { description, action, parent_id: parentId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      onDone()
    },
  })

  return (
    <div className="border border-indigo-200 rounded-lg p-3 bg-indigo-50 space-y-2 text-xs">
      <input
        autoFocus
        className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
        placeholder="Yes/no question (YES = violation)..."
        value={description}
        onChange={e => setDescription(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && description.trim()) mutation.mutate(); if (e.key === 'Escape') onDone() }}
      />
      <div className="flex items-center gap-2">
        <span className="text-gray-400 italic">Type inferred automatically</span>
        <select
          className="border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none ml-auto"
          value={action}
          onChange={e => setAction(e.target.value)}
        >
          <option value="flag">Flag</option>
          <option value="remove">Remove</option>
          <option value="continue">Continue</option>
        </select>
        <button
          className="btn-primary text-xs py-1"
          onClick={() => mutation.mutate()}
          disabled={!description.trim() || mutation.isPending}
        >
          <Check size={12} />
          {mutation.isPending ? 'Inferring...' : 'Add'}
        </button>
        <button className="btn-secondary text-xs py-1" onClick={onDone}>
          <X size={12} />
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── ChecklistNode ─────────────────────────────────────────────────────────────

function ChecklistNode({ item, ruleId, depth, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId }: { item: ChecklistItem; ruleId: string; depth: number; onAnchorHover?: (anchor: string | null) => void; selectedItemId?: string | null; onItemSelect?: (itemId: string | null) => void; highlightedItemId?: string | null }) {
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const [showLogic, setShowLogic] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [addingChild, setAddingChild] = useState(false)
  const [editedDescription, setEditedDescription] = useState(item.description)
  const [editedFailAction, setEditedFailAction] = useState(item.action)

  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (data: Partial<ChecklistItem>) => updateChecklistItem(item.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      setEditing(false)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteChecklistItem(item.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
    },
  })

  const typeBadge = {
    deterministic: <span className="badge badge-blue">DETERMINISTIC</span>,
    structural: <span className="badge badge-yellow">STRUCTURAL</span>,
    subjective: <span className="badge badge-purple">SUBJECTIVE</span>,
  }[item.item_type] ?? <span className="badge badge-gray">{item.item_type}</span>

  const actionBadge = {
    remove: <span className="badge badge-red">REMOVE</span>,
    flag: <span className="badge badge-yellow">FLAG</span>,
    continue: <span className="badge badge-gray">continue</span>,
  }[item.action] ?? null

  const hasChildren = item.children && item.children.length > 0
  const indent = depth * 16

  const handleSave = () => {
    mutation.mutate({
      description: editedDescription,
      action: editedFailAction as 'remove' | 'flag' | 'continue',
    })
  }

  const isSelected = selectedItemId === item.id
  const isHighlighted = highlightedItemId === item.id

  return (
    <div style={{ marginLeft: indent }}>
      <div
        className={`border rounded-lg p-3 transition-colors cursor-pointer ${
          isSelected ? 'bg-white border-indigo-400 ring-1 ring-indigo-300' :
          isHighlighted ? 'bg-amber-50 border-amber-400 ring-1 ring-amber-300' :
          'bg-white border-gray-200 hover:border-gray-300'
        }`}
        onMouseEnter={() => onAnchorHover?.(item.rule_text_anchor || null)}
        onMouseLeave={() => onAnchorHover?.(null)}
        onClick={e => { if (!(e.target as HTMLElement).closest('button, input, textarea, select')) onItemSelect?.(isSelected ? null : item.id) }}
      >
        <div className="flex items-stretch gap-2">
          {/* Expand toggle */}
          <div className="flex grow-0 items-start">
            {hasChildren ? (
              <button
                className="flex-shrink-0 mt-0.5 text-gray-400 hover:text-gray-600"
                onClick={() => setExpanded(!expanded)}
              >
                {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </button>
            ) : (
              <div className="w-4 flex-shrink-0" />
            )}
          </div>


          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {typeBadge}
              {!editing && (
                <span className="text-sm font-medium text-gray-800">{item.description}</span>
              )}
            </div>

            {editing ? (
              <div className="mt-2 space-y-2">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Description</label>
                  <textarea
                    rows={4}
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    value={editedDescription}
                    onChange={e => setEditedDescription(e.target.value)}
                  />
                </div>
                {!hasChildren && (
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Action</label>
                    <select
                      className="border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      value={editedFailAction}
                      onChange={e => setEditedFailAction(e.target.value)}
                    >
                      <option value="remove">remove</option>
                      <option value="flag">flag</option>
                      <option value="continue">continue</option>
                    </select>
                  </div>
                )}
                <div className="flex gap-2">
                  <button className="btn-primary text-xs py-1" onClick={handleSave} disabled={mutation.isPending}>
                    <Check size={12} />
                    {mutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                  <button className="btn-secondary text-xs py-1" onClick={() => setEditing(false)}>
                    <X size={12} />
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* {item.rule_text_anchor && (
                  <p className="text-xs text-indigo-600 mt-1">
                    Anchor: &ldquo;{item.rule_text_anchor}&rdquo;
                  </p>
                )} */}
                {item.context_influenced && (
                  <div className="flex items-start gap-1 mt-1" title={item.context_note ?? 'Shaped by community context'}>
                    <Sparkles size={11} className="text-teal-500 mt-0.5 flex-shrink-0" />
                    <span className="text-xs text-teal-700">
                      {item.context_note ?? 'Influenced by community context'}
                    </span>
                  </div>
                )}
                {showLogic && <LogicInspector item={item} />}
              </>
            )}
          </div>


          {!editing && (
            <div className="flex flex-col items-end gap-1 w-24 flex-shrink-0 justify-between">
              <div className="flex gap-1">
                <button
                  className={`p-1 rounded transition-colors ${showLogic ? 'text-indigo-600 bg-indigo-50' : 'text-gray-400 hover:text-gray-700'}`}
                  onClick={() => setShowLogic(v => !v)}
                  title="Inspect logic"
                >
                  <Code size={14} />
                </button>
                <button
                  className="p-1 text-gray-400 hover:text-gray-700 rounded"
                  onClick={() => setEditing(true)}
                  title="Edit item"
                >
                  <Edit2 size={14} />
                </button>
                <button
                  className="p-1 text-gray-400 hover:text-indigo-600 rounded"
                  onClick={() => { setExpanded(true); setAddingChild(true) }}
                  title="Add child item"
                >
                  <Plus size={14} />
                </button>
                {confirmDelete ? (
                  <>
                    <button
                      className="p-1 text-red-600 hover:text-red-800 rounded"
                      onClick={() => deleteMutation.mutate()}
                      disabled={deleteMutation.isPending}
                      title="Confirm delete"
                    >
                      <Check size={14} />
                    </button>
                    <button
                      className="p-1 text-gray-400 hover:text-gray-700 rounded"
                      onClick={() => setConfirmDelete(false)}
                      title="Cancel"
                    >
                      <X size={14} />
                    </button>
                  </>
                ) : (
                  <button
                    className="p-1 text-gray-400 hover:text-red-600 rounded"
                    onClick={() => setConfirmDelete(true)}
                    title="Delete item"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
              <div className="flex flex-col items-end text-xs text-gray-400">
                <span>if yes →</span>
                {actionBadge}
              </div>
            </div>
          )}


        </div>

      </div>

      {/* Children */}
      {expanded && (item.children.length > 0 || addingChild) && (
        <div className="mt-1 space-y-1">
          {item.children.map(child => (
            <ChecklistNode key={child.id} item={child} ruleId={ruleId} depth={depth + 1} onAnchorHover={onAnchorHover} selectedItemId={selectedItemId} onItemSelect={onItemSelect} highlightedItemId={highlightedItemId} />
          ))}
          {addingChild && (
            <AddItemForm ruleId={ruleId} parentId={item.id} onDone={() => setAddingChild(false)} />
          )}
        </div>
      )}
    </div>
  )
}
