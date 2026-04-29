import { useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw, Wand2 } from 'lucide-react'
import { suggestRuleText, type SuggestRuleTextResponse, type RuleTextCitation } from '../api/client'

type Props = {
  communityId: string
  title: string
  onApply: (draftText: string, suggestedRelevantContext: { dimension: string; tag: string }[]) => void
}

const DEBOUNCE_MS = 800
const MIN_TITLE_LEN = 5

function CitationChip({ c }: { c: RuleTextCitation }) {
  if (c.kind === 'context') {
    return (
      <span
        className="inline-flex items-center gap-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-700 border border-indigo-200"
        title={c.note_text || ''}
      >
        {c.dimension}: {c.tag}
      </span>
    )
  }
  // peer_rule
  const tip = c.rule_text ? `${c.rule_title}\n\n${c.rule_text}` : (c.rule_title || '')
  // Strip the "r/" prefix if present in the source data — the UI re-adds it.
  const cname = (c.community_name || '').replace(/^r\//i, '')
  return (
    <span
      className="inline-flex items-center gap-1 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700 border border-emerald-200"
      title={tip}
    >
      r/{cname} · {c.rule_title}
      {c.shared_tag ? <span className="text-emerald-500/70"> [{c.shared_tag}]</span> : null}
    </span>
  )
}

export function RuleTextSuggestion({ communityId, title, onApply }: Props) {
  const [data, setData] = useState<SuggestRuleTextResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [bumpToken, setBumpToken] = useState(0)
  const lastTitleRef = useRef<string>('')

  useEffect(() => {
    const trimmed = title.trim()
    if (trimmed.length < MIN_TITLE_LEN) {
      setData(null)
      setError(null)
      return
    }
    // Skip if same as last successful run unless explicit regenerate
    if (trimmed === lastTitleRef.current && data && bumpToken === 0) return

    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await suggestRuleText(communityId, trimmed)
        if (cancelled) return
        setData(res)
        lastTitleRef.current = trimmed
      } catch (e: any) {
        if (cancelled) return
        const detail = e?.response?.data?.detail || e?.message || 'Suggestion failed'
        setError(String(detail))
        setData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, DEBOUNCE_MS)

    return () => {
      cancelled = true
      clearTimeout(handle)
    }
    // bumpToken is in deps to allow regenerate to retrigger even with same title
  }, [communityId, title, bumpToken])

  if (title.trim().length < MIN_TITLE_LEN && !loading) {
    return (
      <div className="text-xs text-gray-400 px-1 py-2">
        Type a title (≥ {MIN_TITLE_LEN} chars) to draft from this community's context.
      </div>
    )
  }

  return (
    <div className="border border-indigo-100 bg-indigo-50/30 rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 text-xs font-medium text-indigo-700">
          <Wand2 size={12} />
          Suggested draft
          {data && (
            <span className="text-indigo-400 font-normal">
              · {data.peer_rules_considered} peer rule(s) · {data.target_has_context ? 'with context' : 'no context'}
            </span>
          )}
        </div>
        <button
          type="button"
          className="text-xs text-indigo-600 hover:text-indigo-800 disabled:text-gray-400 inline-flex items-center gap-1"
          disabled={loading || title.trim().length < MIN_TITLE_LEN}
          onClick={() => setBumpToken(t => t + 1)}
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          Regenerate
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Loader2 size={12} className="animate-spin" /> Drafting…
        </div>
      )}

      {error && !loading && (
        <div className="text-xs text-red-600">{error}</div>
      )}

      {data && !loading && (
        <>
          <div className="bg-white border border-gray-200 rounded p-2 text-sm whitespace-pre-wrap">
            {data.draft_text || <span className="text-gray-400">(empty)</span>}
          </div>
          <ul className="space-y-1.5">
            {data.clauses.map((c, i) => (
              <li key={i} className="text-xs">
                <div className="text-gray-700">{c.text}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {c.citations.map((cit, j) => (
                    <CitationChip key={j} c={cit} />
                  ))}
                </div>
              </li>
            ))}
          </ul>
          <div className="flex justify-end pt-1">
            <button
              type="button"
              className="btn-primary text-xs"
              onClick={() => onApply(data.draft_text, data.suggested_relevant_context)}
            >
              Use this draft
            </button>
          </div>
        </>
      )}
    </div>
  )
}
