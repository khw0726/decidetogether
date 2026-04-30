import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, AlertTriangle, CheckCircle, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Zap, Eye, Check } from 'lucide-react'
import { showErrorToast } from './Toast'
import {
  getRuleHealth,
  analyzeRuleHealth,
  listSuggestions,
  acceptRecompile,
  acceptSuggestion,
  acceptContextSuggestion,
  dismissSuggestion,
  previewFixes,
  reevaluateDecisions,
  ItemHealthMetrics,
  ExampleSummary,
  ErrorCase,
  Suggestion,
  ImpactPreviewResult,
  ImpactEvaluation,
} from '../api/client'

const ACTION_LABELS: Record<string, string> = {
  tighten_rubric: 'Tighten Rubric',
  adjust_threshold: 'Adjust Threshold',
  promote_to_deterministic: 'Promote to Deterministic',
  split_item: 'Split Item',
  add_item: 'Add Item',
}

const ACTION_COLORS: Record<string, string> = {
  tighten_rubric: 'bg-amber-100 text-amber-800',
  adjust_threshold: 'bg-blue-100 text-blue-800',
  promote_to_deterministic: 'bg-violet-100 text-violet-800',
  split_item: 'bg-orange-100 text-orange-800',
  add_item: 'bg-green-100 text-green-800',
}

const ITEM_TYPE_COLORS: Record<string, string> = {
  deterministic: 'bg-violet-100 text-violet-700',
  structural: 'bg-cyan-100 text-cyan-700',
  subjective: 'bg-orange-100 text-orange-700',
}

const LABEL_COLORS: Record<string, string> = {
  compliant: 'bg-green-100 text-green-800',
  violating: 'bg-red-100 text-red-800',
  borderline: 'bg-amber-100 text-amber-800',
}

const HEALTH_ACTIONS = new Set([
  'tighten_rubric',
  'adjust_threshold',
  'promote_to_deterministic',
  'split_item',
  'add_item',
])

function pct(n: number) {
  return `${(n * 100).toFixed(0)}%`
}

function conf(n: number | null) {
  return n != null ? n.toFixed(2) : '—'
}

function isUnhealthy(item: ItemHealthMetrics) {
  return item.false_positive_rate > 0.15 || item.false_negative_rate > 0.15
}

// ── ExampleRow ──────────────────────────────────────────────────────────────────

function ExampleRow({ ex }: { ex: ExampleSummary }) {
  return (
    <div className="flex items-center gap-2 py-1 border-b border-gray-100 last:border-0">
      <span className={`text-xs font-semibold px-1.5 py-0.5 rounded flex-shrink-0 ${LABEL_COLORS[ex.label] || 'bg-gray-100 text-gray-700'}`}>
        {ex.label}
      </span>
      <span className="text-xs text-gray-600 truncate">{ex.title}</span>
    </div>
  )
}

// ── ErrorCaseList ───────────────────────────────────────────────────────────────

const ERROR_CASE_COLORS: Record<string, { border: string; bg: string; label: string; text: string }> = {
  red:   { border: 'border-red-100', bg: 'bg-red-50', label: 'text-red-600', text: 'text-red-500' },
  amber: { border: 'border-amber-100', bg: 'bg-amber-50', label: 'text-amber-600', text: 'text-amber-500' },
}

