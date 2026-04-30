import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2, RefreshCw, Wand2 } from 'lucide-react'
import {
  suggestRuleText,
  type SuggestRuleTextResponse,
  type PeerRuleOption,
} from '../api/client'

type ContextBundle = { dimension: string; tag: string }

type Props = {
  communityId: string
  title: string
  // Tags the user has already picked in the modal — passed to the API so peer rule
  // matching + LLM drafting are scoped to the selected contexts. Empty = use all.
  selectedRelevantContext?: ContextBundle[]
  // text + relevant_context to apply. Optional title override (peer options carry their
  // own title that's often more polished than the user's draft input).
  onApply: (
    text: string,
    relevantContext: ContextBundle[],
    titleOverride?: string,
  ) => void
}

const DEBOUNCE_MS = 800
const MIN_TITLE_LEN = 5

// One source label rendered above each option card.
function SourceLabel({ kind, communityName }: { kind: 'draft' | 'peer'; communityName?: string }) {
  if (kind === 'draft') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700">
        <Wand2 size={11} />
        Drafted from your context
      </span>
    )
  }
  const cname = (communityName || '').replace(/^r\//i, '')
  return (
    <span className="text-[11px] font-semibold text-emerald-700">r/{cname}</span>
  )
}

// Tag chip — indigo for shared with target, muted for peer-only.
function TagChip({ b, shared }: { b: ContextBundle; shared: boolean }) {
  return (
    <span
      className={`text-[10px] px-1.5 py-0.5 rounded border ${
        shared
          ? 'bg-indigo-50 text-indigo-700 border-indigo-200 font-medium'
          : 'bg-gray-50 text-gray-500 border-gray-200'
      }`}
      title={`${b.dimension}: ${b.tag}${shared ? ' · shared with this community' : ' · only in source community'}`}
    >
      {b.tag.replace(/_/g, ' ')}
    </span>
  )
}

type CarouselSlide =
  | {
      kind: 'draft'
      text: string
      tags: { dimension: string; tag: string }[]
      sharedKeys: Set<string>
      footer: React.ReactNode
      onUse: () => void
    }
  | {
      kind: 'peer'
      communityName: string
      text: string
      tags: { dimension: string; tag: string }[]
      sharedKeys: Set<string>
      footer: React.ReactNode
      onUse: () => void
    }

export function RuleTextSuggestion({
  communityId,
  title,
  selectedRelevantContext,
  onApply,
}: Props) {
  const [data, setData] = useState<SuggestRuleTextResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [bumpToken, setBumpToken] = useState(0)
  const [slideIdx, setSlideIdx] = useState(0)
  const lastTitleRef = useRef<string>('')
  const lastSelectionKeyRef = useRef<string>('')

  // Stable string key for the selection so we can put it in the effect deps without
  // re-firing on every render (object identity changes).
  const selectionKey = useMemo(() => {
    if (!selectedRelevantContext || selectedRelevantContext.length === 0) return ''
    return [...selectedRelevantContext]
      .map(t => `${t.dimension}::${t.tag}`)
      .sort()
      .join('|')
  }, [selectedRelevantContext])

  useEffect(() => {
    const trimmed = title.trim()
    if (trimmed.length < MIN_TITLE_LEN) {
      setData(null)
      setError(null)
      return
    }
    if (
      trimmed === lastTitleRef.current
      && selectionKey === lastSelectionKeyRef.current
      && data
      && bumpToken === 0
    ) return

    let cancelled = false
    const handle = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await suggestRuleText(
          communityId,
          trimmed,
          'both',
          selectedRelevantContext,
        )
        if (cancelled) return
        setData(res)
        lastTitleRef.current = trimmed
        lastSelectionKeyRef.current = selectionKey
        // Reset to first slide whenever the input changes so the user sees the new top option.
        setSlideIdx(0)
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
  }, [communityId, title, bumpToken, selectionKey])

  if (title.trim().length < MIN_TITLE_LEN && !loading) {
    return (
      <div className="text-xs text-gray-400 px-1 py-2">
        Type a title (≥ {MIN_TITLE_LEN} chars) to see how peer communities phrase this rule.
      </div>
    )
  }

  return (
    <div className="border border-indigo-100 bg-indigo-50/30 rounded p-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 text-xs font-medium text-indigo-700">
          <Wand2 size={12} />
          Compare options
          {data && (
            <span className="text-indigo-400 font-normal">
              {data.draft_text ? `· LLM draft + ${data.peer_options.length} peer rule${data.peer_options.length === 1 ? '' : 's'}` : `· ${data.peer_options.length} peer rule${data.peer_options.length === 1 ? '' : 's'}`}
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
        <CarouselBody data={data} slideIdx={slideIdx} setSlideIdx={setSlideIdx} onApply={onApply} />
      )}
    </div>
  )
}

