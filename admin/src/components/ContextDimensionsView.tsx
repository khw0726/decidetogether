import { useState, useEffect } from 'react'
import { Loader2, Pencil } from 'lucide-react'
import { CommunityContext, CommunityContextDimension, CommunityContextNote, type ContextTaxonomy, getContextTaxonomy, getContextTagUsage, type TagUsageEntry } from '../api/client'

export const DIMENSION_META: [string, keyof CommunityContext][] = [
  ['Purpose', 'purpose'],
  ['Participants', 'participants'],
  ['Stakes', 'stakes'],
  ['Tone', 'tone'],
]

/** Normalize a note from either old (string) or new ({text, tag}) format. */
function normalizeNote(note: string | CommunityContextNote): CommunityContextNote {
  if (typeof note === 'string') return { text: note, tag: '' }
  return { text: note.text ?? '', tag: note.tag ?? '' }
}

/** Derive unique tags from notes, preserving order. */
function deriveTags(notes: (string | CommunityContextNote)[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const n of notes) {
    const tag = typeof n === 'string' ? '' : (n.tag ?? '')
    if (tag && !seen.has(tag)) {
      seen.add(tag)
      result.push(tag)
    }
  }
  return result
}

interface Props {
  context: CommunityContext
  communityId: string
  // When omitted, the Regenerate button is hidden entirely.
  onRegenerate?: () => void
  isRegenerating?: boolean
  onSaveDimension?: (key: keyof CommunityContext, dim: CommunityContextDimension) => Promise<void>
  isSaving?: boolean
}

