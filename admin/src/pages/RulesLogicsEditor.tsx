import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  Trash2,
  Wand2,
  X,
} from 'lucide-react'
import {
  CommunityContext,
  CommunityContextNote,
  DecisionPreviewResult,
  ItemHealthMetrics,
  PreviewRecompileResult,
  ReviseRuleTextResponse,
  Rule,
  RuleContextTag,
  RuleHealthSummary,
  Suggestion,
  commitRecompile,
  createRule,
  deactivateRule,
  getChecklist,
  getCommunity,
  getRuleHealth,
  getRulesHealthSummary,
  listRules,
  overrideRuleType,
  previewDecisions,
  previewRecompile,
  suggestRuleTextRevision,
  updateRule,
} from '../api/client'
import ChecklistTree from '../components/ChecklistTree'
import ChecklistPreview from '../components/ChecklistPreview'
import DecisionsPanel from '../components/DecisionsPanel'
import RuleIntentChat from '../components/RuleIntentChat'
import RuleContextPicker from '../components/RuleContextPicker'
import RuleHealthPanel from '../components/RuleHealthPanel'
import { RuleTextSuggestion } from '../components/RuleTextSuggestion'
import TestModal from '../components/TestModal'
import { showErrorToast } from '../components/Toast'
import Tooltip from '../components/Tooltip'

function extractErrorMessage(error: unknown): string {
  if (error && typeof error === 'object') {
    const axiosErr = error as { response?: { data?: { detail?: string } }; message?: string }
    if (axiosErr.response?.data?.detail) return axiosErr.response.data.detail
    if (axiosErr.message) return axiosErr.message
  }
  return 'Something went wrong. Please try again.'
}