function ErrorCaseList({
  label,
  sublabel,
  cases,
  color,
}: {
  label: string
  sublabel: string
  cases: ErrorCase[]
  color: 'red' | 'amber'
}) {
  const c = ERROR_CASE_COLORS[color]
  return (
    <div>
      <p className={`text-xs font-medium mb-0.5 ${c.label}`}>{label}</p>
      <p className={`text-[10px] mb-1 ${c.text}`}>{sublabel}</p>
      <div className={`border ${c.border} rounded ${c.bg} px-2 py-1 max-h-28 overflow-y-auto`}>
        {cases.map(cs => (
          <div key={cs.decision_id} className="py-1 border-b border-gray-100/50 last:border-0">
            <div className="flex items-center gap-2">
              <span className={`text-[10px] font-mono ${c.text} flex-shrink-0`}>
                {(cs.confidence * 100).toFixed(0)}%
              </span>
              <span className="text-xs text-gray-700 truncate">{cs.title}</span>
            </div>
            {cs.moderator_notes && (
              <p className="text-[10px] text-gray-400 italic ml-8 mt-0.5 truncate">
                {cs.moderator_reasoning_category && <span className="not-italic font-medium text-gray-500">{cs.moderator_reasoning_category}: </span>}
                {cs.moderator_notes}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Suggested Fixes Panel ──────────────────────────────────────────────────────

const TYPE_BADGES: Record<string, { label: string; color: string }> = {
  checklist: { label: 'Logic', color: 'bg-blue-100 text-blue-800' },
  rule_text: { label: 'Rule Text', color: 'bg-emerald-100 text-emerald-800' },
  context: { label: 'Context', color: 'bg-purple-100 text-purple-800' },
}

function ImpactRow({ ev }: { ev: ImpactEvaluation }) {
  const icon = ev.fixed ? '✓' : ev.regressed ? '✗' : '—'
  const color = ev.fixed ? 'text-green-600' : ev.regressed ? 'text-red-600' : 'text-gray-400'
  const errorLabel = ev.error_type === 'wrongly_flagged' ? 'FP' : 'FN'

  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className={`text-xs font-bold w-4 text-center ${color}`}>{icon}</span>
      <span className="text-[10px] font-mono text-gray-400 flex-shrink-0">{errorLabel}</span>
      <span className="text-xs text-gray-700 truncate flex-1">{ev.title}</span>
      <span className="text-[10px] text-gray-400 flex-shrink-0">
        {ev.old_verdict} → {ev.new_verdict}
      </span>
    </div>
  )
}

// A "slide" in the carousel: either a single suggestion, OR a paired L1+L3 collapsed
// into one slide (the L3 is the primary; the L1 rides along).
type Slide = {
  key: string
  primary: Suggestion
  pairedL1?: Suggestion
}

function buildSlides(suggestions: Suggestion[]): Slide[] {
  const byId = new Map(suggestions.map(s => [s.id, s]))
  const consumedAsLinkedL1 = new Set<string>()
  const slides: Slide[] = []

  // First pass: rule_text suggestions that supersede a still-pending L1 → paired slide.
  for (const s of suggestions) {
    if (s.suggestion_type !== 'rule_text') continue
    const linkedL1Id =
      ((s.content as Record<string, unknown>).supersedes_logic_suggestion_id as string | undefined)
      ?? ((s.content as Record<string, unknown>).linked_suggestion_id as string | undefined)
    const l1 = linkedL1Id ? byId.get(linkedL1Id) : undefined
    if (l1 && l1.suggestion_type === 'checklist' && l1.status === 'pending') {
      slides.push({ key: s.id, primary: s, pairedL1: l1 })
      consumedAsLinkedL1.add(l1.id)
    } else {
      slides.push({ key: s.id, primary: s })
    }
  }

  // Second pass: everything else that hasn't been consumed.
  for (const s of suggestions) {
    if (s.suggestion_type === 'rule_text') continue  // handled above
    if (consumedAsLinkedL1.has(s.id)) continue
    slides.push({ key: s.id, primary: s })
  }

  return slides
}

function FixCarousel({
  suggestions,
  ruleId,
  itemDescById,
  acceptingAll,
  onActiveSuggestionChange,
}: {
  suggestions: Suggestion[]
  ruleId: string
  itemDescById: Map<string, string>
  acceptingAll: boolean
  onActiveSuggestionChange?: (suggestion: Suggestion | null) => void
}) {
  const slides = useMemo(() => buildSlides(suggestions), [suggestions])
  const [index, setIndex] = useState(0)

  // Clamp index when slides shrink (e.g., after accept/dismiss).
  useEffect(() => {
    if (slides.length === 0) {
      setIndex(0)
    } else if (index >= slides.length) {
      setIndex(slides.length - 1)
    }
  }, [slides.length, index])

  const current = slides.length > 0 ? slides[Math.min(index, slides.length - 1)] : null

  // Lift the active slide's primary suggestion to the parent so it can drive the
  // unified live-preview pipeline (text editor diff + ChecklistPreview in the logic pane).
  useEffect(() => {
    if (!onActiveSuggestionChange) return
    onActiveSuggestionChange(current?.primary ?? null)
    return () => onActiveSuggestionChange?.(null)
  }, [current?.key, onActiveSuggestionChange])

  if (!current) return null
  const handleAdvance = () => {
    // After accept/dismiss the underlying suggestions list shrinks; the effect above
    // clamps the index. Holding `index` steady means we land on what was the next slide.
  }

  return (
    <div className="space-y-2">
      {/* Nav header */}
      <div className="flex items-center justify-between text-xs">
        <button
          className="px-1.5 py-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent"
          disabled={index === 0 || acceptingAll}
          onClick={() => setIndex(i => Math.max(0, i - 1))}
          title="Previous"
        >
          <ChevronLeft size={14} />
        </button>
        <span className="text-gray-500 font-medium">
          {Math.min(index, slides.length - 1) + 1} of {slides.length}
        </span>
        <button
          className="px-1.5 py-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:hover:bg-transparent"
          disabled={index >= slides.length - 1 || acceptingAll}
          onClick={() => setIndex(i => Math.min(slides.length - 1, i + 1))}
          title="Next"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      <FixSlide
        key={current.key}
        slide={current}
        ruleId={ruleId}
        itemDescById={itemDescById}
        acceptingAll={acceptingAll}
        onAfterResolve={handleAdvance}
      />
    </div>
  )
}

function FixSlide({
  slide,
  ruleId,
  itemDescById,
  acceptingAll,
  onAfterResolve,
}: {
  slide: Slide
  ruleId: string
  itemDescById: Map<string, string>
  acceptingAll: boolean
  onAfterResolve: () => void
}) {
  const queryClient = useQueryClient()
  const [applyPhase, setApplyPhase] = useState<null | 'accepting' | 'reevaluating'>(null)
  const { primary, pairedL1 } = slide
  const content = primary.content as Record<string, unknown>
  const action = (content.action as string | undefined)
    ?? ((pairedL1?.content as Record<string, unknown> | undefined)?.action as string | undefined)
  const reasoning = content.reasoning as string | undefined
  const levelReasoning = content.level_reasoning as string | undefined
  const linkedHasPair = !!pairedL1

  // Rule_text payload
  const proposedText =
    (content.proposed_text as string | undefined)
    ?? ((content.proposed_change as Record<string, unknown> | undefined)?.text as string | undefined)

  // Context payload
  const proposedNote = content.proposed_note as { text?: string; tag?: string } | undefined
  const affectsRules = (content.affects_rules as Array<{ rule_id: string; score: number; signals?: string[] }>) || []
  const l2Trigger = content.l2_trigger as string | undefined
  const [optedIn, setOptedIn] = useState<Set<string>>(() => new Set(affectsRules.map(r => r.rule_id)))

  // Logic target description (for the in-slide hint pointing to the Automod Logic panel)
  const logicSource = primary.suggestion_type === 'checklist' ? primary : pairedL1
  const logicTargetDesc = logicSource?.checklist_item_id
    ? itemDescById.get(logicSource.checklist_item_id) || 'Unknown item'
    : 'new item'

  const applyMutation = useMutation({
    mutationFn: async () => {
      setApplyPhase('accepting')
      if (primary.suggestion_type === 'checklist') {
        await acceptRecompile(ruleId, primary.id)
      } else if (primary.suggestion_type === 'context') {
        await acceptContextSuggestion(primary.id, Array.from(optedIn))
      } else {
        await acceptSuggestion(primary.id)
      }
      setApplyPhase('reevaluating')
      await reevaluateDecisions(ruleId)
    },
    onSuccess: () => {
      setApplyPhase(null)
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      onAfterResolve()
    },
    onError: () => setApplyPhase(null),
  })

  const dismissMutation = useMutation({
    mutationFn: async () => {
      // Dismiss both halves of a paired slide so the L1 doesn't linger.
      await dismissSuggestion(primary.id)
      if (pairedL1) await dismissSuggestion(pairedL1.id)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      onAfterResolve()
    },
  })

  const busy = applyMutation.isPending || dismissMutation.isPending || acceptingAll

  const typeBadge = TYPE_BADGES[primary.suggestion_type] ?? { label: primary.suggestion_type, color: 'bg-gray-100 text-gray-700' }

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white space-y-2">
      {/* Badges */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${typeBadge.color}`}>
          {typeBadge.label}
        </span>
        {action && (
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${ACTION_COLORS[action] || 'bg-gray-100 text-gray-700'}`}>
            {ACTION_LABELS[action] || action}
          </span>
        )}
        {linkedHasPair && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700">
            paired
          </span>
        )}
      </div>

      {/* Purpose — prefer the human `reasoning` (≤25-word explanation of why this fix
          addresses the observed errors); fall back to the codified `level_reasoning`
          only if the LLM didn't produce a human one. */}
      {(reasoning || levelReasoning) && (
        <p className="text-xs text-gray-700">{reasoning || levelReasoning}</p>
      )}

      {/* For rule_text and L1 (checklist) slides, the live preview is shown in the
          rule text editor and Automod Logic panel through the unified preview pipeline.
          The slide just shows purpose + paired badge + accept/dismiss. */}
      {primary.suggestion_type === 'rule_text' && proposedText && (
        <p className="text-[11px] text-emerald-700 italic">
          ↖ Diff appears in the rule text editor; resulting logic in the Automod Logic panel.
        </p>
      )}
      {primary.suggestion_type === 'checklist' && (
        <p className="text-[11px] text-blue-700 italic">
          ↘ Logic update appears in the Automod Logic panel
          {logicTargetDesc && <span className="text-gray-400"> · target: {logicTargetDesc}</span>}.
        </p>
      )}

      {/* Context body */}
      {primary.suggestion_type === 'context' && proposedNote && (
        <div className="text-xs bg-purple-50 border border-purple-100 rounded p-2 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            {proposedNote.tag && (
              <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-800">
                {proposedNote.tag}
              </span>
            )}
            {l2Trigger && (
              <span className="text-[10px] text-purple-700 italic">
                {l2Trigger === 'against_existing_context' ? 'against existing context' : 'applies across rules'}
              </span>
            )}
          </div>
          {proposedNote.text && (
            <p className="text-gray-700">{proposedNote.text}</p>
          )}
          {affectsRules.length > 0 && (
            <div className="border-t border-purple-100 pt-1 mt-1">
              <p className="text-[11px] font-medium text-purple-700 mb-0.5">May also apply to:</p>
              <ul className="space-y-0.5">
                {affectsRules.map(r => (
                  <li key={r.rule_id} className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={optedIn.has(r.rule_id)}
                      onChange={() => setOptedIn(prev => {
                        const next = new Set(prev)
                        if (next.has(r.rule_id)) next.delete(r.rule_id)
                        else next.add(r.rule_id)
                        return next
                      })}
                    />
                    <span className="font-mono text-gray-700">{r.rule_id.slice(0, 8)}</span>
                    <span className="text-gray-400">score {r.score.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}


      {/* Actions */}
      <div className="flex justify-end gap-1 pt-1">
        <button
          className="btn-secondary text-xs py-1 px-2"
          onClick={() => dismissMutation.mutate()}
          disabled={busy}
        >
          Dismiss
        </button>
        <button
          className="btn-success text-xs py-1 px-2"
          onClick={() => applyMutation.mutate()}
          disabled={busy}
        >
          {applyMutation.isPending ? <Loader2 size={10} className="animate-spin" /> : null}
          {applyPhase === 'accepting' ? 'Applying...' : applyPhase === 'reevaluating' ? 'Updating...' : 'Accept'}
        </button>
      </div>
    </div>
  )
}

function SuggestedFixesPanel({
  suggestions,
  ruleId,
  items,
  onActiveSuggestionChange,
}: {
  suggestions: Suggestion[]
  ruleId: string
  items: ItemHealthMetrics[]
  onActiveSuggestionChange?: (suggestion: Suggestion | null) => void
}) {
  const queryClient = useQueryClient()
  const [acceptAllPhase, setAcceptAllPhase] = useState<null | 'accepting' | 'reevaluating'>(null)
  const [previewResult, setPreviewResult] = useState<ImpactPreviewResult | null>(null)

  const previewMutation = useMutation({
    mutationFn: () => previewFixes(ruleId),
    onSuccess: (data) => setPreviewResult(data),
  })

  // Build item description lookup
  const itemDescById = new Map<string, string>()
  for (const item of items) {
    itemDescById.set(item.item_id, item.description)
  }

  const handleAcceptAll = async () => {
    setAcceptAllPhase('accepting')
    try {
      // Accept rule_text first so the silent recompile can supersede paired L1s.
      const ordered = [...suggestions].sort((a, b) => {
        const rank = (t: string) => (t === 'rule_text' ? 0 : t === 'context' ? 1 : 2)
        return rank(a.suggestion_type) - rank(b.suggestion_type)
      })
      for (const s of ordered) {
        if (s.suggestion_type === 'checklist') {
          await acceptRecompile(ruleId, s.id)
        } else if (s.suggestion_type === 'context') {
          const affects = (s.content as Record<string, unknown>).affects_rules as
            Array<{ rule_id: string }> | undefined
          await acceptContextSuggestion(s.id, (affects || []).map(r => r.rule_id))
        } else {
          await acceptSuggestion(s.id)
        }
      }
      setAcceptAllPhase('reevaluating')
      await reevaluateDecisions(ruleId)
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : 'Failed to accept fixes')
    } finally {
      setAcceptAllPhase(null)
      setPreviewResult(null)
    }
  }

  const acceptingAll = acceptAllPhase !== null

  return (
    <div className="border border-indigo-200 rounded-lg bg-indigo-50/30 p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Suggested Fixes ({suggestions.length})
        </p>
        <div className="flex gap-1.5">
          <button
            className="btn-secondary text-xs flex items-center gap-1.5 py-1 px-2"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || acceptingAll}
            title="Preview how applying all fixes would affect error cases"
          >
            {previewMutation.isPending
              ? <Loader2 size={10} className="animate-spin" />
              : <Eye size={10} />
            }
            {previewMutation.isPending ? 'Previewing...' : 'Preview'}
          </button>
          <button
            className="btn-success text-xs flex items-center gap-1.5 py-1 px-2"
            onClick={handleAcceptAll}
            disabled={acceptingAll}
          >
            {acceptingAll
              ? <Loader2 size={10} className="animate-spin" />
              : <Check size={10} />
            }
            {acceptAllPhase === 'accepting' ? 'Applying fixes...' : acceptAllPhase === 'reevaluating' ? 'Updating metrics...' : 'Accept all'}
          </button>
        </div>
      </div>

      {/* Carousel */}
      <FixCarousel
        suggestions={suggestions}
        ruleId={ruleId}
        itemDescById={itemDescById}
        acceptingAll={acceptingAll}
        onActiveSuggestionChange={onActiveSuggestionChange}
      />

      {/* Impact preview */}
      {previewResult && (
        <div className="border border-gray-200 rounded-lg p-3 bg-white space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
              Predicted Impact
            </p>
            <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => setPreviewResult(null)}>
              Close
            </button>
          </div>

          {previewResult.evaluations.length === 0 ? (
            <p className="text-xs text-gray-400 italic">No error cases to evaluate.</p>
          ) : (
            <>
              <div className="flex items-center gap-3 text-xs">
                {previewResult.summary.would_fix > 0 && (
                  <span className="text-green-600 font-semibold">
                    {previewResult.summary.would_fix}/{previewResult.summary.total_error_cases} fixed
                  </span>
                )}
                {previewResult.summary.would_remain > 0 && (
                  <span className="text-gray-500">
                    {previewResult.summary.would_remain} unchanged
                  </span>
                )}
                {previewResult.summary.would_regress > 0 && (
                  <span className="text-red-600 font-semibold">
                    {previewResult.summary.would_regress} regressed
                  </span>
                )}
              </div>
              <div className="max-h-48 overflow-y-auto space-y-0.5">
                {previewResult.evaluations.map(ev => (
                  <ImpactRow key={ev.decision_id} ev={ev} />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── ItemHealthCard ──────────────────────────────────────────────────────────────

function ItemHealthCard({
  item,
  depth = 0,
  highlighted = false,
}: {
  item: ItemHealthMetrics
  depth?: number
  highlighted?: boolean
}) {
  const [expanded, setExpanded] = useState(isUnhealthy(item) || highlighted)
  const cardRef = useRef<HTMLDivElement | null>(null)
  const unhealthy = isUnhealthy(item)

  useEffect(() => {
    if (highlighted) {
      setExpanded(true)
      cardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [highlighted])
  const totalExamples = [
    ...item.examples.compliant,
    ...item.examples.violating,
    ...item.examples.borderline,
  ]

  return (
    <div
      ref={cardRef}
      className={`border rounded-lg ${highlighted ? 'border-indigo-400 ring-1 ring-indigo-200' : unhealthy ? 'border-amber-200' : 'border-gray-200'} ${depth > 0 ? 'border-l-2 border-l-gray-300' : ''}`}
      style={depth > 0 ? { marginLeft: depth * 20 } : undefined}
    >
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 p-3 text-left hover:bg-gray-50 rounded-t-lg"
        onClick={() => setExpanded(e => !e)}
      >
        {depth > 0 && <span className="text-gray-300 text-xs flex-shrink-0">↳</span>}
        {unhealthy
          ? <AlertTriangle size={14} className="text-amber-500 flex-shrink-0" />
          : <CheckCircle size={14} className="text-green-500 flex-shrink-0" />
        }
        <span className="text-sm font-medium text-gray-800 flex-1 min-w-0 truncate">
          {item.description}
        </span>
        <span className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${ITEM_TYPE_COLORS[item.item_type] || 'bg-gray-100 text-gray-600'}`}>
          {item.item_type}
        </span>
        {expanded ? <ChevronUp size={14} className="text-gray-400 flex-shrink-0" /> : <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-gray-100">
          {/* Metrics table */}
          <div className="grid grid-cols-3 gap-2 mt-3">
            <div className="bg-red-50 border border-red-100 rounded p-2 text-center">
              <p className="text-xs text-red-500 font-semibold">Wrongly Flagged</p>
              <p className="text-[10px] text-red-400">Triggered, but mod approved</p>
              <p className="text-base font-bold text-red-700">{pct(item.false_positive_rate)}</p>
              <p className="text-xs text-red-400">{item.false_positive_count}/{item.decision_count}</p>
              {item.avg_confidence_errors != null && (
                <p className="text-xs text-red-400 mt-0.5">conf: {conf(item.avg_confidence_errors)}</p>
              )}
            </div>
            <div className="bg-amber-50 border border-amber-100 rounded p-2 text-center">
              <p className="text-xs text-amber-600 font-semibold">Missed</p>
              <p className="text-[10px] text-amber-500">Didn't trigger, but mod removed</p>
              <p className="text-base font-bold text-amber-700">{pct(item.false_negative_rate)}</p>
              <p className="text-xs text-amber-500">{item.false_negative_count}/{item.decision_count}</p>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded p-2 text-center">
              <p className="text-xs text-gray-500 font-semibold">Decisions</p>
              <p className="text-base font-bold text-gray-700">{item.decision_count}</p>
              {item.avg_confidence_correct != null && (
                <p className="text-xs text-gray-400 mt-0.5">correct conf: {conf(item.avg_confidence_correct)}</p>
              )}
            </div>
          </div>

          {/* Wrongly flagged cases */}
          {item.wrongly_flagged.length > 0 && (
            <ErrorCaseList
              label="Wrongly Flagged"
              sublabel="Rule triggered but moderator approved these posts"
              cases={item.wrongly_flagged}
              color="red"
            />
          )}

          {/* Missed violation cases */}
          {item.missed_violations.length > 0 && (
            <ErrorCaseList
              label="Missed Violations"
              sublabel="Rule didn't trigger but moderator removed these posts"
              cases={item.missed_violations}
              color="amber"
            />
          )}

          {/* Linked examples */}
          {totalExamples.length > 0 && (
            <div>
              <p className="text-xs text-gray-400 font-medium mb-1">Linked examples</p>
              <div className="border border-gray-100 rounded bg-white px-2 py-1 max-h-28 overflow-y-auto">
                {totalExamples.slice(0, 8).map(ex => (
                  <ExampleRow key={ex.example_id} ex={ex} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── RuleHealthPanel ─────────────────────────────────────────────────────────────

interface RuleHealthPanelProps {
  ruleId: string
  highlightItemId?: string | null
  onHealthSuggestionsChange?: (suggestions: Suggestion[]) => void
  // Fires the primary suggestion of the currently-displayed carousel slide (or null
  // when no slide is shown). The parent uses this to drive the unified live-preview
  // pipeline (rule-text editor diff overlay + ChecklistPreview in the logic pane).
  onActiveSuggestionChange?: (suggestion: Suggestion | null) => void
  // True when the user has unsaved rule-text or context edits. Suggest-fixes is
  // disabled while dirty so suggestion-driven previews don't collide with user drafts.
  userDraftDirty?: boolean
  compact?: boolean
}

export default function RuleHealthPanel({
  ruleId,
  highlightItemId = null,
  onHealthSuggestionsChange,
  onActiveSuggestionChange,
  userDraftDirty = false,
  compact = false,
}: RuleHealthPanelProps) {
  const queryClient = useQueryClient()

  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['rule-health', ruleId],
    queryFn: () => getRuleHealth(ruleId),
  })

  const { data: suggestions } = useQuery({
    queryKey: ['suggestions', ruleId],
    queryFn: () => listSuggestions(ruleId, 'pending'),
  })

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeRuleHealth(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rule-health', ruleId] })
    },
  })

  // Filter to suggestions emitted by analyze-health (logic / rule_text / context).
  // Backward-compat: older checklist suggestions lack `source`, so also accept the
  // legacy signal (action ∈ HEALTH_ACTIONS).
  const healthSuggestions = useMemo(
    () =>
      (suggestions || []).filter(s => {
        const c = (s.content as Record<string, unknown>) || {}
        if (c.source === 'health_analysis') return true
        const action = c.action as string | undefined
        return s.suggestion_type === 'checklist' && !!action && HEALTH_ACTIONS.has(action)
      }),
    [suggestions],
  )

  useEffect(() => {
    onHealthSuggestionsChange?.(healthSuggestions)
  }, [healthSuggestions, onHealthSuggestionsChange])

  if (healthLoading) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <Loader2 size={16} className="animate-spin text-gray-400" />
      </div>
    )
  }

  if (!health) {
    return (
      <div className="p-4 text-sm text-gray-500">Could not load health data.</div>
    )
  }

  const { overall, items } = health
  const unhealthyCount = items.filter(isUnhealthy).length
  const selectedItem = highlightItemId
    ? items.find(i => i.item_id === highlightItemId) || null
    : null

  return (
    <div className={compact ? 'overflow-y-auto p-3' : 'h-full overflow-y-auto p-4 space-y-4'}>
      {/* Rule-wide health — bordered to distinguish from per-item health */}
      <div className={compact ? 'space-y-2' : 'border border-gray-300 rounded-lg p-3 space-y-2 bg-white'}>
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Rule-wide Health</p>
          <button
            className="btn-primary text-xs flex items-center gap-1.5"
            onClick={() => analyzeMutation.mutate()}
            disabled={analyzeMutation.isPending || userDraftDirty}
            title={
              userDraftDirty
                ? 'Save or discard your current edits first — suggestion previews would collide with the unsaved draft.'
                : 'Generate logic-update suggestions from recent error cases'
            }
          >
            {analyzeMutation.isPending
              ? <Loader2 size={12} className="animate-spin" />
              : <Zap size={12} />
            }
            {analyzeMutation.isPending ? 'Generating…' : 'Suggest Fixes from Errors'}
          </button>
        </div>

        {/* Rule-level FP/FN metrics */}
        <div className="grid grid-cols-3 gap-2">
          <div className="bg-red-50 border border-red-100 rounded p-2">
            <p className="text-[10px] text-red-500 font-semibold uppercase tracking-wider">Wrongly Flagged</p>
            <p className="text-lg font-bold text-red-700 leading-tight">{pct(overall.wrongly_flagged_rate)}</p>
            <p className="text-[10px] text-red-400">{overall.wrongly_flagged_count}/{overall.total_decisions}</p>
          </div>
          <div className="bg-amber-50 border border-amber-100 rounded p-2">
            <p className="text-[10px] text-amber-600 font-semibold uppercase tracking-wider">Missed</p>
            <p className="text-lg font-bold text-amber-700 leading-tight">{pct(overall.missed_rate)}</p>
            <p className="text-[10px] text-amber-500">{overall.missed_count}</p>
          </div>
          <div className="bg-gray-50 border border-gray-200 rounded p-2">
            <p className="text-[10px] text-gray-500 font-semibold uppercase tracking-wider">Decisions</p>
            <p className="text-lg font-bold text-gray-700 leading-tight">{overall.total_decisions}</p>
            <p className="text-[10px] text-gray-400">{pct(overall.override_rate)} override</p>
          </div>
        </div>

        {(unhealthyCount > 0 || overall.total_decisions > 0) && (
          <div className="flex items-center gap-3 text-[11px] text-gray-500">
            {unhealthyCount > 0 && (
              <span className="text-amber-600 font-semibold flex items-center gap-1">
                <AlertTriangle size={11} />
                {unhealthyCount} item{unhealthyCount !== 1 ? 's' : ''} need attention
              </span>
            )}
            {unhealthyCount === 0 && overall.total_decisions > 0 && (
              <span className="text-green-600 font-semibold flex items-center gap-1">
                <CheckCircle size={11} />
                All items healthy
              </span>
            )}
          </div>
        )}

        {overall.total_decisions === 0 && (
          <p className="text-sm text-gray-400 text-center py-2">
            No resolved decisions yet. Evaluate some posts and resolve them to see health metrics.
          </p>
        )}

        {/* Suggested Fixes panel */}
        {healthSuggestions.length > 0 && (
          <SuggestedFixesPanel
            suggestions={healthSuggestions}
            ruleId={ruleId}
            items={items}
            onActiveSuggestionChange={onActiveSuggestionChange}
          />
        )}
      </div>

      {/* Item-specific health — only when a checklist item is selected; hidden in compact mode */}
      {!compact && items.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Selected Item Health</p>
          {selectedItem ? (
            <ItemHealthCard item={selectedItem} depth={0} highlighted={true} />
          ) : (
            <p className="text-xs text-gray-400 italic px-1">
              Select a checklist item on the left to see its health.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