export default function ContextDimensionsView({
  context,
  communityId,
  onRegenerate,
  isRegenerating,
  onSaveDimension,
  isSaving,
}: Props) {
  const [expandedDim, setExpandedDim] = useState<string | null>(null)
  const [editingDim, setEditingDim] = useState<string | null>(null)
  const [editNotes, setEditNotes] = useState<CommunityContextNote[]>([])
  const [taxonomy, setTaxonomy] = useState<ContextTaxonomy | null>(null)
  const [tagUsage, setTagUsage] = useState<Record<string, TagUsageEntry>>({})

  useEffect(() => {
    getContextTaxonomy().then(setTaxonomy).catch(() => {})
  }, [])

  useEffect(() => {
    if (!communityId) return
    getContextTagUsage(communityId)
      .then(entries => {
        const m: Record<string, TagUsageEntry> = {}
        for (const e of entries) m[`${e.dimension}::${e.tag}`] = e
        setTagUsage(m)
      })
      .catch(() => {})
  }, [communityId, context])

  const usageFor = (dim: string, tag: string): TagUsageEntry | undefined =>
    tagUsage[`${dim}::${tag}`]

  const startEdit = (key: string, dim: CommunityContextDimension) => {
    setEditingDim(key)
    setExpandedDim(key)
    setEditNotes(dim.notes.map(normalizeNote))
  }

  const cancelEdit = () => {
    setEditingDim(null)
    setEditNotes([])
  }

  const saveEdit = async (key: keyof CommunityContext) => {
    if (!onSaveDimension) return
    await onSaveDimension(key, {
      notes: editNotes.filter(n => n.tag),
    })
    setEditingDim(null)
  }

  const toggleTag = (tag: string) => {
    const existing = editNotes.findIndex(n => n.tag === tag)
    if (existing >= 0) {
      setEditNotes(editNotes.filter((_, i) => i !== existing))
    } else {
      setEditNotes([...editNotes, { tag, text: '' }])
    }
  }

  const updateNoteText = (tag: string, text: string) => {
    setEditNotes(editNotes.map(n => n.tag === tag ? { ...n, text } : n))
  }

  return (
    <>
      <div className="space-y-2">
        {DIMENSION_META.map(([label, key]) => {
          const dim = context[key]
          if (!dim) return null
          const isOpen = expandedDim === key
          const isEditing = editingDim === key
          const displayTags = isEditing ? deriveTags(editNotes) : deriveTags(dim.notes)

          return (
            <div
              key={key}
              className="rounded-lg bg-gray-50 border border-gray-200"
              data-log-context={JSON.stringify({ dimension: key })}
            >
              <button
                data-log="context.dimension.toggle-expand"
                className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-gray-100 transition-colors rounded-lg"
                onClick={() => {
                  if (!isEditing) setExpandedDim(isOpen ? null : key)
                }}
              >
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider w-24 flex-shrink-0">
                  {label}
                  {dim.manually_edited && (
                    <span className="ml-1 text-indigo-400" title="Manually edited">*</span>
                  )}
                </span>
                <div className="flex flex-wrap gap-1.5 flex-1">
                  {displayTags.map(tag => {
                    // Find the explanation text for this tag to show on hover
                    const notes = isEditing ? editNotes : dim.notes.map(normalizeNote)
                    const noteForTag = notes.find(n => (typeof n === 'string' ? '' : n.tag) === tag)
                    const usage = usageFor(key, tag)
                    const usageStr = usage && usage.rule_count > 0
                      ? ` · used by ${usage.rule_count} rule${usage.rule_count > 1 ? 's' : ''} (Σ ${usage.weight_sum.toFixed(1)})`
                      : ' · unused by rules'
                    const hoverText = (
                      noteForTag && typeof noteForTag !== 'string' && noteForTag.text
                        ? `${tag.replace(/_/g, ' ')}: ${noteForTag.text}`
                        : taxonomy?.[key]?.[tag] || tag.replace(/_/g, ' ')
                    ) + usageStr
                    const heavy = usage && usage.weight_sum >= 1.5
                    return (
                      <span
                        key={tag}
                        className={`text-xs px-2 py-0.5 rounded-full font-medium flex items-center gap-1 ${
                          heavy
                            ? 'bg-indigo-100 text-indigo-700'
                            : usage && usage.rule_count > 0
                              ? 'bg-indigo-50 text-indigo-600'
                              : 'bg-gray-100 text-gray-400'
                        }`}
                        title={hoverText}
                      >
                        {tag.replace(/_/g, ' ')}
                        {usage && usage.rule_count > 0 && (
                          <span className="text-[9px] opacity-70">×{usage.rule_count}</span>
                        )}
                      </span>
                    )
                  })}
                  {displayTags.length === 0 && !isEditing && (
                    <span className="text-xs text-gray-400 italic">no tags</span>
                  )}
                </div>
                {!isEditing && onSaveDimension && (
                  <button
                    data-log="context.dimension.edit-start"
                    className="text-gray-400 hover:text-indigo-600 flex-shrink-0 p-1"
                    onClick={(e) => { e.stopPropagation(); startEdit(key, dim) }}
                    title="Edit this dimension"
                  >
                    <Pencil size={13} />
                  </button>
                )}
                <span className="text-xs text-gray-300 flex-shrink-0">{isOpen ? '▾' : '▸'}</span>
              </button>

              {/* Read-only expanded view — tag-first layout */}
              {isOpen && !isEditing && (
                <div className="px-4 pb-3 pt-0">
                  <div className="border-t border-gray-200 pt-2 space-y-2">
                    {(() => {
                      // Group notes by tag, preserving order
                      const grouped: { tag: string; texts: string[] }[] = []
                      const tagIndex = new Map<string, number>()
                      for (const rawNote of dim.notes) {
                        const note = normalizeNote(rawNote)
                        const tag = note.tag || '_untagged'
                        if (tagIndex.has(tag)) {
                          grouped[tagIndex.get(tag)!].texts.push(note.text)
                        } else {
                          tagIndex.set(tag, grouped.length)
                          grouped.push({ tag, texts: note.text ? [note.text] : [] })
                        }
                      }
                      if (grouped.length === 0) {
                        return <p className="text-sm text-gray-400 italic">No context notes yet.</p>
                      }
                      return grouped.map(({ tag, texts }) => (
                        <div key={tag} className="flex gap-2 items-start">
                          {tag !== '_untagged' ? (
                            <span
                              className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium flex-shrink-0 mt-0.5"
                              title={taxonomy?.[key]?.[tag] || tag}
                            >
                              {tag.replace(/_/g, ' ')}
                            </span>
                          ) : (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium flex-shrink-0 mt-0.5">
                              general
                            </span>
                          )}
                          <div className="flex-1 min-w-0">
                            {texts.length > 0 ? (
                              texts.map((t, i) => (
                                <p key={i} className="text-sm text-gray-600 leading-relaxed">{t}</p>
                              ))
                            ) : (
                              <p className="text-sm text-gray-400 italic">No explanation added</p>
                            )}
                          </div>
                        </div>
                      ))
                    })()}
                  </div>
                </div>
              )}

              {/* Edit mode */}
              {isEditing && (
                <div className="px-4 pb-4 pt-0 border-t border-gray-200 mt-0 space-y-3">
                  {/* Tag pills selector */}
                  <div className="pt-3">
                    <label className="text-xs font-medium text-gray-500 block mb-1.5">Tags</label>
                    {taxonomy && taxonomy[key] ? (
                      <div className="flex flex-wrap gap-1.5">
                        {Object.entries(taxonomy[key]).map(([tag, description]) => {
                          const selected = editNotes.some(n => n.tag === tag)
                          return (
                            <button
                              key={tag}
                              data-log={selected ? 'context.tag.deselect' : 'context.tag.select'}
                              data-log-context={JSON.stringify({ dimension: key, tag })}
                              className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                                selected
                                  ? 'bg-indigo-100 border-indigo-300 text-indigo-700 font-medium'
                                  : 'bg-white border-gray-200 text-gray-500 hover:border-indigo-300 hover:text-indigo-600'
                              }`}
                              onClick={() => toggleTag(tag)}
                              title={description}
                            >
                              {tag.replace(/_/g, ' ')}
                            </button>
                          )
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-gray-400 italic">No taxonomy loaded.</p>
                    )}
                  </div>

                  {/* Explanations for selected tags */}
                  {editNotes.filter(n => n.tag).length > 0 && (
                    <div>
                      <label className="text-xs font-medium text-gray-500 block mb-1.5">
                        Notes <span className="font-normal text-gray-400">(how each tag relates to this community and how it should inform moderation decisions)</span>
                      </label>
                      <div className="space-y-1.5">
                        {editNotes.filter(n => n.tag).map(note => (
                          <div
                            key={note.tag}
                            className="flex gap-1.5 items-start"
                            data-log-context={JSON.stringify({ tag: note.tag })}
                          >
                            <span className="text-xs px-1.5 py-1 rounded bg-indigo-50 text-indigo-600 font-medium flex-shrink-0 mt-px min-w-[80px] text-center">
                              {note.tag.replace(/_/g, ' ')}
                            </span>
                            <input
                              type="text"
                              data-log="context.note.edit-text"
                              className="flex-1 text-sm border border-gray-200 rounded px-2 py-1 focus:outline-none focus:border-indigo-400"
                              value={note.text}
                              onChange={(e) => updateNoteText(note.tag, e.target.value)}
                              placeholder="How does this tag apply to this community?"
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex gap-2 pt-1">
                    <button
                      data-log="context.dimension.save"
                      className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1.5"
                      onClick={() => saveEdit(key)}
                      disabled={isSaving}
                    >
                      {isSaving && <Loader2 size={12} className="animate-spin" />}
                      Save
                    </button>
                    <button
                      data-log="context.dimension.edit-cancel"
                      className="btn-secondary text-xs px-3 py-1.5"
                      onClick={cancelEdit}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {onRegenerate && (
        <button
          data-log="context.regenerate"
          className="btn-secondary flex items-center gap-2 text-sm"
          onClick={onRegenerate}
          disabled={isRegenerating}
        >
          {isRegenerating && <Loader2 size={14} className="animate-spin" />}
          Regenerate
        </button>
      )}
    </>
  )
}
