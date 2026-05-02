import { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2, Sparkles } from 'lucide-react'
import Tooltip from './Tooltip'
import type {
  CommunityContext,
  CommunityContextNote,
  Rule,
  RuleContextTag,
} from '../api/client'
import { matchRuleContext } from '../api/client'

interface Props {
  rule: Rule
  community_context: CommunityContext | null
  onChange: (data: {
    relevant_context: RuleContextTag[] | null
    custom_context_notes: CommunityContextNote[]
  }) => void
  readOnly?: boolean
}

const DIMENSIONS: { label: string; key: keyof CommunityContext }[] = [
  { label: 'Purpose', key: 'purpose' },
  { label: 'Participants', key: 'participants' },
  { label: 'Stakes', key: 'stakes' },
  { label: 'Tone', key: 'tone' },
]

function keyOf(dim: string, tag: string): string {
  return `${dim}::${tag}`
}

// Weight semantics shown on hover/under each slider.
function weightLabel(w: number): { text: string; cls: string } {
  if (w < 0.25) return { text: 'ignore', cls: 'text-gray-400' }
  if (w < 0.75) return { text: 'informs', cls: 'text-indigo-500' }
  return { text: 'strongly informs', cls: 'text-indigo-700 font-medium' }
}

function snapWeight(w: number): number {
  // snap to nearest 0.5, clamp to [0, 1]
  const rounded = Math.round(w * 2) / 2
  if (rounded < 0) return 0
  if (rounded > 1) return 1
  return rounded
}

