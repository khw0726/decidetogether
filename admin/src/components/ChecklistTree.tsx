import { useState } from 'react'
import { ChevronRight, ChevronDown, Edit2, Check, X, Code, Trash2, Plus, Sparkles, Pin, PinOff, Loader2, Gauge, FileText, Type, Zap, PlusCircle, Search, RefreshCw } from 'lucide-react'
import { ChecklistItem, createChecklistItem, updateChecklistItem, deleteChecklistItem, setContextOverride, Rule, StructuralFieldSpec, getStructuralFields } from '../api/client'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

// Default empty logic per item type — used when the user switches the
// type radio so the editor has a coherent shape to render.
function defaultLogicFor(itemType: 'deterministic' | 'structural' | 'subjective'): Record<string, unknown> {
  if (itemType === 'deterministic') {
    return { type: 'deterministic', patterns: [{ regex: '', case_sensitive: false }], match_mode: 'any', negate: false, field: 'all' }
  }
  if (itemType === 'structural') {
    return { type: 'structural', checks: [{ field: '', operator: '==', value: '' }], match_mode: 'all' }
  }
  return { type: 'subjective', prompt_template: '', rubric: '', threshold: 0.7 }
}

// Operator options driven by the structural field's value_type. Numeric
// fields get the full comparison set; strings/bools get equality + membership.
function operatorsFor(valueType: string | undefined): string[] {
  if (valueType === 'number') return ['<', '<=', '>', '>=', '==', '!=']
  if (valueType === 'bool') return ['==', '!=']
  return ['==', '!=', 'in']
}

// Coerce a raw input value into the type the field expects so the
// backend doesn't compare a string against a number at evaluation time.
function coerceStructuralValue(raw: string, valueType: string | undefined): unknown {
  if (valueType === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : raw
  }
  if (valueType === 'bool') {
    if (raw === 'true') return true
    if (raw === 'false') return false
    return raw
  }
  return raw
}

// ── LogicEditor ─────────────────────────────────────────────────────────────
// Editable counterpart to LogicInspector. The parent owns the logic state and
// passes onChange so the Save handler can ship the final shape unchanged.

