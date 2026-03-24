import { useState } from 'react'
import { ChevronRight, ChevronDown, Edit2, Check, X, Code, Trash2 } from 'lucide-react'
import { ChecklistItem, updateChecklistItem, deleteChecklistItem } from '../api/client'
import { useMutation, useQueryClient } from '@tanstack/react-query'

interface ChecklistTreeProps {
  items: ChecklistItem[]
  ruleId: string
  onAnchorHover?: (anchor: string | null) => void
}

export default function ChecklistTree({ items, ruleId, onAnchorHover }: ChecklistTreeProps) {
  if (items.length === 0) {
    return (
      <div className="text-sm text-gray-400 italic py-4 text-center">
        No checklist items yet. Compile the rule to generate them.
      </div>
    )
  }
  return (
    <div className="space-y-1">
      {items.map(item => (
        <ChecklistNode key={item.id} item={item} ruleId={ruleId} depth={0} onAnchorHover={onAnchorHover} />
      ))}
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

// ── ChecklistNode ─────────────────────────────────────────────────────────────

function ChecklistNode({ item, ruleId, depth, onAnchorHover }: { item: ChecklistItem; ruleId: string; depth: number; onAnchorHover?: (anchor: string | null) => void }) {
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const [showLogic, setShowLogic] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editedDescription, setEditedDescription] = useState(item.description)
  const [editedIntent, setEditedIntent] = useState(item.intent)
  const [editedFailAction, setEditedFailAction] = useState(item.fail_action)

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
    deterministic: <span className="badge badge-blue">DET</span>,
    structural: <span className="badge badge-yellow">STR</span>,
    subjective: <span className="badge badge-purple">SUB</span>,
  }[item.item_type] ?? <span className="badge badge-gray">{item.item_type}</span>

  const actionBadge = {
    remove: <span className="badge badge-red">REMOVE</span>,
    flag: <span className="badge badge-yellow">FLAG</span>,
    continue: <span className="badge badge-gray">continue</span>,
  }[item.fail_action] ?? null

  const hasChildren = item.children && item.children.length > 0
  const indent = depth * 16

  const handleSave = () => {
    mutation.mutate({
      description: editedDescription,
      intent: editedIntent,
      fail_action: editedFailAction as 'remove' | 'flag' | 'continue',
    })
  }

  return (
    <div style={{ marginLeft: indent }}>
      <div
        className="border border-gray-200 rounded-lg p-3 bg-white hover:border-gray-300 transition-colors"
        onMouseEnter={() => onAnchorHover?.(item.rule_text_anchor || null)}
        onMouseLeave={() => onAnchorHover?.(null)}
      >
        <div className="flex items-start gap-2">
          {/* Expand toggle */}
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

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {typeBadge}
              {actionBadge}
              {!editing && (
                <span className="text-sm font-medium text-gray-800">{item.description}</span>
              )}
            </div>

            {editing ? (
              <div className="mt-2 space-y-2">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Description</label>
                  <input
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    value={editedDescription}
                    onChange={e => setEditedDescription(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Intent</label>
                  <textarea
                    className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    rows={2}
                    value={editedIntent}
                    onChange={e => setEditedIntent(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Fail Action</label>
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
                <p className="text-xs text-gray-500 mt-1 leading-relaxed">{item.intent}</p>
                {item.rule_text_anchor && (
                  <p className="text-xs text-indigo-600 mt-1">
                    Anchor: &ldquo;{item.rule_text_anchor}&rdquo;
                  </p>
                )}
                <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-400">
                  <span>Combine: {item.combine_mode}</span>
                </div>
                {showLogic && <LogicInspector item={item} />}
              </>
            )}
          </div>

          {!editing && (
            <div className="flex gap-1 flex-shrink-0">
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
          )}
        </div>
      </div>

      {/* Children */}
      {hasChildren && expanded && (
        <div className="mt-1 space-y-1">
          {item.children.map(child => (
            <ChecklistNode key={child.id} item={child} ruleId={ruleId} depth={depth + 1} onAnchorHover={onAnchorHover} />
          ))}
        </div>
      )}
    </div>
  )
}