function RuleContextPicker({
  rule,
  community_context,
  onChange,
  readOnly,
}: Props) {
  const allBundles = useMemo(() => {
    const out: { dim: keyof CommunityContext; tag: string; text: string }[] = []
    if (!community_context) return out
    for (const { key } of DIMENSIONS) {
      const dim = community_context[key]
      if (!dim) continue
      for (const raw of dim.notes) {
        const note = typeof raw === 'string' ? { text: raw, tag: '' } : raw
        if (!note.tag) continue
        out.push({ dim: key, tag: note.tag, text: note.text })
      }
    }
    return out
  }, [community_context])

  // Initial weights derived from rule.relevant_context.
  // null/undefined → unmatched: every tag at +1 (mirrors the legacy "use all" semantics
  // until the auto-match runs on first compile).
  // [] → opted out: every tag at 0.
  // list → use the weights present, default missing entries to 0.
  const initialWeights = useMemo(() => {
    const m: Record<string, number> = {}
    if (rule.relevant_context === null || rule.relevant_context === undefined) {
      for (const b of allBundles) m[keyOf(b.dim, b.tag)] = 1
    } else {
      const byKey = new Map(
        rule.relevant_context.map(t => [keyOf(t.dimension, t.tag), t.weight ?? 1] as const),
      )
      for (const b of allBundles) {
        const raw = byKey.get(keyOf(b.dim, b.tag)) ?? 0
        m[keyOf(b.dim, b.tag)] = snapWeight(raw)
      }
    }
    return m
  }, [rule.relevant_context, allBundles])

  const [weights, setWeights] = useState<Record<string, number>>(initialWeights)
  const [customNotes, setCustomNotes] = useState<CommunityContextNote[]>(
    rule.custom_context_notes || [],
  )
  const [matching, setMatching] = useState(false)
  const [matchError, setMatchError] = useState<string | null>(null)

  useEffect(() => {
    setWeights(initialWeights)
    setCustomNotes(rule.custom_context_notes || [])
  }, [rule.id, initialWeights, rule.custom_context_notes])

  const emitChange = (
    nextWeights: Record<string, number>,
    nextNotes: CommunityContextNote[],
  ) => {
    const relevantContext: RuleContextTag[] = allBundles
      .map(b => {
        const w = nextWeights[keyOf(b.dim, b.tag)] ?? 0
        return { dimension: b.dim as string, tag: b.tag, weight: w }
      })
      .filter(t => (t.weight ?? 0) !== 0)
    const cleanNotes = nextNotes
      .map(n => ({ text: (n.text || '').trim(), tag: (n.tag || '').trim() }))
      .filter(n => n.text)
    onChange({ relevant_context: relevantContext, custom_context_notes: cleanNotes })
  }

  const updateWeight = (dim: keyof CommunityContext, tag: string, w: number) => {
    const next = { ...weights, [keyOf(dim, tag)]: snapWeight(w) }
    setWeights(next)
    emitChange(next, customNotes)
  }

  const handleUseAll = () => {
    const next: Record<string, number> = {}
    for (const b of allBundles) next[keyOf(b.dim, b.tag)] = 1
    setWeights(next)
    emitChange(next, customNotes)
  }

  const handleUseNone = () => {
    const next: Record<string, number> = {}
    for (const b of allBundles) next[keyOf(b.dim, b.tag)] = 0
    setWeights(next)
    emitChange(next, customNotes)
  }

  const handleAutoMatch = async () => {
    setMatching(true)
    setMatchError(null)
    try {
      const result = await matchRuleContext(rule.id)
      const next: Record<string, number> = {}
      for (const b of allBundles) next[keyOf(b.dim, b.tag)] = 0
      for (const t of result.relevant_context || []) {
        next[keyOf(t.dimension, t.tag)] = snapWeight(t.weight ?? 1)
      }
      setWeights(next)
      emitChange(next, customNotes)
    } catch (e: any) {
      setMatchError(e?.response?.data?.detail || 'Auto-match failed')
    } finally {
      setMatching(false)
    }
  }

  const updateNotes = (next: CommunityContextNote[]) => {
    setCustomNotes(next)
    emitChange(weights, next)
  }

  if (!community_context || allBundles.length === 0) {
    return (
      <div className="text-xs text-gray-400 italic">
        No community context generated yet — generate it in Community Settings to enable per-rule context selection.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Relevant Community Context
          </h4>
        </div>
        {!readOnly && (
          <div className="flex gap-1.5">
            <button
              className="text-xs px-2 py-0.5 rounded border border-indigo-200 text-indigo-600 hover:bg-indigo-50 transition-colors flex items-center gap-1 disabled:opacity-50"
              onClick={handleAutoMatch}
              type="button"
              disabled={matching}
              title="Let the LLM pick which tags inform this rule, with weights"
            >
              <Sparkles size={11} />
              {matching ? 'Matching…' : 'Auto-match'}
            </button>
            <button
              className="text-xs px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
              onClick={handleUseAll}
              type="button"
            >
              All +1
            </button>
            <button
              className="text-xs px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
              onClick={handleUseNone}
              type="button"
            >
              None
            </button>
          </div>
        )}
      </div>

      {matchError && (
        <div className="text-xs text-rose-600">{matchError}</div>
      )}

      {DIMENSIONS.map(({ label, key }) => {
        const bundles = allBundles.filter(b => b.dim === key)
        if (bundles.length === 0) return null
        return (
          <div key={key}>
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1">
              {label}
            </div>
            <div className="space-y-1.5">
              {bundles.map(b => {
                const k = keyOf(b.dim, b.tag)
                const w = weights[k] ?? 0
                const lbl = weightLabel(w)
                return (
                  <div key={k} className="flex items-center gap-2 text-xs">
                    <Tooltip
                      content={b.text || <span className="italic text-gray-300">No description</span>}
                      className="w-32"
                    >
                      <span
                        className={`truncate border-b border-dotted border-gray-300 cursor-help ${
                          w === 0 ? 'text-gray-400' : 'text-gray-700 font-medium'
                        }`}
                      >
                        {b.tag.replace(/_/g, ' ')}
                      </span>
                    </Tooltip>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.5}
                      value={w}
                      disabled={readOnly}
                      onChange={e => updateWeight(b.dim, b.tag, parseFloat(e.target.value))}
                      className="w-20 accent-indigo-500"
                      title={`weight ${w}`}
                    />
                    <div className={`flex-1 ${lbl.cls}`}>
                      {lbl.text}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}

      {/* Custom rule notes */}
      {(!readOnly || customNotes.length > 0) && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">
              Rule-specific notes
              <span className="ml-1 normal-case text-gray-400 font-normal">
                (overrides or extras that don't fit any community tag)
              </span>
            </div>
            {!readOnly && (
              <button
                className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-0.5"
                type="button"
                onClick={() => updateNotes([...customNotes, { text: '', tag: '' }])}
              >
                <Plus size={11} /> Add note
              </button>
            )}
          </div>
          <div className="space-y-1.5">
            {customNotes.map((note, i) => (
              <div key={i} className="flex gap-1.5 items-start">
                <input
                  className="w-24 text-xs border border-gray-200 rounded px-2 py-1 focus:outline-none focus:border-indigo-400 flex-shrink-0 disabled:bg-gray-50 disabled:text-gray-500"
                  value={note.tag || ''}
                  onChange={e => {
                    const next = [...customNotes]
                    next[i] = { ...note, tag: e.target.value }
                    updateNotes(next)
                  }}
                  placeholder="tag (optional)"
                  disabled={readOnly}
                />
                <input
                  className="flex-1 text-xs border border-gray-200 rounded px-2 py-1 focus:outline-none focus:border-indigo-400 disabled:bg-gray-50 disabled:text-gray-500"
                  value={note.text}
                  onChange={e => {
                    const next = [...customNotes]
                    next[i] = { ...note, text: e.target.value }
                    updateNotes(next)
                  }}
                  placeholder="e.g., Seeking advice here still means specific legal advice is dangerous"
                  disabled={readOnly}
                />
                {!readOnly && (
                  <button
                    type="button"
                    className="text-gray-400 hover:text-red-500 p-1 flex-shrink-0"
                    onClick={() => updateNotes(customNotes.filter((_, j) => j !== i))}
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            ))}
            {customNotes.length === 0 && !readOnly && (
              <p className="text-xs text-gray-400 italic">No custom notes for this rule.</p>
            )}
          </div>
        </div>
      )}

    </div>
  )
}

export default RuleContextPicker
