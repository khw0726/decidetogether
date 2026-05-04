import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, ChevronLeft, ChevronRight, Zap, Eye, Check } from 'lucide-react'
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
      // Pre-compute the L1s a rule_text accept will supersede so we don't try
      // to accept them afterward (they'd 404 — server requires status=pending).
      const supersededIds = new Set<string>()
      for (const s of ordered) {
        if (s.suggestion_type === 'rule_text') {
          const id = (s.content as Record<string, unknown>).supersedes_logic_suggestion_id as string | undefined
          if (id) supersededIds.add(id)
        }
      }
      for (const s of ordered) {
        if (supersededIds.has(s.id)) continue
        try {
          if (s.suggestion_type === 'checklist') {
            await acceptRecompile(ruleId, s.id)
          } else if (s.suggestion_type === 'context') {
            const affects = (s.content as Record<string, unknown>).affects_rules as
              Array<{ rule_id: string }> | undefined
            await acceptContextSuggestion(s.id, (affects || []).map(r => r.rule_id))
          } else {
            await acceptSuggestion(s.id)
          }
        } catch (err) {
          // A 404/400 here typically means an earlier accept already superseded
          // or applied this one — safe to skip and continue with the rest.
          const status = (err as { response?: { status?: number } })?.response?.status
          if (status === 404 || status === 400) continue
          throw err
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

// ── RuleHealthPanel ─────────────────────────────────────────────────────────────

interface RuleHealthPanelProps {
  ruleId: string
  onHealthSuggestionsChange?: (suggestions: Suggestion[]) => void
  // Fires the primary suggestion of the currently-displayed carousel slide (or null
  // when no slide is shown). The parent uses this to drive the unified live-preview
  // pipeline (rule-text editor diff overlay + ChecklistPreview in the logic pane).
  onActiveSuggestionChange?: (suggestion: Suggestion | null) => void
  // True when the user has unsaved rule-text or context edits. Suggest-fixes is
  // disabled while dirty so suggestion-driven previews don't collide with user drafts.
  userDraftDirty?: boolean
  compact?: boolean
  // Click-through filter on the FP/FN boxes — selecting a box flips this in the
  // parent, which then narrows the Decisions panel to those decision IDs.
  errorTypeFilter?: 'wrongly_flagged' | 'missed' | null
  onErrorTypeFilterChange?: (next: 'wrongly_flagged' | 'missed' | null) => void
}

export default function RuleHealthPanel({
  ruleId,
  onHealthSuggestionsChange,
  onActiveSuggestionChange,
  userDraftDirty = false,
  compact = false,
  errorTypeFilter = null,
  onErrorTypeFilterChange,
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
          <button
            type="button"
            disabled={!onErrorTypeFilterChange || overall.wrongly_flagged_count === 0}
            onClick={() => onErrorTypeFilterChange?.(
              errorTypeFilter === 'wrongly_flagged' ? null : 'wrongly_flagged'
            )}
            className={`text-left bg-red-50 border rounded p-2 transition ${
              errorTypeFilter === 'wrongly_flagged'
                ? 'border-red-500 ring-1 ring-red-400'
                : 'border-red-100 hover:border-red-300'
            } ${(!onErrorTypeFilterChange || overall.wrongly_flagged_count === 0) ? 'cursor-default' : 'cursor-pointer'}`}
            title={overall.wrongly_flagged_count > 0 ? 'Filter the Decisions panel to these cases' : ''}
          >
            <p className="text-[10px] text-red-500 font-semibold uppercase tracking-wider">Wrongly Flagged</p>
            <p className="text-lg font-bold text-red-700 leading-tight">{pct(overall.wrongly_flagged_rate)}</p>
            <p className="text-[10px] text-red-400">{overall.wrongly_flagged_count}/{overall.rule_denominator ?? overall.total_decisions}</p>
          </button>
          <button
            type="button"
            disabled={!onErrorTypeFilterChange || overall.missed_count === 0}
            onClick={() => onErrorTypeFilterChange?.(
              errorTypeFilter === 'missed' ? null : 'missed'
            )}
            className={`text-left bg-amber-50 border rounded p-2 transition ${
              errorTypeFilter === 'missed'
                ? 'border-amber-500 ring-1 ring-amber-400'
                : 'border-amber-100 hover:border-amber-300'
            } ${(!onErrorTypeFilterChange || overall.missed_count === 0) ? 'cursor-default' : 'cursor-pointer'}`}
            title={overall.missed_count > 0 ? 'Filter the Decisions panel to these cases' : ''}
          >
            <p className="text-[10px] text-amber-600 font-semibold uppercase tracking-wider">Missed</p>
            <p className="text-lg font-bold text-amber-700 leading-tight">{pct(overall.missed_rate)}</p>
            <p className="text-[10px] text-amber-500">{overall.missed_count}/{overall.rule_denominator ?? overall.total_decisions}</p>
          </button>
          <div className="bg-gray-50 border border-gray-200 rounded p-2">
            <p className="text-[10px] text-gray-500 font-semibold uppercase tracking-wider">Decisions</p>
            <p className="text-lg font-bold text-gray-700 leading-tight">{overall.total_decisions}</p>
            <p className="text-[10px] text-gray-400">{pct(overall.override_rate)} override</p>
          </div>
        </div>

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
    </div>
  )
}