function LogicEditor({
  itemType,
  logic,
  onChange,
  structuralFields,
}: {
  itemType: 'deterministic' | 'structural' | 'subjective'
  logic: Record<string, unknown>
  onChange: (next: Record<string, unknown>) => void
  structuralFields: StructuralFieldSpec[]
}) {
  if (itemType === 'deterministic') {
    const patterns = (logic.patterns as Array<{ regex: string; case_sensitive?: boolean }>) ?? []
    const matchMode = (logic.match_mode as string) ?? 'any'
    const negate = Boolean(logic.negate)
    const field = (logic.field as string) ?? 'all'
    const setPatterns = (next: Array<{ regex: string; case_sensitive?: boolean }>) =>
      onChange({ ...logic, patterns: next })
    return (
      <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-3 items-center">
          <label className="flex items-center gap-1">
            <span className="text-gray-500">Match</span>
            <select
              className="border border-gray-300 rounded px-1.5 py-0.5 bg-white"
              value={matchMode}
              onChange={e => onChange({ ...logic, match_mode: e.target.value })}
            >
              <option value="any">any</option>
              <option value="all">all</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <span className="text-gray-500">Field</span>
            <select
              className="border border-gray-300 rounded px-1.5 py-0.5 bg-white"
              value={field}
              onChange={e => onChange({ ...logic, field: e.target.value })}
            >
              <option value="all">all</option>
              <option value="title">title</option>
              <option value="body">body</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={negate}
              onChange={e => onChange({ ...logic, negate: e.target.checked })}
            />
            <span className="text-gray-500">Negate</span>
          </label>
        </div>
        <div className="space-y-1">
          {patterns.map((p, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <input
                className="flex-1 border border-gray-300 rounded px-2 py-1 font-mono text-indigo-700"
                placeholder="regex pattern"
                value={p.regex}
                onChange={e => {
                  const next = [...patterns]
                  next[i] = { ...next[i], regex: e.target.value }
                  setPatterns(next)
                }}
              />
              <label className="flex items-center gap-1 text-gray-500">
                <input
                  type="checkbox"
                  checked={Boolean(p.case_sensitive)}
                  onChange={e => {
                    const next = [...patterns]
                    next[i] = { ...next[i], case_sensitive: e.target.checked }
                    setPatterns(next)
                  }}
                />
                Aa
              </label>
              <button
                type="button"
                className="p-1 text-gray-400 hover:text-red-600"
                onClick={() => setPatterns(patterns.filter((_, j) => j !== i))}
                title="Remove pattern"
              >
                <X size={12} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
            onClick={() => setPatterns([...patterns, { regex: '', case_sensitive: false }])}
          >
            <Plus size={12} /> Add pattern
          </button>
        </div>
      </div>
    )
  }

  if (itemType === 'structural') {
    const checks = (logic.checks as Array<{ field: string; operator: string; value: unknown }>) ?? []
    const matchMode = (logic.match_mode as string) ?? 'all'
    const fieldByName = new Map(structuralFields.map(f => [f.field, f]))
    const setChecks = (next: typeof checks) => onChange({ ...logic, checks: next })
    return (
      <div className="space-y-2 text-xs">
        <label className="flex items-center gap-1">
          <span className="text-gray-500">Match</span>
          <select
            className="border border-gray-300 rounded px-1.5 py-0.5 bg-white"
            value={matchMode}
            onChange={e => onChange({ ...logic, match_mode: e.target.value })}
          >
            <option value="all">all</option>
            <option value="any">any</option>
          </select>
        </label>
        <div className="space-y-1">
          {checks.map((c, i) => {
            const spec = fieldByName.get(c.field)
            const ops = operatorsFor(spec?.value_type)
            return (
              <div key={i}>
                <div className="flex items-center gap-1.5">
                  <select
                    className="border border-gray-300 rounded px-1.5 py-1 bg-white font-mono text-amber-700"
                    value={c.field}
                    onChange={e => {
                      const next = [...checks]
                      const newSpec = fieldByName.get(e.target.value)
                      const newOps = operatorsFor(newSpec?.value_type)
                      // Keep operator if still valid for the new field, else reset.
                      const op = newOps.includes(c.operator) ? c.operator : newOps[0]
                      next[i] = { ...next[i], field: e.target.value, operator: op }
                      setChecks(next)
                    }}
                  >
                    <option value="">— field —</option>
                    {structuralFields.map(f => (
                      <option key={f.field} value={f.field}>{f.field}</option>
                    ))}
                  </select>
                  <select
                    className="border border-gray-300 rounded px-1.5 py-1 bg-white font-mono"
                    value={c.operator}
                    onChange={e => {
                      const next = [...checks]
                      next[i] = { ...next[i], operator: e.target.value }
                      setChecks(next)
                    }}
                  >
                    {ops.map(op => <option key={op} value={op}>{op}</option>)}
                  </select>
                  {spec?.value_type === 'bool' ? (
                    <select
                      className="border border-gray-300 rounded px-1.5 py-1 bg-white font-mono text-green-700"
                      value={String(c.value)}
                      onChange={e => {
                        const next = [...checks]
                        next[i] = { ...next[i], value: coerceStructuralValue(e.target.value, 'bool') }
                        setChecks(next)
                      }}
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input
                      className="flex-1 border border-gray-300 rounded px-2 py-1 font-mono text-green-700"
                      placeholder={spec?.value_type === 'number' ? '0' : 'value'}
                      value={c.value === null || c.value === undefined ? '' : String(c.value)}
                      onChange={e => {
                        const next = [...checks]
                        next[i] = { ...next[i], value: coerceStructuralValue(e.target.value, spec?.value_type) }
                        setChecks(next)
                      }}
                    />
                  )}
                  <button
                    type="button"
                    className="p-1 text-gray-400 hover:text-red-600"
                    onClick={() => setChecks(checks.filter((_, j) => j !== i))}
                    title="Remove check"
                  >
                    <X size={12} />
                  </button>
                </div>
                {spec && (
                  <p className="text-[10px] text-gray-400 ml-1 mt-0.5">{spec.description}</p>
                )}
              </div>
            )
          })}
          <button
            type="button"
            className="text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
            onClick={() => setChecks([...checks, { field: structuralFields[0]?.field ?? '', operator: '==', value: '' }])}
          >
            <Plus size={12} /> Add check
          </button>
        </div>
      </div>
    )
  }

  // Subjective
  const prompt = (logic.prompt_template as string) ?? ''
  const rubric = (logic.rubric as string) ?? ''
  const threshold = (logic.threshold as number) ?? 0.7
  return (
    <div className="space-y-2 text-xs">
      <label className="flex items-center gap-2">
        <span className="text-gray-500">Threshold</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          className="w-20 border border-gray-300 rounded px-1.5 py-0.5 font-mono"
          value={threshold}
          onChange={e => onChange({ ...logic, threshold: Number(e.target.value) })}
        />
        <span className="text-gray-400">(0–1; higher = stricter)</span>
      </label>
      <div>
        <label className="block text-gray-500 mb-1">Rubric</label>
        <textarea
          rows={4}
          className="w-full border border-gray-300 rounded px-2 py-1 leading-relaxed"
          placeholder="What signals indicate a YES verdict on this item?"
          value={rubric}
          onChange={e => onChange({ ...logic, rubric: e.target.value })}
        />
      </div>
      <div>
        <label className="block text-gray-500 mb-1">Prompt template (optional)</label>
        <textarea
          rows={2}
          className="w-full border border-gray-300 rounded px-2 py-1 leading-relaxed"
          placeholder="Override the default item prompt (rarely needed)"
          value={prompt}
          onChange={e => onChange({ ...logic, prompt_template: e.target.value })}
        />
      </div>
    </div>
  )
}


// ── Change Type Icons ────────────────────────────────────────────────────────

const CHANGE_TYPE_META: Record<string, { icon: typeof Gauge; label: string; color: string }> = {
  threshold: { icon: Gauge, label: 'Threshold adjusted', color: 'text-teal-600' },
  rubric: { icon: FileText, label: 'Rubric refined', color: 'text-blue-600' },
  description: { icon: Type, label: 'Description changed', color: 'text-violet-600' },
  action: { icon: Zap, label: 'Action changed', color: 'text-orange-600' },
  new_item: { icon: PlusCircle, label: 'Added by context', color: 'text-emerald-600' },
  pattern: { icon: Search, label: 'Pattern changed', color: 'text-indigo-600' },
  check: { icon: Search, label: 'Check changed', color: 'text-indigo-600' },
}

function ChangeTypeIcons({ types }: { types: string[] }) {
  if (!types || types.length === 0) return null
  return (
    <span className="inline-flex items-center gap-0.5 ml-1">
      {types.map(t => {
        const meta = CHANGE_TYPE_META[t]
        if (!meta) return null
        const Icon = meta.icon
        return (
          <span key={t} className={meta.color} title={meta.label}>
            <Icon size={10} />
          </span>
        )
      })}
    </span>
  )
}

// ── Threshold Gauge ──────────────────────────────────────────────────────────

function ThresholdGauge({ current, base }: { current: number; base: number | null }) {
  if (base === null || base === current) return null
  const lowered = current < base
  const delta = current - base
  const barColor = lowered ? 'bg-teal-400' : 'bg-amber-400'
  const markerColor = lowered ? 'border-teal-600' : 'border-amber-600'
  const textColor = lowered ? 'text-teal-700' : 'text-amber-700'
  const label = lowered ? 'more sensitive' : 'stricter'

  return (
    <div className="flex items-center gap-2 mt-1.5" title={`Base: ${base.toFixed(2)} → Current: ${current.toFixed(2)}`}>
      <div className="relative w-28 h-2 bg-gray-200 rounded-full overflow-visible">
        {/* Base marker */}
        <div
          className="absolute top-[-1px] w-0.5 h-[10px] bg-gray-400 rounded-full z-10"
          style={{ left: `${base * 100}%` }}
          title={`Base: ${base.toFixed(2)}`}
        />
        {/* Current fill */}
        <div
          className={`absolute top-0 left-0 h-full rounded-full ${barColor}`}
          style={{ width: `${current * 100}%` }}
        />
        {/* Current marker */}
        <div
          className={`absolute top-[-2px] w-1 h-[12px] ${markerColor} border rounded-full z-20`}
          style={{ left: `${current * 100}%`, transform: 'translateX(-50%)' }}
        />
      </div>
      <span className={`text-[10px] font-mono font-medium ${textColor} whitespace-nowrap`}>
        {delta > 0 ? '+' : ''}{delta.toFixed(2)} {label}
      </span>
    </div>
  )
}

// ── Rubric Diff ──────────────────────────────────────────────────────────────

function RubricDiff({ base, current }: { base: string; current: string }) {
  return (
    <div className="mt-1.5 space-y-1.5 text-xs">
      <div>
        <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-0.5">Base rubric</p>
        <p className="bg-gray-50 border border-gray-200 rounded px-2 py-1.5 text-gray-600 leading-relaxed whitespace-pre-wrap">{base}</p>
      </div>
      <div>
        <p className="text-[10px] font-semibold text-blue-500 uppercase tracking-wider mb-0.5">Adjusted rubric</p>
        <p className="bg-blue-50 border border-blue-200 rounded px-2 py-1.5 text-blue-800 leading-relaxed whitespace-pre-wrap">{current}</p>
      </div>
    </div>
  )
}

// ── Context Badge ────────────────────────────────────────────────────────────

function ContextBadge({ itemId, note, changeTypes, pinned, overrideNote, ruleId, threshold, baseThreshold, baseRubric, currentRubric }: {
  itemId: string
  note: string | null
  changeTypes: string[] | null
  pinned: boolean
  overrideNote: string | null
  ruleId: string
  threshold: number | null
  baseThreshold: number | null
  baseRubric: string | null
  currentRubric: string | null
}) {
  const [open, setOpen] = useState(false)
  const [showRubric, setShowRubric] = useState(false)
  const [editingNote, setEditingNote] = useState(false)
  const [draftNote, setDraftNote] = useState(overrideNote ?? '')
  const queryClient = useQueryClient()

  const pinMutation = useMutation({
    mutationFn: (args: { pin: boolean; note?: string }) =>
      setContextOverride(itemId, args.pin, args.note),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] }),
  })

  const handlePin = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (pinned) {
      pinMutation.mutate({ pin: false })
      setEditingNote(false)
    } else {
      setEditingNote(true)
    }
  }

  const submitPin = (e: React.MouseEvent | React.FormEvent) => {
    e.stopPropagation()
    e.preventDefault()
    pinMutation.mutate({ pin: true, note: draftNote.trim() || undefined })
    setEditingNote(false)
  }

  const isNewItem = changeTypes?.includes('new_item')
  const hasThresholdChange = changeTypes?.includes('threshold') && threshold !== null && baseThreshold !== null
  const hasRubricChange = changeTypes?.includes('rubric') && baseRubric !== null && currentRubric !== null && baseRubric !== currentRubric

  return (
    <div className="mt-1">
      <span className="inline-flex items-center gap-0.5">
        <button
          className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium transition-colors ${
            pinned
              ? 'bg-amber-50 border border-amber-300 text-amber-700 hover:bg-amber-100'
              : isNewItem
              ? 'bg-emerald-50 border border-emerald-300 text-emerald-700 hover:bg-emerald-100'
              : 'bg-teal-50 border border-teal-200 text-teal-700 hover:bg-teal-100'
          }`}
          onClick={(e) => { e.stopPropagation(); setOpen(v => !v) }}
          title={note ?? 'Shaped by community context'}
        >
          {pinned
            ? <Pin size={10} className="flex-shrink-0" />
            : isNewItem
            ? <PlusCircle size={10} className="flex-shrink-0" />
            : <Sparkles size={10} className="flex-shrink-0" />
          }
          {pinned ? 'pinned' : isNewItem ? 'context-added' : 'context'}
        </button>
        {!pinned && changeTypes && !isNewItem && <ChangeTypeIcons types={changeTypes} />}
      </span>

      {open && (
        <div className={`text-xs mt-1 pl-1 border-l-2 ${
          pinned ? 'border-amber-200' : isNewItem ? 'border-emerald-200' : 'border-teal-200'
        }`}>
          {note && <p className={pinned ? 'text-amber-700' : isNewItem ? 'text-emerald-700' : 'text-teal-700'}>{note}</p>}
          {hasThresholdChange && (
            <ThresholdGauge current={threshold!} base={baseThreshold!} />
          )}
          {hasRubricChange && (
            <div className="mt-1.5">
              <button
                className="inline-flex items-center gap-1 text-[10px] font-medium text-blue-600 hover:text-blue-800"
                onClick={(e) => { e.stopPropagation(); setShowRubric(v => !v) }}
              >
                <FileText size={10} />
                {showRubric ? 'Hide rubric diff' : 'View rubric diff'}
              </button>
              {showRubric && <RubricDiff base={baseRubric!} current={currentRubric!} />}
            </div>
          )}
          {pinned && overrideNote && (
            <p className="text-amber-600 mt-0.5 italic">Pin note: {overrideNote}</p>
          )}
          {editingNote ? (
            <form onSubmit={submitPin} className="mt-1.5 flex gap-1.5 items-start" onClick={e => e.stopPropagation()}>
              <input
                type="text"
                className="flex-1 text-xs border border-gray-200 rounded px-1.5 py-0.5 focus:outline-none focus:border-amber-400"
                value={draftNote}
                onChange={e => setDraftNote(e.target.value)}
                placeholder="Why pin this? (optional)"
                autoFocus
              />
              <button type="submit" className="text-amber-600 hover:text-amber-800" disabled={pinMutation.isPending}>
                {pinMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              </button>
              <button type="button" className="text-gray-400 hover:text-gray-600" onClick={(e) => { e.stopPropagation(); setEditingNote(false) }}>
                <X size={12} />
              </button>
            </form>
          ) : (
            <button
              className={`mt-1 inline-flex items-center gap-1 text-[10px] font-medium ${
                pinned ? 'text-amber-500 hover:text-amber-700' : 'text-teal-500 hover:text-teal-700'
              }`}
              onClick={handlePin}
              disabled={pinMutation.isPending}
              title={pinned ? 'Unpin — let recompilation change this item freely' : 'Pin — preserve this calibration across recompilations'}
            >
              {pinMutation.isPending
                ? <Loader2 size={10} className="animate-spin" />
                : pinned ? <><PinOff size={10} /> unpin</> : <><Pin size={10} /> pin this adjustment</>
              }
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Ghost Row (removed by context) ──────────────────────────────────────────

function GhostRow({ item, depth }: { item: Record<string, unknown>; depth: number }) {
  const indent = depth * 16
  const description = (item.description as string) || ''
  const itemType = (item.item_type as string) || 'subjective'

  const typeBadge = {
    deterministic: <span className="badge badge-blue opacity-40">DETERMINISTIC</span>,
    structural: <span className="badge badge-yellow opacity-40">STRUCTURAL</span>,
    subjective: <span className="badge badge-purple opacity-40">SUBJECTIVE</span>,
  }[itemType] ?? <span className="badge badge-gray opacity-40">{itemType}</span>

  return (
    <div style={{ marginLeft: indent }}>
      <div className="border border-dashed border-gray-300 rounded-lg p-3 bg-gray-50 opacity-50">
        <div className="flex items-center gap-2">
          <div className="w-4 flex-shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {typeBadge}
              <span className="text-sm font-medium text-gray-400 line-through">{description}</span>
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-red-50 border border-red-200 text-red-500">
                <X size={9} /> removed by context
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main Tree ────────────────────────────────────────────────────────────────

interface ChecklistTreeProps {
  items: ChecklistItem[]
  ruleId: string
  rule?: Rule | null
  onAnchorHover?: (anchor: string | null) => void
  selectedItemId?: string | null
  onItemSelect?: (itemId: string | null) => void
  highlightedItemId?: string | null
}

export default function ChecklistTree({ items, ruleId, rule, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId }: ChecklistTreeProps) {
  const [adding, setAdding] = useState(false)
  const [showGhosts, setShowGhosts] = useState(false)

  // Compute ghost items: items in base_checklist_json but not in current checklist
  const ghostItems = computeGhostItems(rule?.base_checklist_json as Record<string, unknown>[] | null, items)
  const hasGhosts = ghostItems.length > 0

  // Build a map from base_checklist_json for diffing and threshold comparison
  const baseItemMap = buildBaseItemMap(rule?.base_checklist_json as Record<string, unknown>[] | null)

  return (
    <div className="space-y-1">
      {items.length === 0 && !adding && (
        <div className="text-sm text-gray-400 italic py-4 text-center">
          No checklist items yet. Compile the rule to generate them, or add one manually.
        </div>
      )}
      {hasGhosts && (
        <div className="flex justify-end mb-1">
          <button
            className={`text-[10px] px-2 py-0.5 rounded-full transition-colors ${
              showGhosts
                ? 'bg-gray-200 text-gray-600'
                : 'bg-gray-100 text-gray-400 hover:bg-gray-200 hover:text-gray-600'
            }`}
            onClick={() => setShowGhosts(v => !v)}
          >
            {showGhosts ? 'Hide' : 'Show'} {ghostItems.length} removed by context
          </button>
        </div>
      )}
      {items.map(item => (
        <ChecklistNode
          key={item.id}
          item={item}
          ruleId={ruleId}
          depth={0}
          onAnchorHover={onAnchorHover}
          selectedItemId={selectedItemId}
          onItemSelect={onItemSelect}
          highlightedItemId={highlightedItemId}
          baseItemMap={baseItemMap}
        />
      ))}
      {showGhosts && ghostItems.map((ghost, i) => (
        <GhostRow key={`ghost-${i}`} item={ghost} depth={0} />
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

// ── Helpers for ghost items and base thresholds ─────────────────────────────

function computeGhostItems(baseChecklist: Record<string, unknown>[] | null, currentItems: ChecklistItem[]): Record<string, unknown>[] {
  if (!baseChecklist) return []
  const currentDescriptions = new Set(flattenDescriptions(currentItems))
  return flattenBaseItems(baseChecklist).filter(
    baseItem => !currentDescriptions.has((baseItem.description as string || '').toLowerCase().trim())
  )
}

function flattenDescriptions(items: ChecklistItem[]): string[] {
  const result: string[] = []
  for (const item of items) {
    result.push(item.description.toLowerCase().trim())
    if (item.children) result.push(...flattenDescriptions(item.children))
  }
  return result
}

function flattenBaseItems(items: Record<string, unknown>[]): Record<string, unknown>[] {
  const result: Record<string, unknown>[] = []
  for (const item of items) {
    result.push(item)
    const children = item.children as Record<string, unknown>[] | undefined
    if (children) result.push(...flattenBaseItems(children))
  }
  return result
}

type BaseItemMap = Map<string, Record<string, unknown>>

function buildBaseItemMap(baseChecklist: Record<string, unknown>[] | null): BaseItemMap {
  const map = new Map<string, Record<string, unknown>>()
  if (!baseChecklist) return map

  function walk(items: Record<string, unknown>[]) {
    for (const item of items) {
      const desc = (item.description as string || '').toLowerCase().trim()
      map.set(desc, item)
      const children = item.children as Record<string, unknown>[] | undefined
      if (children) walk(children)
    }
  }
  walk(baseChecklist)
  return map
}

/** Look up a base item by the persisted base_description (preferred) or by falling back to current description. */
function lookupBaseItem(baseItemMap: BaseItemMap, item: ChecklistItem): Record<string, unknown> | undefined {
  if (item.base_description) {
    const byBase = baseItemMap.get(item.base_description.toLowerCase().trim())
    if (byBase) return byBase
  }
  return baseItemMap.get(item.description.toLowerCase().trim())
}

function getBaseThreshold(baseItemMap: BaseItemMap, item: ChecklistItem): number | null {
  const baseItem = lookupBaseItem(baseItemMap, item)
  if (!baseItem) return null
  const logic = baseItem.logic as Record<string, unknown> | undefined
  if (logic && typeof logic.threshold === 'number') return logic.threshold
  return null
}

function getBaseRubric(baseItemMap: BaseItemMap, item: ChecklistItem): string | null {
  const baseItem = lookupBaseItem(baseItemMap, item)
  if (!baseItem) return null
  const logic = baseItem.logic as Record<string, unknown> | undefined
  if (logic && typeof logic.rubric === 'string') return logic.rubric
  return null
}

/** Infer context_change_types by diffing current item against base when field is null. */
function inferChangeTypes(item: ChecklistItem, baseItemMap: BaseItemMap): string[] {
  // If server already provided change types, use them
  if (item.context_change_types && item.context_change_types.length > 0) {
    return item.context_change_types
  }
  // If item is not context-influenced, nothing to infer
  if (!item.context_influenced) return []

  const baseItem = lookupBaseItem(baseItemMap, item)

  // No matching base item → it was added by context
  if (!baseItem) return ['new_item']

  const changes: string[] = []
  const baseLgc = (baseItem.logic as Record<string, unknown>) || {}
  const curLgc = (item.logic as Record<string, unknown>) || {}

  // Threshold change
  if (typeof baseLgc.threshold === 'number' && typeof curLgc.threshold === 'number' && baseLgc.threshold !== curLgc.threshold) {
    changes.push('threshold')
  }
  // Rubric change
  if (typeof baseLgc.rubric === 'string' && typeof curLgc.rubric === 'string' && baseLgc.rubric !== curLgc.rubric) {
    changes.push('rubric')
  }
  // Prompt template change
  if (typeof baseLgc.prompt_template === 'string' && typeof curLgc.prompt_template === 'string' && baseLgc.prompt_template !== curLgc.prompt_template) {
    changes.push('description')
  }
  // Action change
  if ((baseItem.action as string) !== item.action) {
    changes.push('action')
  }
  // Pattern change (deterministic)
  if (JSON.stringify(baseLgc.patterns) !== JSON.stringify(curLgc.patterns)) {
    changes.push('pattern')
  }
  // Check change (structural)
  if (JSON.stringify(baseLgc.checks) !== JSON.stringify(curLgc.checks)) {
    changes.push('check')
  }
  // Description itself changed
  if ((baseItem.description as string || '') !== item.description) {
    changes.push('description')
  }

  // If context_influenced but we couldn't detect specific changes, mark generically
  if (changes.length === 0) changes.push('rubric')

  return [...new Set(changes)]
}

// ─��� Logic Inspector ───────────────────────────────────────────────────────────

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
  const [action, setAction] = useState('warn')
  // The moderator picks the type up-front; the server fills in the matching
  // logic (regex / structural checks / rubric) for that type, and the user
  // can then tweak it via the edit form.
  const [itemType, setItemType] = useState<'deterministic' | 'structural' | 'subjective'>('subjective')
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => createChecklistItem(ruleId, { description, action, parent_id: parentId, item_type: itemType }),
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
      <div className="flex items-center gap-2 flex-wrap">
        <label className="flex items-center gap-1">
          <span className="text-gray-500">Type</span>
          <select
            className="border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none"
            value={itemType}
            onChange={e => setItemType(e.target.value as typeof itemType)}
          >
            <option value="subjective">subjective</option>
            <option value="deterministic">deterministic</option>
            <option value="structural">structural</option>
          </select>
        </label>
        <select
          className="border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none ml-auto"
          value={action}
          onChange={e => setAction(e.target.value)}
        >
          <option value="warn">Warn</option>
          <option value="remove">Remove</option>
          <option value="continue">Continue</option>
        </select>
        <button
          className="btn-primary text-xs py-1"
          onClick={() => mutation.mutate()}
          disabled={!description.trim() || mutation.isPending}
        >
          <Check size={12} />
          {mutation.isPending ? 'Generating...' : 'Add'}
        </button>
        <button className="btn-secondary text-xs py-1" onClick={onDone}>
          <X size={12} />
          Cancel
        </button>
      </div>
      <p className="text-[11px] text-gray-500">
        Server fills in the logic for the chosen type — open the item to edit it after.
      </p>
    </div>
  )
}

// ── ChecklistNode ─────────────────────────────────────────────────────────────

function ChecklistNode({ item, ruleId, depth, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId, baseItemMap }: { item: ChecklistItem; ruleId: string; depth: number; onAnchorHover?: (anchor: string | null) => void; selectedItemId?: string | null; onItemSelect?: (itemId: string | null) => void; highlightedItemId?: string | null; baseItemMap: BaseItemMap }) {
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const [showLogic, setShowLogic] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [addingChild, setAddingChild] = useState(false)
  const [editedDescription, setEditedDescription] = useState(item.description)
  const [editedFailAction, setEditedFailAction] = useState(item.action)
  const [editedItemType, setEditedItemType] = useState<'deterministic' | 'structural' | 'subjective'>(item.item_type)
  const [editedLogic, setEditedLogic] = useState<Record<string, unknown>>(item.logic ?? defaultLogicFor(item.item_type))

  // Structural field schema is needed only inside the editor; cache it
  // app-wide so opening multiple items doesn't refetch.
  const structuralFieldsQuery = useQuery({
    queryKey: ['structural-fields'],
    queryFn: getStructuralFields,
    staleTime: Infinity,
  })

  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: (data: Partial<ChecklistItem> & { user_edited_logic?: boolean }) =>
      updateChecklistItem(item.id, data as Partial<ChecklistItem>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      setEditing(false)
    },
  })

  const regenerateMutation = useMutation({
    // Sending user_edited_logic=false (with no logic in the body) tells the
    // server to clear the pin and re-infer item_type/logic from the current
    // description. The list re-fetches and the editor closes.
    mutationFn: () => updateChecklistItem(item.id, { user_edited_logic: false } as Partial<ChecklistItem>),
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
    warn: <span className="badge badge-yellow">WARN</span>,
    continue: <span className="badge badge-gray">continue</span>,
  }[item.action] ?? null

  const hasChildren = item.children && item.children.length > 0
  const indent = depth * 16

  const handleSave = () => {
    // Detect whether the user actually changed type or logic. If they did,
    // ship those fields so the backend flips user_edited_logic = true and
    // skips re-inference. If they only touched the description, leave them
    // out so the existing re-infer-on-description-change behavior still
    // works for non-pinned items.
    const typeChanged = editedItemType !== item.item_type
    const logicChanged = JSON.stringify(editedLogic) !== JSON.stringify(item.logic ?? {})
    const payload: Partial<ChecklistItem> & { user_edited_logic?: boolean } = {
      description: editedDescription,
      action: editedFailAction as 'remove' | 'warn' | 'continue',
    }
    if (typeChanged) payload.item_type = editedItemType
    if (typeChanged || logicChanged) payload.logic = editedLogic
    mutation.mutate(payload)
  }

  const isSelected = selectedItemId === item.id
  const isHighlighted = highlightedItemId === item.id

  // Infer change types from base diff when server field is null
  const effectiveChangeTypes = inferChangeTypes(item, baseItemMap)
  const isContextAdded = effectiveChangeTypes.includes('new_item')

  // Look up base threshold for this item by description match
  const currentThreshold = item.item_type === 'subjective' ? (item.logic as Record<string, unknown>).threshold as number | null : null
  const baseThreshold = getBaseThreshold(baseItemMap, item)
  const baseRubric = getBaseRubric(baseItemMap, item)
  const currentRubric = item.item_type === 'subjective' ? (item.logic as Record<string, unknown>).rubric as string | null : null

  return (
    <div style={{ marginLeft: indent }}>
      <div
        className={`border rounded-lg p-3 transition-colors cursor-pointer ${
          isContextAdded
            ? 'bg-emerald-50/30 border-dashed border-emerald-300 hover:border-emerald-400'
            : isSelected ? 'bg-white border-indigo-400 ring-1 ring-indigo-300'
            : isHighlighted ? 'bg-amber-50 border-amber-400 ring-1 ring-amber-300'
            : 'bg-white border-gray-200 hover:border-gray-300'
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
              {item.user_edited_logic && (
                <span
                  className="badge bg-emerald-50 text-emerald-700 border border-emerald-200"
                  title="Logic was hand-edited; recompile leaves it alone"
                >
                  USER-EDITED
                </span>
              )}
              {!editing && (
                <span className="text-sm font-medium text-gray-800">{item.description}</span>
              )}
            </div>

            {editing ? (
              <div className="mt-2 space-y-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Description</label>
                  <textarea
                    rows={3}
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
                      <option value="warn">warn</option>
                      <option value="continue">continue</option>
                    </select>
                  </div>
                )}
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Type</label>
                  <div className="flex gap-3 text-xs">
                    {(['deterministic', 'structural', 'subjective'] as const).map(t => (
                      <label key={t} className="flex items-center gap-1 cursor-pointer">
                        <input
                          type="radio"
                          name={`item-type-${item.id}`}
                          value={t}
                          checked={editedItemType === t}
                          onChange={() => {
                            setEditedItemType(t)
                            // Switching types resets logic to the default shape for the
                            // new type — partial cross-type logic objects would render
                            // garbage in the editor.
                            setEditedLogic(defaultLogicFor(t))
                          }}
                        />
                        <span>{t}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Logic</label>
                  <div className="border border-gray-200 rounded px-2 py-2 bg-gray-50">
                    <LogicEditor
                      itemType={editedItemType}
                      logic={editedLogic}
                      onChange={setEditedLogic}
                      structuralFields={structuralFieldsQuery.data ?? []}
                    />
                  </div>
                </div>
                <div className="flex gap-2 flex-wrap">
                  <button className="btn-primary text-xs py-1" onClick={handleSave} disabled={mutation.isPending}>
                    <Check size={12} />
                    {mutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                  <button className="btn-secondary text-xs py-1" onClick={() => setEditing(false)}>
                    <X size={12} />
                    Cancel
                  </button>
                  {item.user_edited_logic && (
                    <button
                      className="btn-secondary text-xs py-1 ml-auto"
                      title="Discard your hand-edited logic and re-infer from the description"
                      onClick={() => regenerateMutation.mutate()}
                      disabled={regenerateMutation.isPending}
                    >
                      <RefreshCw size={12} />
                      {regenerateMutation.isPending ? 'Regenerating...' : 'Regenerate from description'}
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <>
                {item.context_influenced && (
                  <ContextBadge
                    itemId={item.id}
                    note={item.context_note}
                    changeTypes={effectiveChangeTypes}
                    pinned={item.context_pinned}
                    overrideNote={item.context_override_note}
                    ruleId={ruleId}
                    threshold={currentThreshold}
                    baseThreshold={baseThreshold}
                    baseRubric={baseRubric}
                    currentRubric={currentRubric}
                  />
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
                  onClick={() => {
                    // Reseed the editor from the current item so reopening
                    // after a server-side change (e.g. recompile) doesn't
                    // show stale state from the previous edit session.
                    setEditedDescription(item.description)
                    setEditedFailAction(item.action)
                    setEditedItemType(item.item_type)
                    setEditedLogic(item.logic ?? defaultLogicFor(item.item_type))
                    setEditing(true)
                  }}
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
            <ChecklistNode key={child.id} item={child} ruleId={ruleId} depth={depth + 1} onAnchorHover={onAnchorHover} selectedItemId={selectedItemId} onItemSelect={onItemSelect} highlightedItemId={highlightedItemId} baseItemMap={baseItemMap} />
          ))}
          {addingChild && (
            <AddItemForm ruleId={ruleId} parentId={item.id} onDone={() => setAddingChild(false)} />
          )}
        </div>
      )}
    </div>
  )
}
