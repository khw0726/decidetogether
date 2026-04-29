import { useState } from 'react'
import { ChevronRight, ChevronDown, Edit2, Check, X, Code, Trash2, Plus, Sparkles, Pin, PinOff, Loader2, Gauge, FileText, Type, Zap, PlusCircle, Search } from 'lucide-react'
import { ChecklistItem, createChecklistItem, updateChecklistItem, deleteChecklistItem, setContextOverride, Rule, ItemHealthMetrics } from '../api/client'
import { useMutation, useQueryClient } from '@tanstack/react-query'

// ── Health Chip ──────────────────────────────────────────────────────────────

function HealthChip({ metrics }: { metrics: ItemHealthMetrics }) {
  const [open, setOpen] = useState(false)
  if (metrics.decision_count === 0) return null
  const fpRate = metrics.false_positive_rate
  const fnRate = metrics.false_negative_rate
  const errorRate = Math.max(fpRate, fnRate)
  const errorCount = metrics.false_positive_count + metrics.false_negative_count
  const unhealthy = errorRate > 0.15
  const errorPct = Math.round(errorRate * 100)
  const tone = unhealthy
    ? 'bg-amber-50 text-amber-700 border-amber-300 hover:bg-amber-100'
    : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border text-[10px] font-medium transition-colors ${tone}`}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onClick={(e) => { e.stopPropagation(); setOpen(v => !v) }}
        title=""
      >
        {unhealthy ? '⚠' : '✓'} {unhealthy ? `${errorPct}% error` : `${metrics.decision_count} ok`}
      </button>
      {open && (
        <div
          className="absolute left-0 top-full mt-1 z-30 w-72 bg-white border border-gray-200 rounded-lg shadow-lg p-2 text-[11px] text-gray-700 cursor-default"
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="grid grid-cols-3 gap-1.5 mb-1.5">
            <div className="bg-red-50 border border-red-100 rounded px-1.5 py-1 text-center">
              <p className="text-[9px] text-red-500 font-semibold uppercase tracking-wide">Wrongly flagged</p>
              <p className="text-sm font-bold text-red-700 leading-tight">{Math.round(fpRate * 100)}%</p>
              <p className="text-[9px] text-red-400">{metrics.false_positive_count}/{metrics.decision_count}</p>
            </div>
            <div className="bg-amber-50 border border-amber-100 rounded px-1.5 py-1 text-center">
              <p className="text-[9px] text-amber-600 font-semibold uppercase tracking-wide">Missed</p>
              <p className="text-sm font-bold text-amber-700 leading-tight">{Math.round(fnRate * 100)}%</p>
              <p className="text-[9px] text-amber-500">{metrics.false_negative_count}/{metrics.decision_count}</p>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded px-1.5 py-1 text-center">
              <p className="text-[9px] text-gray-500 font-semibold uppercase tracking-wide">Decisions</p>
              <p className="text-sm font-bold text-gray-700 leading-tight">{metrics.decision_count}</p>
              {metrics.avg_confidence_correct != null && (
                <p className="text-[9px] text-gray-400">conf {metrics.avg_confidence_correct.toFixed(2)}</p>
              )}
            </div>
          </div>
          {errorCount > 0 ? (
            <p className="text-[10px] text-gray-500">
              {metrics.false_positive_count} wrongly flagged, {metrics.false_negative_count} missed
              {metrics.avg_confidence_errors != null && ` · errors avg conf ${metrics.avg_confidence_errors.toFixed(2)}`}
            </p>
          ) : (
            <p className="text-[10px] text-emerald-600">No errors recorded.</p>
          )}
        </div>
      )}
    </span>
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
  itemHealthById?: Record<string, ItemHealthMetrics>
}

export default function ChecklistTree({ items, ruleId, rule, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId, itemHealthById }: ChecklistTreeProps) {
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
          itemHealthById={itemHealthById}
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

function ChecklistNode({ item, ruleId, depth, onAnchorHover, selectedItemId, onItemSelect, highlightedItemId, baseItemMap, itemHealthById }: { item: ChecklistItem; ruleId: string; depth: number; onAnchorHover?: (anchor: string | null) => void; selectedItemId?: string | null; onItemSelect?: (itemId: string | null) => void; highlightedItemId?: string | null; baseItemMap: BaseItemMap; itemHealthById?: Record<string, ItemHealthMetrics> }) {
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
    warn: <span className="badge badge-yellow">WARN</span>,
    continue: <span className="badge badge-gray">continue</span>,
  }[item.action] ?? null

  const hasChildren = item.children && item.children.length > 0
  const indent = depth * 16

  const handleSave = () => {
    mutation.mutate({
      description: editedDescription,
      action: editedFailAction as 'remove' | 'warn' | 'continue',
    })
  }

  const isSelected = selectedItemId === item.id
  const isHighlighted = highlightedItemId === item.id

  // Infer change types from base diff when server field is null
  const effectiveChangeTypes = inferChangeTypes(item, baseItemMap)
  const isContextAdded = effectiveChangeTypes.includes('new_item')

  const healthMetrics = itemHealthById?.[item.id]
  const errorRate = healthMetrics
    ? Math.max(healthMetrics.false_positive_rate, healthMetrics.false_negative_rate)
    : 0
  const isUnhealthy = !!healthMetrics && healthMetrics.decision_count > 0 && errorRate > 0.15

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
            : isUnhealthy ? 'bg-amber-50/40 border-amber-300 hover:border-amber-400'
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
              {!editing && (
                <span className="text-sm font-medium text-gray-800">{item.description}</span>
              )}
            </div>
            {!editing && healthMetrics && healthMetrics.decision_count > 0 && (
              <div className="mt-1">
                <HealthChip metrics={healthMetrics} />
              </div>
            )}

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
                      <option value="warn">warn</option>
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
            <ChecklistNode key={child.id} item={child} ruleId={ruleId} depth={depth + 1} onAnchorHover={onAnchorHover} selectedItemId={selectedItemId} onItemSelect={onItemSelect} highlightedItemId={highlightedItemId} baseItemMap={baseItemMap} itemHealthById={itemHealthById} />
          ))}
          {addingChild && (
            <AddItemForm ruleId={ruleId} parentId={item.id} onDone={() => setAddingChild(false)} />
          )}
        </div>
      )}
    </div>
  )
}
