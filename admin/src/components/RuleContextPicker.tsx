import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from 'react'
import { Plus, Trash2, Check } from 'lucide-react'
import type {
  CommunityContext,
  CommunityContextNote,
  Rule,
  RuleContextTag,
} from '../api/client'

export interface RuleContextPickerHandle {
  savePreview: () => Promise<void>
}

interface Props {
  rule: Rule
  community_context: CommunityContext | null
  onSavePreview: (data: {
    relevant_context: RuleContextTag[] | null
    custom_context_notes: CommunityContextNote[]
  }) => Promise<void>
  onDirtyChange?: (dirty: boolean) => void
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

const RuleContextPicker = forwardRef<RuleContextPickerHandle, Props>(function RuleContextPicker({
  rule,
  community_context,
  onSavePreview,
  onDirtyChange,
  readOnly,
}, ref) {
  const allBundles = useMemo(() => {
    const out: { dim: keyof CommunityContext; tag: string; text: string }[] = []
    if (!community_context) return out
    for (const { key } of DIMENSIONS) {
      const dim = community_context[key]
      if (!dim) continue
      for (const raw of dim.notes) {
        const note =
          typeof raw === 'string' ? { text: raw, tag: '' } : raw
        if (!note.tag) continue
        out.push({ dim: key, tag: note.tag, text: note.text })
      }
    }
    return out
  }, [community_context])

  const defaultSelected = useMemo(() => {
    if (rule.relevant_context === null || rule.relevant_context === undefined) {
      return new Set(allBundles.map(b => keyOf(b.dim, b.tag)))
    }
    return new Set(rule.relevant_context.map(t => keyOf(t.dimension, t.tag)))
  }, [rule.relevant_context, allBundles])

  const [selected, setSelected] = useState<Set<string>>(defaultSelected)
  const [useAll, setUseAll] = useState<boolean>(
    rule.relevant_context === null || rule.relevant_context === undefined,
  )
  const [customNotes, setCustomNotes] = useState<CommunityContextNote[]>(
    rule.custom_context_notes || [],
  )
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    setSelected(defaultSelected)
    setUseAll(rule.relevant_context === null || rule.relevant_context === undefined)
    setCustomNotes(rule.custom_context_notes || [])
    setDirty(false)
  }, [rule.id, defaultSelected, rule.relevant_context, rule.custom_context_notes])

  useEffect(() => {
    onDirtyChange?.(dirty)
  }, [dirty, onDirtyChange])

  const toggleBundle = (dim: keyof CommunityContext, tag: string) => {
    const k = keyOf(dim, tag)
    const next = new Set(selected)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    setSelected(next)
    setUseAll(false)
    setDirty(true)
  }

  const handleUseAll = () => {
    setUseAll(true)
    setSelected(new Set(allBundles.map(b => keyOf(b.dim, b.tag))))
    setDirty(true)
  }

  const handleUseNone = () => {
    setUseAll(false)
    setSelected(new Set())
    setDirty(true)
  }

  const handleSavePreview = async () => {
    const relevantContext: RuleContextTag[] | null = useAll
      ? null
      : allBundles
          .filter(b => selected.has(keyOf(b.dim, b.tag)))
          .map(b => ({ dimension: b.dim as string, tag: b.tag }))
    const cleanNotes = customNotes
      .map(n => ({ text: (n.text || '').trim(), tag: (n.tag || '').trim() }))
      .filter(n => n.text)
    await onSavePreview({
      relevant_context: relevantContext,
      custom_context_notes: cleanNotes,
    })
    setDirty(false)
  }

  useImperativeHandle(ref, () => ({ savePreview: handleSavePreview }))

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
              className="text-xs px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
              onClick={handleUseAll}
              type="button"
            >
              All
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

      {DIMENSIONS.map(({ label, key }) => {
        const bundles = allBundles.filter(b => b.dim === key)
        if (bundles.length === 0) return null
        return (
          <div key={key}>
            <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1">
              {label}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {bundles.map(b => {
                const k = keyOf(b.dim, b.tag)
                const checked = selected.has(k)
                return (
                  <button
                    key={k}
                    type="button"
                    disabled={readOnly}
                    className={`text-xs px-2 py-0.5 rounded-full border flex items-center gap-1 ${
                      readOnly
                        ? checked
                          ? 'bg-indigo-50 border-indigo-200 text-indigo-600 cursor-default'
                          : 'bg-white border-gray-200 text-gray-300 cursor-default'
                        : checked
                        ? 'bg-indigo-100 border-indigo-300 text-indigo-700 font-medium transition-colors'
                        : 'bg-white border-gray-200 text-gray-400 hover:border-indigo-300 hover:text-indigo-600 transition-colors'
                    }`}
                    onClick={() => !readOnly && toggleBundle(b.dim, b.tag)}
                    title={b.text}
                  >
                    {checked && <Check size={10} />}
                    {b.tag.replace(/_/g, ' ')}
                  </button>
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
                onClick={() => {
                  setCustomNotes([...customNotes, { text: '', tag: '' }])
                  setDirty(true)
                }}
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
                    setCustomNotes(next)
                    setDirty(true)
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
                    setCustomNotes(next)
                    setDirty(true)
                  }}
                  placeholder="e.g., Seeking advice here still means specific legal advice is dangerous"
                  disabled={readOnly}
                />
                {!readOnly && (
                  <button
                    type="button"
                    className="text-gray-400 hover:text-red-500 p-1 flex-shrink-0"
                    onClick={() => {
                      setCustomNotes(customNotes.filter((_, j) => j !== i))
                      setDirty(true)
                    }}
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
})

export default RuleContextPicker