function renderTextWithHighlight(text: string, anchor: string | null) {
  if (!anchor) return <>{text}</>
  const escaped = anchor.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  if (parts.length <= 1) return <>{text}</>
  const matchRegex = new RegExp(`^${escaped}$`, 'i')
  return (
    <>
      {parts.map((part, i) =>
        matchRegex.test(part) ? (
          <mark key={i} className="bg-yellow-200 text-yellow-900 rounded">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  )
}

const RULE_TYPE_COLORS: Record<string, string> = {
  actionable: 'badge-green',
  procedural: 'badge-blue',
  meta: 'badge-purple',
  informational: 'badge-gray',
}

function sameTagSet(
  a: RuleContextTag[] | null | undefined,
  b: RuleContextTag[] | null | undefined,
): boolean {
  if (!a && !b) return true
  if (!a || !b) return false
  if (a.length !== b.length) return false
  const mk = (t: RuleContextTag) => `${t.dimension}::${t.tag}`
  const ma = new Map(a.map(t => [mk(t), t.weight ?? 1] as const))
  return b.every(t => ma.has(mk(t)) && ma.get(mk(t)) === (t.weight ?? 1))
}

function sameNotes(
  a: CommunityContextNote[] | null | undefined,
  b: CommunityContextNote[] | null | undefined,
): boolean {
  const na = a ?? []
  const nb = b ?? []
  if (na.length !== nb.length) return false
  for (let i = 0; i < na.length; i++) {
    if ((na[i].text || '') !== (nb[i].text || '')) return false
    if ((na[i].tag || '') !== (nb[i].tag || '')) return false
  }
  return true
}

interface RulesLogicsEditorProps {
  communityId: string
}

export default function RulesLogicsEditor({ communityId }: RulesLogicsEditorProps) {
  const queryClient = useQueryClient()

  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const [hoveredAnchor, setHoveredAnchor] = useState<string | null>(null)
  const [selectedChecklistItemId, setSelectedChecklistItemId] = useState<string | null>(null)

  const [previewResult, setPreviewResult] = useState<PreviewRecompileResult | null>(null)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)

  // Draft per-rule context state — edits buffer here until commit, mirroring rule-text edits.
  const [draftRelevantContext, setDraftRelevantContext] = useState<RuleContextTag[] | null>(null)
  const [draftCustomNotes, setDraftCustomNotes] = useState<CommunityContextNote[]>([])

  // Context-driven rule-text revision: when the moderator edits title or context,
  // a debounced effect calls the LLM for a minimally-revised rule text and surfaces
  // it as an inline diff over the textarea, awaiting Accept/Dismiss.
  const [revisedTextSuggestion, setRevisedTextSuggestion] = useState<ReviseRuleTextResponse | null>(null)
  const [isRevisionLoading, setIsRevisionLoading] = useState(false)

  // Driven by RuleHealthPanel's carousel: the primary suggestion of the currently-shown
  // slide, or null. All suggestion-driven previews flow through this single source.
  const [activeSuggestion, setActiveSuggestion] = useState<Suggestion | null>(null)

  // Convenience derivations for the active suggestion preview.
  const activePreviewText = useMemo<string | null>(() => {
    if (activeSuggestion?.suggestion_type !== 'rule_text') return null
    const c = activeSuggestion.content as Record<string, unknown>
    return (
      (c.proposed_text as string | undefined)
      ?? ((c.proposed_change as Record<string, unknown> | undefined)?.text as string | undefined)
      ?? null
    )
  }, [activeSuggestion])

  // Pre-computed recompile ops baked into the suggestion at analyze-health time.
  // When present, the frontend skips the live previewRecompile call entirely.
  const activePrecomputedOps = useMemo<Record<string, unknown>[] | null>(() => {
    if (activeSuggestion?.suggestion_type !== 'rule_text') return null
    const c = activeSuggestion.content as Record<string, unknown>
    const ops = c.precomputed_recompile_ops
    return Array.isArray(ops) ? (ops as Record<string, unknown>[]) : null
  }, [activeSuggestion])

  const activeContextDraft = useMemo<{ note: { text: string; tag: string } } | null>(() => {
    if (activeSuggestion?.suggestion_type !== 'context') return null
    const note = (activeSuggestion.content as Record<string, unknown>).proposed_note as
      | { text?: string; tag?: string } | undefined
    if (!note?.text) return null
    return { note: { text: note.text, tag: note.tag ?? '' } }
  }, [activeSuggestion])

  const activeLogicOps = useMemo<Record<string, unknown>[] | null>(() => {
    if (activeSuggestion?.suggestion_type !== 'checklist') return null
    const ops = (activeSuggestion.content as Record<string, unknown>).operations
    return Array.isArray(ops) ? (ops as Record<string, unknown>[]) : null
  }, [activeSuggestion])

  const [decisionPreview, setDecisionPreview] = useState<DecisionPreviewResult[] | null>(null)
  const [decisionPreviewLoading, setDecisionPreviewLoading] = useState(false)

  // Curated test-set: localStorage-backed per-rule list of decision_ids.
  const [testSetIds, setTestSetIds] = useState<string[]>([])
  const [useTestSet, setUseTestSet] = useState(false)
  useEffect(() => {
    if (!selectedRuleId) { setTestSetIds([]); setUseTestSet(false); return }
    try {
      const raw = localStorage.getItem(`fluid-test-set:${selectedRuleId}`)
      const parsed = raw ? JSON.parse(raw) : { ids: [], use: false }
      setTestSetIds(Array.isArray(parsed.ids) ? parsed.ids : [])
      setUseTestSet(!!parsed.use)
    } catch {
      setTestSetIds([]); setUseTestSet(false)
    }
  }, [selectedRuleId])
  useEffect(() => {
    if (!selectedRuleId) return
    localStorage.setItem(
      `fluid-test-set:${selectedRuleId}`,
      JSON.stringify({ ids: testSetIds, use: useTestSet }),
    )
  }, [selectedRuleId, testSetIds, useTestSet])
  const toggleTestSetMember = (decisionId: string) => {
    setTestSetIds(prev =>
      prev.includes(decisionId) ? prev.filter(id => id !== decisionId) : [...prev, decisionId],
    )
  }

  const [showTestModal, setShowTestModal] = useState(false)
  const [showNewRule, setShowNewRule] = useState(false)
  const [decisionsExpanded, setDecisionsExpanded] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
    // Poll while any rule is mid-compile so status badges update without a manual refresh.
    refetchInterval: (query) => {
      const items = query.state.data
      return items?.some(r => r.compile_status === 'pending') ? 3000 : false
    },
  })

  const { data: community } = useQuery({
    queryKey: ['community', communityId],
    queryFn: () => getCommunity(communityId),
    enabled: !!communityId,
  })

  const { data: healthSummaries = [] } = useQuery({
    queryKey: ['rules-health-summary', communityId],
    queryFn: () => getRulesHealthSummary(communityId),
    enabled: !!communityId,
  })

  const healthByRule = useMemo(() => {
    const map: Record<string, RuleHealthSummary> = {}
    for (const h of healthSummaries) map[h.rule_id] = h
    return map
  }, [healthSummaries])

  const selectedRule = rules.find(r => r.id === selectedRuleId) || null

  const { data: checklist = [] } = useQuery({
    queryKey: ['checklist', selectedRuleId],
    queryFn: () => getChecklist(selectedRuleId!),
    enabled: !!selectedRuleId,
    refetchInterval: (query) => {
      const items = query.state.data
      const isActionable = selectedRule?.rule_type === 'actionable'
      return isActionable && (!items || items.length === 0) ? 3000 : false
    },
  })

  const { data: ruleHealth } = useQuery({
    queryKey: ['rule-health', selectedRuleId],
    queryFn: () => getRuleHealth(selectedRuleId!),
    enabled: !!selectedRuleId,
  })

  const itemHealthById = useMemo(() => {
    const m: Record<string, ItemHealthMetrics> = {}
    for (const it of ruleHealth?.items || []) m[it.item_id] = it
    return m
  }, [ruleHealth])

  const createRuleMutation = useMutation({
    mutationFn: ({ title, text, relevant_context }: { title: string; text: string; relevant_context: RuleContextTag[] | null }) =>
      createRule(communityId, { title, text, priority: rules.length, relevant_context }),
    onSuccess: rule => {
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
      setSelectedRuleId(rule.id)
      setEditingText(rule.text)
      setEditingTitle(rule.title)
      setShowNewRule(false)
    },
  })

  const deleteRuleMutation = useMutation({
    mutationFn: (ruleId: string) => deactivateRule(ruleId),
    onSuccess: (_data, ruleId) => {
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
      if (selectedRuleId === ruleId) {
        setSelectedRuleId(null)
        setEditingText('')
        setEditingTitle('')
        setPreviewResult(null)
        setActiveSuggestion(null)
      }
    },
    onError: err => showErrorToast(extractErrorMessage(err)),
  })

  const handleDeleteRule = (rule: Rule) => {
    if (!window.confirm(`Delete rule "${rule.title}"? This will deactivate it and hide it from the queue.`)) {
      return
    }
    deleteRuleMutation.mutate(rule.id)
  }

  const handleSelectRule = (rule: Rule) => {
    setSelectedRuleId(rule.id)
    setEditingText(rule.text)
    setEditingTitle(rule.title)
    setPreviewResult(null)
    setHoveredAnchor(null)
    setSelectedChecklistItemId(null)
    setDraftRelevantContext(rule.relevant_context ?? null)
    setDraftCustomNotes(rule.custom_context_notes ?? [])
    setActiveSuggestion(null)
    setRevisedTextSuggestion(null)
    setDecisionPreview(null)
  }

  // Whenever the selected rule's persisted context changes (e.g., after a commit
  // or external update), resync the draft baseline.
  useEffect(() => {
    if (!selectedRule) return
    setDraftRelevantContext(selectedRule.relevant_context ?? null)
    setDraftCustomNotes(selectedRule.custom_context_notes ?? [])
  }, [selectedRule?.id, selectedRule?.relevant_context, selectedRule?.custom_context_notes])

  const textDirty = !!selectedRule && editingText !== selectedRule.text
  const titleDirty = !!selectedRule && editingTitle !== selectedRule.title
  const contextDirty =
    !!selectedRule &&
    (!sameTagSet(draftRelevantContext, selectedRule.relevant_context) ||
      !sameNotes(draftCustomNotes, selectedRule.custom_context_notes))
  const anyDirty = textDirty || titleDirty || contextDirty

  const handleSaveRule = async () => {
    if (!selectedRuleId) return
    setIsSaving(true)
    try {
      await updateRule(selectedRuleId, { text: editingText, title: editingTitle })
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
    } catch (e) {
      showErrorToast(extractErrorMessage(e))
    } finally {
      setIsSaving(false)
    }
  }

  const handleConfirmSave = async () => {
    if (!selectedRuleId || !selectedRule) return
    // Fluid-editor path: commit text + context + ops in one shot.
    if (selectedRule.rule_type === 'actionable' && (textDirty || contextDirty)) {
      setIsSaving(true)
      try {
        await commitRecompile(selectedRuleId, {
          rule_text: editingText,
          title: editingTitle,
          operations: (previewResult?.operations ?? []) as unknown as Record<string, unknown>[],
          context: contextDirty
            ? {
                relevant_context: draftRelevantContext,
                custom_context_notes: draftCustomNotes,
              }
            : undefined,
        })
        queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
        queryClient.invalidateQueries({ queryKey: ['checklist', selectedRuleId] })
      } catch (e) {
        showErrorToast(extractErrorMessage(e))
      } finally {
        setIsSaving(false)
      }
    } else {
      await handleSaveRule()
      queryClient.invalidateQueries({ queryKey: ['checklist', selectedRuleId] })
    }
    setPreviewResult(null)
    setDecisionPreview(null)
  }

  const handleDiscardEdit = () => {
    if (selectedRule) {
      setEditingText(selectedRule.text)
      setEditingTitle(selectedRule.title)
      setDraftRelevantContext(selectedRule.relevant_context ?? null)
      setDraftCustomNotes(selectedRule.custom_context_notes ?? [])
    }
    setPreviewResult(null)
    setRevisedTextSuggestion(null)
    setDecisionPreview(null)
  }

  // Fluid editor: debounce text drafts (or an active suggestion's proposed text/context)
  // and auto-recompile the checklist. Context-only edits no longer trigger a recompile —
  // they flow through the rule-text revision suggestion first; the moderator must accept
  // a rule-text update (which sets textDirty) before the logic preview fires.
  useEffect(() => {
    if (!selectedRule || selectedRule.rule_type !== 'actionable') return
    const hasUserDraft = textDirty
    const hasSuggestionDraft = !!activePreviewText || !!activeContextDraft
    if (!hasUserDraft && !hasSuggestionDraft) {
      // No drafts → clear any stale preview.
      setPreviewResult(null)
      setIsPreviewLoading(false)
      return
    }

    // Short-circuit: if the active suggestion already carries pre-computed ops (baked
    // in at analyze-health time), use them directly and skip the LLM round-trip.
    if (!hasUserDraft && activePrecomputedOps && activePrecomputedOps.length > 0) {
      setPreviewResult({
        operations: activePrecomputedOps as PreviewRecompileResult['operations'],
        adjustment_summary: null,
        example_verdicts: [],
        summary: {
          keep: activePrecomputedOps.filter(o => (o as Record<string, unknown>).op === 'keep').length,
          update: activePrecomputedOps.filter(o => (o as Record<string, unknown>).op === 'update').length,
          delete: activePrecomputedOps.filter(o => (o as Record<string, unknown>).op === 'delete').length,
          add: activePrecomputedOps.filter(o => (o as Record<string, unknown>).op === 'add').length,
          examples_may_change: 0,
        },
      } as PreviewRecompileResult)
      setIsPreviewLoading(false)
      return
    }

    const controller = new AbortController()
    const ruleId = selectedRule.id

    // Resolve effective text: user draft > suggestion preview > saved.
    const text = textDirty ? editingText : (activePreviewText ?? selectedRule.text)

    // Resolve effective context payload.
    let ctxPayload: { relevant_context: RuleContextTag[] | null; custom_context_notes: CommunityContextNote[] } | undefined
    if (contextDirty) {
      ctxPayload = {
        relevant_context: draftRelevantContext,
        custom_context_notes: draftCustomNotes,
      }
    } else if (activeContextDraft) {
      ctxPayload = {
        relevant_context: selectedRule.relevant_context ?? null,
        custom_context_notes: [
          ...(selectedRule.custom_context_notes ?? []),
          activeContextDraft.note,
        ],
      }
    }

    const handle = window.setTimeout(async () => {
      setIsPreviewLoading(true)
      try {
        const result = await previewRecompile(ruleId, text, ctxPayload)
        if (!controller.signal.aborted) setPreviewResult(result)
      } catch (e) {
        if (!controller.signal.aborted) showErrorToast(extractErrorMessage(e))
      } finally {
        setIsPreviewLoading(false)
      }
    }, 600)
    return () => {
      controller.abort()
      window.clearTimeout(handle)
    }
  }, [editingText, draftRelevantContext, draftCustomNotes, selectedRule, textDirty, contextDirty, activePreviewText, activeContextDraft, activePrecomputedOps])

  // Context- or title-driven rule-text revision: debounce and call the LLM for a
  // minimally-revised rule text. Shown as an inline diff awaiting Accept/Dismiss.
  // Skipped while the moderator is hand-editing rule text (textDirty) — the
  // suggestion would clobber their in-progress edits.
  useEffect(() => {
    if (!selectedRule || selectedRule.rule_type !== 'actionable') {
      setRevisedTextSuggestion(null)
      setIsRevisionLoading(false)
      return
    }
    if (textDirty) {
      // Don't fire during manual rule-text edits; clear any stale suggestion.
      setRevisedTextSuggestion(null)
      setIsRevisionLoading(false)
      return
    }
    if (!titleDirty && !contextDirty) {
      setRevisedTextSuggestion(null)
      setIsRevisionLoading(false)
      return
    }
    const ruleId = selectedRule.id
    const baselineText = selectedRule.text
    const titleForRequest = editingTitle.trim() || selectedRule.title
    const controller = new AbortController()
    const handle = window.setTimeout(async () => {
      setIsRevisionLoading(true)
      try {
        const res = await suggestRuleTextRevision(ruleId, {
          title: titleForRequest,
          current_rule_text: baselineText,
          relevant_context: draftRelevantContext,
          custom_context_notes: draftCustomNotes,
        })
        if (controller.signal.aborted) return
        // Drop verbatim no-ops so the diff overlay only appears for real changes.
        if ((res.revised_text || '').trim() === baselineText.trim()) {
          setRevisedTextSuggestion(null)
        } else {
          setRevisedTextSuggestion(res)
        }
      } catch (e) {
        if (!controller.signal.aborted) showErrorToast(extractErrorMessage(e))
      } finally {
        if (!controller.signal.aborted) setIsRevisionLoading(false)
      }
    }, 800)
    return () => {
      controller.abort()
      window.clearTimeout(handle)
    }
  }, [editingTitle, draftRelevantContext, draftCustomNotes, selectedRule, titleDirty, contextDirty, textDirty])

  const handleAcceptRevision = () => {
    if (!revisedTextSuggestion) return
    setEditingText(revisedTextSuggestion.revised_text)
    setRevisedTextSuggestion(null)
  }

  const handleDismissRevision = () => {
    setRevisedTextSuggestion(null)
  }

  // Trigger decisions preview when rule-text preview becomes active.
  useEffect(() => {
    if (!selectedRuleId || !previewResult) {
      return
    }
    let cancelled = false
    setDecisionPreviewLoading(true)
    const decisionIds = useTestSet && testSetIds.length > 0 ? testSetIds : undefined
    previewDecisions(selectedRuleId, {
      rule_text: editingText,
      limit: 50,
      decision_ids: decisionIds,
    })
      .then(data => {
        if (!cancelled) setDecisionPreview(data.results)
      })
      .catch(e => {
        if (!cancelled) showErrorToast(extractErrorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setDecisionPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [previewResult, selectedRuleId, useTestSet, testSetIds]) // eslint-disable-line react-hooks/exhaustive-deps

  // Trigger decisions preview when an active L1 (checklist) suggestion is on the
  // carousel — its ops are evaluated against the existing decisions.
  useEffect(() => {
    if (!selectedRuleId || !activeLogicOps || activeLogicOps.length === 0) {
      return
    }
    let cancelled = false
    setDecisionPreviewLoading(true)
    previewDecisions(selectedRuleId, { checklist_override_operations: activeLogicOps, limit: 50 })
      .then(data => {
        if (!cancelled) setDecisionPreview(data.results)
      })
      .catch(e => {
        if (!cancelled) showErrorToast(extractErrorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setDecisionPreviewLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeLogicOps, selectedRuleId])

  // Clear decision preview when nothing is active.
  useEffect(() => {
    if (!previewResult && !activeLogicOps) {
      setDecisionPreview(null)
      setDecisionPreviewLoading(false)
    }
  }, [previewResult, activeLogicOps])

  // Auto-expand the decisions panel when a logic preview kicks off so the
  // moderator can watch the impact on past decisions without manually opening it.
  useEffect(() => {
    if (isPreviewLoading || previewResult || (activeLogicOps && activeLogicOps.length > 0)) {
      setDecisionsExpanded(true)
    }
  }, [isPreviewLoading, previewResult, activeLogicOps])

  const isAnyPreviewActive = !!previewResult || (!!activeLogicOps && activeLogicOps.length > 0)

  if (!communityId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <p>Select a community to manage rules.</p>
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Rules sidebar */}
      <div className="w-64 flex-shrink-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
          <h2 className="font-semibold text-sm">Rules</h2>
          <button className="btn-primary text-xs py-1" onClick={() => setShowNewRule(true)}>
            <Plus size={12} />
            New
          </button>
        </div>

        <div className="flex-1 overflow-auto py-2">
          {rulesLoading && (
            <div className="text-xs text-gray-400 text-center py-4">Loading…</div>
          )}
          {rules
            .filter(r => r.is_active)
            .sort((a, b) => a.priority - b.priority)
            .map(rule => {
              const h = healthByRule[rule.id]
              const errorPct = h && h.decision_count > 0
                ? Math.round(h.error_rate * 100)
                : null
              return (
                <div
                  key={rule.id}
                  className={`px-3 py-2 cursor-pointer hover:bg-gray-50 transition-colors border-l-2 ${selectedRuleId === rule.id ? 'border-indigo-500 bg-indigo-50' : 'border-transparent'}`}
                  onClick={() => handleSelectRule(rule)}
                >
                  <p className="text-sm font-medium leading-tight truncate">{rule.title}</p>
                  <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                    <span className={`badge ${RULE_TYPE_COLORS[rule.rule_type] || 'badge-gray'}`}>
                      {rule.rule_type}
                    </span>
                    {rule.applies_to && (
                      <span className="badge badge-gray">{rule.applies_to}</span>
                    )}
                    {rule.compile_status === 'pending' && (
                      <span
                        className="badge bg-blue-50 text-blue-700 border border-blue-200"
                        title="Compiling…"
                      >
                        compiling…
                      </span>
                    )}
                    {rule.compile_status === 'failed' && (
                      <span
                        className="badge bg-red-50 text-red-700 border border-red-200"
                        title={rule.compile_error || 'Compilation failed'}
                      >
                        compile failed
                      </span>
                    )}
                    {errorPct !== null && (
                      <span
                        className={`text-[10px] ml-auto font-semibold ${errorPct >= 30 ? 'text-red-600' : errorPct >= 15 ? 'text-amber-600' : 'text-gray-500'}`}
                        title={`${h?.error_count ?? 0} errors out of ${h?.decision_count ?? 0} decisions`}
                      >
                        {errorPct}%
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          {rules.filter(r => r.is_active).length === 0 && !rulesLoading && (
            <div className="text-xs text-gray-400 text-center py-8">
              No rules yet. Create one!
            </div>
          )}
        </div>
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {selectedRule ? (
          <>
            {/* Header */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
              <input
                className="flex-1 font-semibold text-base bg-transparent border-b border-transparent hover:border-gray-300 focus:border-indigo-500 focus:outline-none px-0.5"
                value={editingTitle}
                onChange={e => setEditingTitle(e.target.value)}
                onBlur={() => {
                  // For actionable rules the title is buffered alongside text/context
                  // so the moderator can review the LLM-revised rule text the title
                  // change implies before committing. Save-on-blur only for non-actionable.
                  if (
                    selectedRule
                    && editingTitle !== selectedRule.title
                    && selectedRule.rule_type !== 'actionable'
                  ) {
                    handleSaveRule()
                  }
                }}
              />
              <button
                className={`btn-secondary text-xs ${chatOpen ? 'bg-indigo-50 text-indigo-700 border-indigo-300' : ''}`}
                onClick={() => setChatOpen(v => !v)}
                title="Casually describe how this rule should be interpreted; get a proposed rule-text edit"
              >
                <MessageSquare size={12} />
                Moderator chat
              </button>
              <button
                className="btn-secondary text-xs"
                onClick={() => setShowTestModal(true)}
                title="Test a hypothetical post against the automod"
              >
                <Play size={12} />
                Test Rule with a Post
              </button>
              <button
                className="btn-secondary text-xs text-red-600 hover:bg-red-50"
                onClick={() => selectedRule && handleDeleteRule(selectedRule)}
                disabled={deleteRuleMutation.isPending}
                title="Delete this rule"
              >
                <Trash2 size={12} />
                Delete
              </button>
            </div>

            {/* 2-column detail area: Rule Text | (Rule Health + Automod Logic stacked) */}
            <div className="flex min-h-0 border-b border-gray-200" style={{ flex: '3 3 0%' }}>
              {/* Rule Text panel */}
              <div className="flex-1 min-w-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
                <PanelHeader title="Rule Text" />

                <div className="flex-1 flex flex-col overflow-hidden p-4 gap-2 min-h-0">
                  {/* Context comes first — title + context are now the primary inputs that
                      drive the rule text suggestion below. */}
                  {selectedRule.rule_type === 'actionable' && (
                    <div className="max-h-48 overflow-auto flex-shrink-0">
                      <RuleContextPicker
                        key={selectedRule.id}
                        rule={{
                          ...selectedRule,
                          relevant_context: draftRelevantContext,
                          custom_context_notes: draftCustomNotes,
                        }}
                        community_context={community?.community_context ?? null}
                        readOnly={false}
                        onChange={({ relevant_context, custom_context_notes }) => {
                          setDraftRelevantContext(relevant_context)
                          setDraftCustomNotes(custom_context_notes)
                        }}
                      />
                    </div>
                  )}

                  {selectedRule.rule_type === 'actionable' && editingText !== selectedRule.text && (
                    <div className="flex-shrink-0 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-800 flex items-start gap-2">
                      <AlertCircle size={13} className="mt-0.5 flex-shrink-0 text-amber-500" />
                      <span>
                        Saving will recompile this rule's logic and automatically{' '}
                        <strong>re-evaluate every pending item in the moderation queue</strong>{' '}
                        against the new logic. Existing verdicts in the queue will update in place.
                      </span>
                    </div>
                  )}

                  {selectedRule.rule_type === 'actionable' && isRevisionLoading && !revisedTextSuggestion && (
                    <div className="flex-shrink-0 text-[11px] text-indigo-600 flex items-center gap-1.5">
                      <Loader2 size={11} className="animate-spin" />
                      Drafting suggested rule-text edits from context…
                    </div>
                  )}
                  {revisedTextSuggestion && (
                    <div className="flex-shrink-0 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2 text-xs text-emerald-900 flex items-start gap-2">
                      <Wand2 size={13} className="mt-0.5 flex-shrink-0 text-emerald-600" />
                      <div className="flex-1 min-w-0">
                        <p className="font-semibold mb-0.5">Suggested rule-text update</p>
                        <p className="text-emerald-800/90">
                          {revisedTextSuggestion.change_summary || 'Revised to align with the updated title/context.'}
                        </p>
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        <button
                          className="text-[11px] px-2 py-0.5 rounded bg-emerald-600 text-white hover:bg-emerald-700"
                          onClick={handleAcceptRevision}
                        >
                          Accept
                        </button>
                        <button
                          className="text-[11px] px-2 py-0.5 rounded border border-emerald-300 text-emerald-700 hover:bg-emerald-100"
                          onClick={handleDismissRevision}
                        >
                          Dismiss
                        </button>
                      </div>
                    </div>
                  )}

                  <HighlightedTextarea
                    value={editingText}
                    onChange={setEditingText}
                    anchor={hoveredAnchor}
                    previewText={activePreviewText ?? revisedTextSuggestion?.revised_text ?? null}
                    placeholder="Rule text..."
                  />
                  {selectedRule.rule_type === 'actionable' && isPreviewLoading && (
                    <div className="flex-shrink-0 text-[11px] text-indigo-600 flex items-center gap-1.5">
                      <Loader2 size={11} className="animate-spin" />
                      Recompiling…
                    </div>
                  )}
                  {anyDirty && (
                    <div className="flex gap-2 justify-end flex-shrink-0">
                      <button className="btn-secondary text-xs" onClick={handleDiscardEdit}>
                        <X size={12} /> Discard edits
                      </button>
                      <button
                        className="btn-primary text-xs"
                        onClick={handleConfirmSave}
                        disabled={isSaving || isPreviewLoading}
                      >
                        {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                        {isSaving ? 'Applying…' : 'Confirm & Save'}
                      </button>
                    </div>
                  )}

                  {/* Type + applies-to controls (context picker moved above textarea) */}
                  <div className="border-t border-gray-100 pt-2 flex flex-col gap-2 flex-shrink-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-gray-500">Type:</span>
                      {['actionable', 'procedural', 'meta', 'informational'].map(type => {
                        const active = selectedRule.rule_type === type
                        return (
                          <button
                            key={type}
                            className={`text-xs px-2 py-0.5 rounded border ${
                              active
                                ? 'bg-indigo-600 text-white border-indigo-600 transition-colors'
                                : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50 transition-colors'
                            }`}
                            onClick={async () => {
                              await overrideRuleType(selectedRule.id, type)
                              queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                            }}
                          >
                            {type}
                          </button>
                        )
                      })}
                    </div>

                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-gray-500">Applies to:</span>
                      {(['posts', 'comments', 'both'] as const).map(target => {
                        const active = (selectedRule.applies_to || 'both') === target
                        return (
                          <button
                            key={target}
                            className={`text-xs px-2 py-0.5 rounded border ${
                              active
                                ? 'bg-emerald-600 text-white border-emerald-600 transition-colors'
                                : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50 transition-colors'
                            }`}
                            onClick={async () => {
                              await updateRule(selectedRule.id, { applies_to: target })
                              queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                            }}
                          >
                            {target}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                </div>
              </div>

              {/* Right column: Rule-wide Health stacked above Automod Logic */}
              <div className="flex-1 min-w-0 flex flex-col bg-white overflow-hidden">
                {/* Rule-wide Health (compact) */}
                <div className="flex-shrink-0 border-b border-gray-200 bg-gray-50/50">
                  <RuleHealthPanel
                    ruleId={selectedRuleId!}
                    highlightItemId={selectedChecklistItemId}
                    onActiveSuggestionChange={setActiveSuggestion}
                    userDraftDirty={textDirty || contextDirty}
                    compact
                  />
                </div>

                {/* Automod Logic */}
                <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                <PanelHeader title="Automoderator Logic">
                  {isAnyPreviewActive && (
                    <>
                      <span className="text-xs font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded px-1.5 py-0.5">
                        {previewResult
                          ? (textDirty && contextDirty
                              ? 'Rule-Text + Context Preview'
                              : contextDirty
                                ? 'Context Preview'
                                : 'Rule-Text Preview')
                          : 'Error-Pattern Preview'}
                      </span>
                      <button
                        className="btn-secondary text-xs py-0.5"
                        title="Exit preview and return to the current logic view"
                        onClick={() => {
                          handleDiscardEdit()
                          setActiveSuggestion(null)
                        }}
                      >
                        <X size={11} /> Exit preview
                      </button>
                    </>
                  )}
                </PanelHeader>

                <div className="relative flex-1 overflow-auto p-3">
                  {/* Loader overlay while a recompile preview is in flight. Sits above
                      the previous render so the moderator sees progress without losing
                      visual context. */}
                  {isPreviewLoading && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70 backdrop-blur-[1px]">
                      <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-indigo-200 shadow-sm text-xs text-indigo-700">
                        <Loader2 size={12} className="animate-spin" />
                        Compiling preview…
                      </div>
                    </div>
                  )}
                  {/* Inline placeholder when an active suggestion expects a preview but
                      none has arrived yet (first render before the debounce fires). */}
                  {!isPreviewLoading && (activePreviewText || activeContextDraft) && !previewResult && (
                    <div className="flex items-center gap-2 text-xs text-indigo-500 italic mb-2">
                      <Loader2 size={11} className="animate-spin" />
                      Preparing preview…
                    </div>
                  )}
                  {previewResult ? (
                    <div className="space-y-2">
                      {previewResult.adjustment_summary && (
                        <div className="bg-teal-50 border border-teal-200 rounded-lg px-3 py-2 text-xs text-teal-800">
                          <p>{previewResult.adjustment_summary}</p>
                        </div>
                      )}
                      <ChecklistPreview operations={previewResult.operations} existingItems={checklist} />
                    </div>
                  ) : activeLogicOps && activeLogicOps.length > 0 ? (
                    <ChecklistPreview operations={activeLogicOps as PreviewRecompileResult['operations']} existingItems={checklist} />
                  ) : selectedRule.rule_type === 'actionable' ? (
                    checklist.length === 0 ? (
                      <div className="flex items-center gap-2 text-xs text-gray-400 italic p-1">
                        <Loader2 size={12} className="animate-spin text-indigo-400" />
                        Compiling checklist… this may take a moment.
                      </div>
                    ) : (
                      <ChecklistTree
                        items={checklist}
                        ruleId={selectedRuleId!}
                        rule={selectedRule}
                        onAnchorHover={setHoveredAnchor}
                        selectedItemId={selectedChecklistItemId}
                        onItemSelect={setSelectedChecklistItemId}
                        highlightedItemId={null}
                        itemHealthById={itemHealthById}
                      />
                    )
                  ) : (
                    <div className="text-xs text-gray-400 italic">Only actionable rules have checklists.</div>
                  )}
                </div>

                {selectedRule && (selectedRule.override_count ?? 0) >= 3 && !isAnyPreviewActive && (
                  <div className="mx-3 mb-2 flex-shrink-0 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-800 flex items-start gap-2">
                    <AlertCircle size={13} className="mt-0.5 flex-shrink-0 text-amber-500" />
                    <span>
                      <strong>{selectedRule.override_count} overrides</strong> suggest this checklist may need updating. Try the <em>Suggest Fixes from Errors</em> button →
                    </span>
                  </div>
                )}
                </div>
              </div>
            </div>

            {/* Decisions panel — collapsible drawer at bottom (default collapsed) */}
            {decisionsExpanded ? (
              <div className="flex flex-col overflow-hidden bg-white min-h-0" style={{ flex: '2 2 0%' }}>
                <PanelHeader title="Decisions">
                  {selectedChecklistItemId && (
                    <button
                      className="btn-secondary text-xs py-0.5"
                      onClick={() => setSelectedChecklistItemId(null)}
                      title="Clear checklist item filter"
                    >
                      <X size={11} /> Clear filter
                    </button>
                  )}
                  <button
                    className="btn-secondary text-xs py-0.5"
                    onClick={() => setDecisionsExpanded(false)}
                    title="Collapse decisions panel"
                  >
                    <ChevronDown size={11} /> Collapse
                  </button>
                </PanelHeader>
                <DecisionsPanel
                  communityId={communityId}
                  ruleId={selectedRuleId}
                  checklistItemId={selectedChecklistItemId}
                  previewResults={decisionPreview}
                  previewLoading={decisionPreviewLoading}
                  testSetIds={testSetIds}
                  useTestSet={useTestSet}
                  onToggleUseTestSet={setUseTestSet}
                  onToggleTestSetMember={toggleTestSetMember}
                />
              </div>
            ) : (
              <button
                className="flex-shrink-0 px-3 py-2 border-t border-gray-200 bg-white hover:bg-gray-50 flex items-center justify-between transition-colors"
                onClick={() => setDecisionsExpanded(true)}
                title="Expand decisions panel"
              >
                <div className="flex items-center gap-1.5">
                  <ChevronUp size={13} className="text-gray-400" />
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Decisions</span>
                  {selectedChecklistItemId && (
                    <span className="text-[10px] text-indigo-600 bg-indigo-50 border border-indigo-200 rounded px-1.5 py-0.5 normal-case">
                      filtered by checklist item
                    </span>
                  )}
                </div>
                <span className="text-[10px] text-gray-400">click to expand</span>
              </button>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <BookOpen size={40} className="mb-3 opacity-30" />
            <p className="text-sm">Select a rule to edit</p>
          </div>
        )}
      </div>

      {/* Moderator chat drawer (right edge) */}
      {chatOpen && selectedRuleId && (
        <div className="w-96 flex-shrink-0 border-l border-gray-200 bg-white flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 flex-shrink-0">
            <div className="flex items-center gap-1.5">
              <MessageSquare size={13} className="text-indigo-500" />
              <span className="text-xs font-semibold text-gray-700">
                {selectedRule?.title || 'Rule'} — chat
              </span>
            </div>
            <button
              type="button"
              className="text-gray-400 hover:text-gray-700"
              onClick={() => setChatOpen(false)}
              title="Close chat"
            >
              <X size={14} />
            </button>
          </div>
          <div className="flex-1 min-h-0 flex flex-col">
            <RuleIntentChat ruleId={selectedRuleId} />
          </div>
        </div>
      )}

      {/* Test modal */}
      {showTestModal && (
        <TestModal
          communityId={communityId}
          checklist={checklist}
          onClose={() => setShowTestModal(false)}
        />
      )}

      {/* New rule modal */}
      {showNewRule && (
        <NewRuleModal
          communityId={communityId}
          communityContext={community?.community_context ?? null}
          onClose={() => setShowNewRule(false)}
          onCreate={(title, text, relevant_context) =>
            createRuleMutation.mutate({ title, text, relevant_context })
          }
          loading={createRuleMutation.isPending}
        />
      )}
    </div>
  )
}

function HighlightedTextarea({
  value,
  onChange,
  anchor,
  previewText,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  anchor: string | null
  previewText?: string | null
  placeholder?: string
}) {
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const syncScroll = () => {
    if (taRef.current && overlayRef.current) {
      overlayRef.current.scrollTop = taRef.current.scrollTop
      overlayRef.current.scrollLeft = taRef.current.scrollLeft
    }
  }
  // The overlay and textarea share font/padding/border so positions line up.
  const shared =
    'absolute inset-0 p-3 text-sm font-mono leading-relaxed whitespace-pre-wrap break-words overflow-auto rounded-lg border'

  const previewing = !!previewText && previewText !== value
  return (
    <div className="flex-1 relative min-h-0">
      <div
        ref={overlayRef}
        aria-hidden
        className={`${shared} border-transparent text-gray-700 pointer-events-none`}
      >
        {previewing
          ? renderRuleTextDiff(value, previewText!)
          : renderTextWithHighlight(value, anchor)}
        {/* trailing space ensures the last line keeps height */}
        {'​'}
      </div>
      <textarea
        ref={taRef}
        className={`${shared} ${previewing ? 'border-emerald-400' : 'border-indigo-300'} bg-transparent text-transparent caret-gray-800 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500 ${previewing ? 'pointer-events-none' : ''}`}
        style={{ WebkitTextFillColor: 'transparent' }}
        value={value}
        onChange={e => onChange(e.target.value)}
        onScroll={syncScroll}
        placeholder={placeholder}
        spellCheck={false}
        readOnly={previewing}
      />
      {previewing && (
        <div className="absolute top-1.5 right-1.5 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 pointer-events-none">
          PREVIEW
        </div>
      )}
    </div>
  )
}

// Word-level diff renderer for the rule-text overlay when previewing a suggestion.
function renderRuleTextDiff(oldText: string, newText: string): React.ReactNode {
  const tokenize = (s: string) => s.split(/(\s+)/)
  const a = tokenize(oldText)
  const b = tokenize(newText)
  const m = a.length, n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: React.ReactNode[] = []
  let i = 0, j = 0, k = 0
  while (i < m || j < n) {
    if (i < m && j < n && a[i] === b[j]) {
      out.push(<span key={k++}>{a[i]}</span>); i++; j++
    } else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) {
      out.push(<span key={k++} className="bg-green-100 text-green-800 font-medium">{b[j]}</span>); j++
    } else {
      out.push(<span key={k++} className="bg-red-100 text-red-700 line-through decoration-red-400">{a[i]}</span>); i++
    }
  }
  return <>{out}</>
}

function PanelHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="px-3 py-2 border-b border-gray-100 flex-shrink-0 flex items-center justify-between">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{title}</h3>
      <div className="flex items-center gap-1.5">
        {children}
      </div>
    </div>
  )
}

function NewRuleModal({
  communityId,
  communityContext,
  onClose,
  onCreate,
  loading,
}: {
  communityId: string
  communityContext: CommunityContext | null
  onClose: () => void
  onCreate: (title: string, text: string, relevantContext: RuleContextTag[] | null) => void
  loading: boolean
}) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [tagWeights, setTagWeights] = useState<Record<string, number>>({})

  const allBundles = useMemo(() => {
    const out: { dim: keyof CommunityContext; tag: string; text: string }[] = []
    if (!communityContext) return out
    for (const dim of ['purpose', 'participants', 'stakes', 'tone'] as const) {
      const d = communityContext[dim]
      if (!d) continue
      for (const raw of d.notes || []) {
        const note = typeof raw === 'string' ? { text: raw, tag: '' } : raw
        if (!note.tag) continue
        out.push({ dim, tag: note.tag, text: note.text })
      }
    }
    return out
  }, [communityContext])

  const keyOf = (dim: string, tag: string) => `${dim}::${tag}`
  const snapWeight = (w: number) => {
    const r = Math.round(w * 2) / 2
    return r < 0 ? 0 : r > 1 ? 1 : r
  }
  const weightLabel = (w: number): { text: string; cls: string } => {
    if (w < 0.25) return { text: 'ignore', cls: 'text-gray-400' }
    if (w < 0.75) return { text: 'informs', cls: 'text-indigo-500' }
    return { text: 'strongly informs', cls: 'text-indigo-700 font-medium' }
  }

  const updateWeight = (dim: string, tag: string, w: number) => {
    setTagWeights(prev => ({ ...prev, [keyOf(dim, tag)]: snapWeight(w) }))
  }

  const selectedCount = Object.values(tagWeights).filter(w => w > 0).length

  const applySuggestion = (
    suggestionText: string,
    relevantContext: { dimension: string; tag: string }[],
    titleOverride?: string,
  ) => {
    setText(suggestionText)
    if (titleOverride) setTitle(titleOverride)
    // Auto-select shared tags at full weight so the user sees which contexts the suggestion implies.
    setTagWeights(prev => {
      const next = { ...prev }
      for (const t of relevantContext) {
        if (allBundles.some(b => b.dim === t.dimension && b.tag === t.tag)) {
          if (!next[keyOf(t.dimension, t.tag)]) next[keyOf(t.dimension, t.tag)] = 1
        }
      }
      return next
    })
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !text.trim()) return
    // null = unmatched (auto-match runs on first compile); only emit a list if the user
    // explicitly picked tags or accepted a suggestion's shared tags.
    const relevantContext: RuleContextTag[] | null = selectedCount === 0
      ? null
      : allBundles
          .filter(b => (tagWeights[keyOf(b.dim, b.tag)] ?? 0) > 0)
          .map(b => ({
            dimension: b.dim as string,
            tag: b.tag,
            weight: tagWeights[keyOf(b.dim, b.tag)] ?? 1,
          }))
    onCreate(title.trim(), text.trim(), relevantContext)
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <h2 className="text-lg font-semibold mb-4">New Rule</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Title</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="e.g., No Self-Promotion"
              autoFocus
            />
          </div>
          {/* Context picker — placed above suggestions so the user can scope what gets matched.
              Mirrors the 3-level slider used in the rules editor for consistency. */}
          {allBundles.length > 0 && (
            <div>
              <label className="block text-sm font-medium mb-1">
                Relevant context tags
                <span className="ml-2 text-xs font-normal text-gray-500">
                  Pick to scope suggestions
                  {selectedCount === 0
                    ? ' — empty = match against all of this community\'s context'
                    : ` — ${selectedCount} selected`}
                </span>
              </label>
              <div className="space-y-2">
                {(['purpose', 'participants', 'stakes', 'tone'] as const).map(dim => {
                  const bundles = allBundles.filter(b => b.dim === dim)
                  if (bundles.length === 0) return null
                  return (
                    <div key={dim}>
                      <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1">
                        {dim}
                      </div>
                      <div className="space-y-1">
                        {bundles.map(b => {
                          const k = keyOf(b.dim, b.tag)
                          const w = tagWeights[k] ?? 0
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
                                onChange={e => updateWeight(b.dim, b.tag, parseFloat(e.target.value))}
                                className="w-20 accent-indigo-500"
                                title={`weight ${w}`}
                              />
                              <div className={`flex-1 ${lbl.cls}`}>{lbl.text}</div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <RuleTextSuggestion
            communityId={communityId}
            title={title}
            selectedRelevantContext={allBundles
              .filter(b => (tagWeights[keyOf(b.dim, b.tag)] ?? 0) > 0)
              .map(b => ({ dimension: b.dim as string, tag: b.tag }))}
            onApply={applySuggestion}
          />
          <div>
            <label className="block text-sm font-medium mb-1">Rule Text</label>
            <textarea
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              rows={5}
              value={text}
              onChange={e => setText(e.target.value)}
              placeholder="Write the full rule text as it would appear in the community rules..."
            />
          </div>

          <div className="flex gap-2 justify-end">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button
              type="submit"
              className="btn-primary"
              disabled={loading || !title.trim() || !text.trim()}
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : null}
              {loading ? 'Creating…' : 'Create Rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
