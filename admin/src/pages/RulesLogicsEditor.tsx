import React, { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  BookOpen,
  Check,
  Edit2,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  X,
} from 'lucide-react'
import {
  CommunityContextNote,
  ContextPreviewResponse,
  DecisionPreviewResult,
  PreviewChecklistItem,
  PreviewRecompileResult,
  Rule,
  RuleContextTag,
  RuleHealthSummary,
  Suggestion,
  commitContextAdjustment,
  createRule,
  discardContextPreview,
  getChecklist,
  getCommunity,
  getRulesHealthSummary,
  listRules,
  overrideRuleType,
  previewContextAdjustment,
  previewDecisions,
  previewRecompile,
  updateRule,
} from '../api/client'
import ChecklistTree from '../components/ChecklistTree'
import ChecklistPreview from '../components/ChecklistPreview'
import ChecklistDiff from '../components/ChecklistDiff'
import DecisionsPanel from '../components/DecisionsPanel'
import RuleContextPicker from '../components/RuleContextPicker'
import RuleHealthPanel from '../components/RuleHealthPanel'
import TestModal from '../components/TestModal'
import { showErrorToast } from '../components/Toast'

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
  const sa = new Set(a.map(mk))
  return b.every(t => sa.has(mk(t)))
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

function nestPreviewItems(flat: Record<string, unknown>[]): PreviewChecklistItem[] {
  const nodes = new Map<string, PreviewChecklistItem>()
  for (const d of flat) {
    const id = String(d.id)
    nodes.set(id, { ...(d as unknown as PreviewChecklistItem), children: [] })
  }
  const roots: PreviewChecklistItem[] = []
  const sorted = [...flat].sort((a, b) => Number(a.order ?? 0) - Number(b.order ?? 0))
  for (const d of sorted) {
    const id = String(d.id)
    const node = nodes.get(id)!
    const parentId = d.parent_id ? String(d.parent_id) : null
    if (parentId && nodes.has(parentId)) {
      nodes.get(parentId)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
}

function mergeSuggestionOperations(suggestions: Suggestion[]): Record<string, unknown>[] {
  const merged: Record<string, unknown>[] = []
  for (const s of suggestions) {
    const ops = (s.content as Record<string, unknown>).operations
    if (Array.isArray(ops)) {
      merged.push(...(ops as Record<string, unknown>[]))
    }
  }
  return merged
}

interface RulesLogicsEditorProps {
  communityId: string
}

export default function RulesLogicsEditor({ communityId }: RulesLogicsEditorProps) {
  const queryClient = useQueryClient()

  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [editMode, setEditMode] = useState(false)
  const [editingText, setEditingText] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const [hoveredAnchor, setHoveredAnchor] = useState<string | null>(null)
  const [selectedChecklistItemId, setSelectedChecklistItemId] = useState<string | null>(null)

  const [previewResult, setPreviewResult] = useState<PreviewRecompileResult | null>(null)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)
  const [contextPreview, setContextPreview] = useState<ContextPreviewResponse | null>(null)
  const [savingContextPreview, setSavingContextPreview] = useState(false)
  const [committingContext, setCommittingContext] = useState(false)

  const [healthSuggestions, setHealthSuggestions] = useState<Suggestion[]>([])

  const [decisionPreview, setDecisionPreview] = useState<DecisionPreviewResult[] | null>(null)
  const [decisionPreviewLoading, setDecisionPreviewLoading] = useState(false)

  const [showTestModal, setShowTestModal] = useState(false)
  const [showNewRule, setShowNewRule] = useState(false)

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
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

  const createRuleMutation = useMutation({
    mutationFn: ({ title, text }: { title: string; text: string }) =>
      createRule(communityId, { title, text, priority: rules.length }),
    onSuccess: rule => {
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
      setSelectedRuleId(rule.id)
      setEditingText(rule.text)
      setEditingTitle(rule.title)
      setShowNewRule(false)
    },
  })

  const handleSelectRule = (rule: Rule) => {
    setSelectedRuleId(rule.id)
    setEditingText(rule.text)
    setEditingTitle(rule.title)
    setPreviewResult(null)
    setHoveredAnchor(null)
    setSelectedChecklistItemId(null)
    setContextPreview(null)
    setEditMode(false)
    setHealthSuggestions([])
    setDecisionPreview(null)
  }

  const exitEditMode = () => {
    if (selectedRule) setEditingText(selectedRule.text)
    setPreviewResult(null)
    setDecisionPreview(null)
    setEditMode(false)
  }

  const handlePreviewChanges = async () => {
    if (!selectedRuleId) return
    setIsPreviewLoading(true)
    setPreviewResult(null)
    try {
      const result = await previewRecompile(selectedRuleId, editingText)
      setPreviewResult(result)
    } catch (e) {
      showErrorToast(extractErrorMessage(e))
    } finally {
      setIsPreviewLoading(false)
    }
  }

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
    await handleSaveRule()
    setPreviewResult(null)
    setDecisionPreview(null)
    setEditMode(false)
  }

  const handleDiscardEdit = () => {
    if (selectedRule) setEditingText(selectedRule.text)
    setPreviewResult(null)
    setDecisionPreview(null)
  }

  // Trigger decisions preview when rule-text preview becomes active.
  useEffect(() => {
    if (!selectedRuleId || !previewResult) {
      return
    }
    let cancelled = false
    setDecisionPreviewLoading(true)
    previewDecisions(selectedRuleId, { rule_text: editingText, limit: 50 })
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
  }, [previewResult, selectedRuleId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Trigger decisions preview when analyze suggestions are present.
  useEffect(() => {
    if (!selectedRuleId || healthSuggestions.length === 0) {
      return
    }
    const ops = mergeSuggestionOperations(healthSuggestions)
    if (ops.length === 0) return
    let cancelled = false
    setDecisionPreviewLoading(true)
    previewDecisions(selectedRuleId, { checklist_override_operations: ops, limit: 50 })
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
  }, [healthSuggestions, selectedRuleId])

  // Clear decision preview when no previews are active.
  useEffect(() => {
    if (!previewResult && !contextPreview && healthSuggestions.length === 0) {
      setDecisionPreview(null)
      setDecisionPreviewLoading(false)
    }
  }, [previewResult, contextPreview, healthSuggestions])

  const effectiveContextPreview: ContextPreviewResponse | null = useMemo(() => {
    if (contextPreview) return contextPreview
    if (!selectedRule?.pending_checklist_json) return null
    const pendingRel = selectedRule.pending_relevant_context?.value ?? null
    if (!sameTagSet(pendingRel, selectedRule.relevant_context)) return null
    if (!sameNotes(selectedRule.pending_custom_context_notes, selectedRule.custom_context_notes)) return null
    return {
      preview_items: nestPreviewItems(selectedRule.pending_checklist_json as Record<string, unknown>[]),
      summary: selectedRule.pending_context_adjustment_summary ?? null,
      generated_at: selectedRule.pending_generated_at ?? '',
      current_items: checklist,
    }
  }, [contextPreview, selectedRule, checklist])

  const analyzePreviewOps = useMemo(
    () => mergeSuggestionOperations(healthSuggestions),
    [healthSuggestions],
  )

  const isAnyPreviewActive =
    !!previewResult || !!effectiveContextPreview || analyzePreviewOps.length > 0

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
                  if (selectedRule && editingTitle !== selectedRule.title) {
                    handleSaveRule()
                  }
                }}
              />
              <button
                className="btn-secondary text-xs"
                onClick={() => setShowTestModal(true)}
                title="Test a hypothetical post against the automod"
              >
                <Play size={12} />
                Test
              </button>
            </div>

            {/* 3-column detail area */}
            <div className="flex min-h-0 border-b border-gray-200" style={{ flex: '3 3 0%' }}>
              {/* Rule Text panel */}
              <div className="flex-1 min-w-0 border-r border-gray-200 bg-white flex flex-col overflow-hidden">
                <PanelHeader title="Rule Text">
                  <button
                    className="btn-secondary text-xs py-0.5"
                    onClick={() => (editMode ? exitEditMode() : setEditMode(true))}
                    title={editMode ? 'Lock rule' : 'Edit rule'}
                  >
                    {editMode ? <><Check size={11} /> Done</> : <><Edit2 size={11} /> Edit</>}
                  </button>
                </PanelHeader>

                <div className="flex-1 flex flex-col overflow-hidden p-4 gap-2 min-h-0">
                  {editMode ? (
                    <>
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
                      <textarea
                        className="flex-1 resize-none border border-indigo-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono min-h-0"
                        value={editingText}
                        onChange={e => { setEditingText(e.target.value); setPreviewResult(null); setDecisionPreview(null) }}
                        placeholder="Rule text..."
                      />
                      <div className="flex gap-2 justify-end flex-shrink-0">
                        <button
                          className="btn-secondary text-xs"
                          onClick={handleDiscardEdit}
                          disabled={editingText === selectedRule.text}
                        >
                          <X size={12} /> Discard edits
                        </button>
                        {previewResult ? (
                          <button className="btn-primary text-xs" onClick={handleConfirmSave} disabled={isSaving}>
                            {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                            {isSaving ? 'Saving…' : 'Confirm & Save'}
                          </button>
                        ) : selectedRule.rule_type === 'actionable' ? (
                          <button
                            className="btn-primary text-xs"
                            onClick={handlePreviewChanges}
                            disabled={isPreviewLoading || editingText === selectedRule.text}
                          >
                            {isPreviewLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                            {isPreviewLoading ? 'Previewing…' : 'Preview'}
                          </button>
                        ) : (
                          <button className="btn-primary text-xs" onClick={handleConfirmSave} disabled={isSaving}>
                            {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                            {isSaving ? 'Saving…' : 'Save'}
                          </button>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="flex-1 border border-gray-200 rounded-lg p-3 text-sm font-mono overflow-auto bg-gray-50 whitespace-pre-wrap text-gray-700 leading-relaxed min-h-0">
                      {renderTextWithHighlight(editingText, hoveredAnchor)}
                    </div>
                  )}

                  {/* Rule metadata: context + type + scope */}
                  <div className="border-t border-gray-100 pt-2 flex flex-col gap-2 flex-shrink-0">
                    {selectedRule.rule_type === 'actionable' && (
                      <div className="max-h-48 overflow-auto">
                        <RuleContextPicker
                          rule={selectedRule}
                          community_context={community?.community_context ?? null}
                          readOnly={!editMode}
                          isSavingPreview={savingContextPreview}
                          isCommitting={committingContext}
                          onSavePreview={async ({ relevant_context, custom_context_notes }) => {
                            setSavingContextPreview(true)
                            try {
                              await updateRule(selectedRule.id, {
                                relevant_context,
                                custom_context_notes,
                              })
                              const preview = await previewContextAdjustment(selectedRule.id)
                              setContextPreview(preview)
                              queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                            } catch (e) {
                              showErrorToast(extractErrorMessage(e))
                            } finally {
                              setSavingContextPreview(false)
                            }
                          }}
                          onCommit={async () => {
                            setCommittingContext(true)
                            try {
                              await commitContextAdjustment(selectedRule.id)
                              setContextPreview(null)
                              queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                              queryClient.invalidateQueries({ queryKey: ['checklist', selectedRule.id] })
                            } catch (e) {
                              showErrorToast(extractErrorMessage(e))
                            } finally {
                              setCommittingContext(false)
                            }
                          }}
                          onDiscard={async () => {
                            try {
                              await discardContextPreview(selectedRule.id)
                              setContextPreview(null)
                              queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                            } catch (e) {
                              showErrorToast(extractErrorMessage(e))
                            }
                          }}
                        />
                      </div>
                    )}

                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-gray-500">Type:</span>
                      {['actionable', 'procedural', 'meta', 'informational'].map(type => {
                        const active = selectedRule.rule_type === type
                        return (
                          <button
                            key={type}
                            disabled={!editMode}
                            className={`text-xs px-2 py-0.5 rounded border ${
                              !editMode
                                ? active
                                  ? 'bg-indigo-50 border-indigo-200 text-indigo-600 cursor-default'
                                  : 'bg-white border-gray-200 text-gray-400 cursor-default'
                                : active
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
                            disabled={!editMode}
                            className={`text-xs px-2 py-0.5 rounded border ${
                              !editMode
                                ? active
                                  ? 'bg-emerald-50 border-emerald-200 text-emerald-600 cursor-default'
                                  : 'bg-white border-gray-200 text-gray-400 cursor-default'
                                : active
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

              {/* Automod Logic panel */}
              <div className="w-[35%] flex-shrink-0 flex flex-col border-r border-gray-200 bg-white overflow-hidden">
                <PanelHeader title="Automoderator Logic">
                  {isAnyPreviewActive && (
                    <span className="text-xs font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded px-1.5 py-0.5">
                      {effectiveContextPreview ? 'Context Preview' : previewResult ? 'Preview' : 'Analyze Preview'}
                    </span>
                  )}
                </PanelHeader>

                <div className="flex-1 overflow-auto p-3">
                  {previewResult ? (
                    <ChecklistPreview operations={previewResult.operations} existingItems={checklist} />
                  ) : effectiveContextPreview ? (
                    <ChecklistDiff
                      current={effectiveContextPreview.current_items}
                      preview={effectiveContextPreview.preview_items}
                      summary={effectiveContextPreview.summary}
                    />
                  ) : analyzePreviewOps.length > 0 ? (
                    <ChecklistPreview operations={analyzePreviewOps as PreviewRecompileResult['operations']} existingItems={checklist} />
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
                      <strong>{selectedRule.override_count} overrides</strong> suggest this checklist may need updating. Try the Analyze button →
                    </span>
                  </div>
                )}
              </div>

              {/* Rule Health panel */}
              <div className="w-[35%] flex-shrink-0 flex flex-col bg-white overflow-hidden">
                <RuleHealthPanel
                  ruleId={selectedRuleId!}
                  highlightItemId={selectedChecklistItemId}
                  onHealthSuggestionsChange={setHealthSuggestions}
                />
              </div>
            </div>

            {/* Decisions panel */}
            <div className="flex-1 flex flex-col overflow-hidden bg-white min-h-0" style={{ flex: '2 2 0%' }}>
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
              </PanelHeader>
              <DecisionsPanel
                communityId={communityId}
                ruleId={selectedRuleId}
                checklistItemId={selectedChecklistItemId}
                previewResults={decisionPreview}
                previewLoading={decisionPreviewLoading}
              />
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <BookOpen size={40} className="mb-3 opacity-30" />
            <p className="text-sm">Select a rule to edit</p>
          </div>
        )}
      </div>

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
          onClose={() => setShowNewRule(false)}
          onCreate={(title, text) => createRuleMutation.mutate({ title, text })}
          loading={createRuleMutation.isPending}
        />
      )}
    </div>
  )
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
  onClose,
  onCreate,
  loading,
}: {
  onClose: () => void
  onCreate: (title: string, text: string) => void
  loading: boolean
}) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !text.trim()) return
    onCreate(title.trim(), text.trim())
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-lg">
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