function CarouselBody({
  data,
  slideIdx,
  setSlideIdx,
  onApply,
}: {
  data: SuggestRuleTextResponse
  slideIdx: number
  setSlideIdx: (n: number) => void
  onApply: Props['onApply']
}) {
  const slides: CarouselSlide[] = useMemo(() => {
    const out: CarouselSlide[] = []
    if (data.draft_text) {
      out.push({
        kind: 'draft',
        text: data.draft_text,
        tags: data.suggested_relevant_context,
        sharedKeys: new Set(
          data.suggested_relevant_context.map(t => `${t.dimension}::${t.tag}`),
        ),
        footer: (
          <span className="text-[10px] text-gray-500">
            Synthesised from your community context + {data.peer_rules_considered} peer rule(s)
          </span>
        ),
        onUse: () => onApply(data.draft_text, data.suggested_relevant_context),
      })
    }
    for (const p of data.peer_options as PeerRuleOption[]) {
      out.push({
        kind: 'peer',
        communityName: p.community_name,
        text: p.rule_text,
        tags: p.peer_context_tags,
        sharedKeys: new Set(p.shared_tags.map(t => `${t.dimension}::${t.tag}`)),
        footer: (
          <span className="text-[10px] text-gray-500">
            {p.shared_tags.length}/{p.peer_context_tags.length} of this rule's context tags
            {' '}also exist in your community
          </span>
        ),
        onUse: () => onApply(p.rule_text, p.shared_tags, p.rule_title),
      })
    }
    return out
  }, [data, onApply])

  // Clamp slide index when slides change.
  const safeIdx = slides.length === 0 ? 0 : Math.min(slideIdx, slides.length - 1)
  if (slides.length === 0) {
    return <p className="text-xs text-gray-400">No suggestions available.</p>
  }
  const slide = slides[safeIdx]

  const goPrev = () => setSlideIdx((safeIdx - 1 + slides.length) % slides.length)
  const goNext = () => setSlideIdx((safeIdx + 1) % slides.length)

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-gray-500">
        Each option shows a rule and the community context it grew out of. Page through to compare
        framings; pick the one whose context resonates, or refine from there.
      </p>

      <div className="flex items-stretch gap-2">
        <button
          type="button"
          aria-label="Previous option"
          className="flex items-center justify-center w-7 rounded border border-indigo-200 text-indigo-500 hover:bg-indigo-50 disabled:opacity-30 disabled:hover:bg-transparent"
          onClick={goPrev}
          disabled={slides.length <= 1}
        >
          <ChevronLeft size={14} />
        </button>

        <div className="flex-1 min-w-0">
          {slide.kind === 'draft' ? (
            <OptionCard
              header={<SourceLabel kind="draft" />}
              text={slide.text}
              tags={slide.tags}
              sharedKeys={slide.sharedKeys}
              footer={slide.footer}
              onUse={slide.onUse}
            />
          ) : (
            <OptionCard
              header={<SourceLabel kind="peer" communityName={slide.communityName} />}
              text={slide.text}
              tags={slide.tags}
              sharedKeys={slide.sharedKeys}
              footer={slide.footer}
              onUse={slide.onUse}
              useLabel="Use as starting point"
            />
          )}
        </div>

        <button
          type="button"
          aria-label="Next option"
          className="flex items-center justify-center w-7 rounded border border-indigo-200 text-indigo-500 hover:bg-indigo-50 disabled:opacity-30 disabled:hover:bg-transparent"
          onClick={goNext}
          disabled={slides.length <= 1}
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {slides.length > 1 && (
        <div className="flex items-center justify-center gap-1.5 pt-0.5">
          {slides.map((s, i) => (
            <button
              key={i}
              type="button"
              aria-label={`Go to option ${i + 1}`}
              className={`h-1.5 rounded-full transition-all ${
                i === safeIdx
                  ? 'w-4 bg-indigo-500'
                  : 'w-1.5 bg-indigo-200 hover:bg-indigo-300'
              }`}
              onClick={() => setSlideIdx(i)}
              title={s.kind === 'draft' ? 'LLM draft' : `r/${s.communityName.replace(/^r\//i, '')}`}
            />
          ))}
          <span className="text-[10px] text-gray-400 ml-1">
            {safeIdx + 1} / {slides.length}
          </span>
        </div>
      )}
    </div>
  )
}

function OptionCard({
  header,
  text,
  tags,
  sharedKeys,
  footer,
  onUse,
  useLabel = 'Use this',
}: {
  header: React.ReactNode
  text: string
  tags: ContextBundle[]
  sharedKeys: Set<string>
  footer: React.ReactNode
  onUse: () => void
  useLabel?: string
}) {
  return (
    <div className="bg-white border border-gray-200 rounded p-2 space-y-1.5">
      <div className="flex items-center justify-between">
        {header}
        <button
          type="button"
          className="text-[11px] px-2 py-0.5 rounded bg-indigo-600 text-white hover:bg-indigo-700"
          onClick={onUse}
        >
          {useLabel}
        </button>
      </div>
      <div className="text-sm text-gray-800 whitespace-pre-wrap">{text}</div>
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {tags.map((t, j) => (
            <TagChip
              key={`${t.dimension}::${t.tag}::${j}`}
              b={t}
              shared={sharedKeys.has(`${t.dimension}::${t.tag}`)}
            />
          ))}
        </div>
      )}
      {footer}
    </div>
  )
}
