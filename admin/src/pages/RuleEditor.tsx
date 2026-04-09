import React, { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus, ChevronUp, ChevronDown, ChevronLeft, ChevronRight, RefreshCw, BookOpen, AlertCircle, Loader2, Upload,
  CheckCircle, XCircle, Edit2, Check, X, Play, Image,
} from 'lucide-react'
import {
  listRules,
  createRule,
  updateRule,
  updateRulePriority,
  overrideRuleType,
  getChecklist,
  recompileRule,
  previewRecompile,
  listSuggestions,
  batchImportRules,
  evaluatePost,
  evaluateExamplesWithDraft,
  BatchImportRuleItem,
  BatchImportResponse,
  PreviewRecompileResult,
  DraftEvaluationResult,
  Rule,
  ChecklistItem,
  Decision,
} from '../api/client'
import ChecklistTree from '../components/ChecklistTree'
import ChecklistPreview from '../components/ChecklistPreview'
import ExamplesPanel from '../components/ExamplesPanel'
import SuggestionDiff from '../components/SuggestionDiff'

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

interface RuleEditorProps {
  communityId: string
}

export default function RuleEditor({ communityId }: RuleEditorProps) {
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [showNewRule, setShowNewRule] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [editingText, setEditingText] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isEditingRuleText, setIsEditingRuleText] = useState(false)
  const [hoveredAnchor, setHoveredAnchor] = useState<string | null>(null)
  const [selectedChecklistItemId, setSelectedChecklistItemId] = useState<string | null>(null)
  const [highlightedItemId, setHighlightedItemId] = useState<string | null>(null)
  const [ruleListOpen, setRuleListOpen] = useState(true)
  const [previewResult, setPreviewResult] = useState<PreviewRecompileResult | null>(null)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)
  const [draftEvalResults, setDraftEvalResults] = useState<DraftEvaluationResult[] | null>(null)
  const [isDraftEvaluating, setIsDraftEvaluating] = useState(false)

  const queryClient = useQueryClient()

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const selectedRule = rules.find(r => r.id === selectedRuleId) || null

  const { data: checklist = [] } = useQuery({
    queryKey: ['checklist', selectedRuleId],
    queryFn: () => getChecklist(selectedRuleId!),
    enabled: !!selectedRuleId,
    refetchInterval: (query) => {
      // Poll while the checklist is empty for an actionable rule (background compilation in progress)
      const items = query.state.data
      const isActionable = selectedRule?.rule_type === 'actionable'
      return isActionable && (!items || items.length === 0) ? 3000 : false
    },
  })

  const { data: suggestions = [] } = useQuery({
    queryKey: ['suggestions', selectedRuleId],
    queryFn: () => listSuggestions(selectedRuleId!, 'pending'),
    enabled: !!selectedRuleId,
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

  const recompileMutation = useMutation({
    mutationFn: () => recompileRule(selectedRuleId!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['checklist', selectedRuleId] })
      queryClient.invalidateQueries({ queryKey: ['suggestions', selectedRuleId] })
      if (!data.diff?.no_changes) {
        setShowSuggestions(true)
      }
    },
  })

  const handleSelectRule = (rule: Rule) => {
    setSelectedRuleId(rule.id)
    setEditingText(rule.text)
    setEditingTitle(rule.title)
    setIsEditingRuleText(false)
    setPreviewResult(null)
    setDraftEvalResults(null)
    setHoveredAnchor(null)
    setSelectedChecklistItemId(null)
  }

  const handlePreviewChanges = async () => {
    if (!selectedRuleId) return
    setIsPreviewLoading(true)
    setPreviewResult(null)
    try {
      const result = await previewRecompile(selectedRuleId, editingText)
      setPreviewResult(result)
    } finally {
      setIsPreviewLoading(false)
    }
  }

  const handleConfirmSave = async () => {
    await handleSaveRule()
    setIsEditingRuleText(false)
    setPreviewResult(null)
    setDraftEvalResults(null)
  }

  const handleDiscardEdit = () => {
    if (selectedRule) setEditingText(selectedRule.text)
    setIsEditingRuleText(false)
    setPreviewResult(null)
    setDraftEvalResults(null)
  }

  const handleDraftEvaluate = async () => {
    if (!selectedRuleId) return
    setIsDraftEvaluating(true)
    setDraftEvalResults(null)
    try {
      const results = await evaluateExamplesWithDraft(selectedRuleId, editingText)
      setDraftEvalResults(results)
    } finally {
      setIsDraftEvaluating(false)
    }
  }

  const handleSaveRule = async () => {
    if (!selectedRuleId) return
    setIsSaving(true)
    try {
      await updateRule(selectedRuleId, { text: editingText, title: editingTitle })
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
    } finally {
      setIsSaving(false)
    }
  }

  const handlePriorityChange = async (ruleId: string, dir: 'up' | 'down') => {
    const sorted = [...rules].sort((a, b) => a.priority - b.priority)
    const idx = sorted.findIndex(r => r.id === ruleId)
    if ((dir === 'up' && idx === 0) || (dir === 'down' && idx === sorted.length - 1)) return

    const swapIdx = dir === 'up' ? idx - 1 : idx + 1
    await updateRulePriority(ruleId, sorted[swapIdx].priority)
    await updateRulePriority(sorted[swapIdx].id, sorted[idx].priority)
    queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
  }

  if (!communityId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <p>Select a community to manage rules.</p>
      </div>
    )
  }

  const pendingSuggestions = suggestions.filter(s => s.status === 'pending')

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left panel: Rule list */}
      <div className={`${ruleListOpen ? 'w-64' : 'w-8'} flex-shrink-0 border-r border-gray-200 bg-white flex flex-col transition-all duration-200 overflow-hidden`}>
        {ruleListOpen ? (
          <>
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h2 className="font-semibold text-sm">Rules</h2>
              <div className="flex gap-1">
                <button className="btn-secondary text-xs py-1" onClick={() => setShowImport(true)} title="Batch import rules">
                  <Upload size={12} />
                  Import
                </button>
                <button className="btn-primary text-xs py-1" onClick={() => setShowNewRule(true)}>
                  <Plus size={12} />
                  New
                </button>
                <button
                  className="text-gray-400 hover:text-gray-700 transition-colors p-1"
                  onClick={() => setRuleListOpen(false)}
                  title="Collapse rule list"
                >
                  <ChevronLeft size={14} />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-auto py-2">
              {rulesLoading && (
                <div className="text-xs text-gray-400 text-center py-4">Loading...</div>
              )}
              {rules
                .filter(r => r.is_active)
                .sort((a, b) => a.priority - b.priority)
                .map(rule => (
                  <div
                    key={rule.id}
                    className={`group flex items-center px-3 py-2 cursor-pointer hover:bg-gray-50 transition-colors border-l-2 ${selectedRuleId === rule.id ? 'border-indigo-500 bg-indigo-50' : 'border-transparent'
                      }`}
                    onClick={() => handleSelectRule(rule)}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{rule.title}</p>
                      <span className={`badge ${RULE_TYPE_COLORS[rule.rule_type] || 'badge-gray'} mt-0.5`}>
                        {rule.rule_type}
                      </span>
                    </div>
                    <div className="flex flex-col opacity-0 group-hover:opacity-100 transition-opacity ml-1">
                      <button
                        className="text-gray-400 hover:text-gray-700 p-0.5"
                        onClick={e => { e.stopPropagation(); handlePriorityChange(rule.id, 'up') }}
                      >
                        <ChevronUp size={12} />
                      </button>
                      <button
                        className="text-gray-400 hover:text-gray-700 p-0.5"
                        onClick={e => { e.stopPropagation(); handlePriorityChange(rule.id, 'down') }}
                      >
                        <ChevronDown size={12} />
                      </button>
                    </div>
                  </div>
                ))}
              {rules.filter(r => r.is_active).length === 0 && !rulesLoading && (
                <div className="text-xs text-gray-400 text-center py-8">
                  No rules yet. Create one!
                </div>
              )}
            </div>
          </>
        ) : (
          <button
            className="flex-1 flex items-start justify-center pt-3 text-gray-400 hover:text-gray-700 transition-colors"
            onClick={() => setRuleListOpen(true)}
            title="Expand rule list"
          >
            <ChevronRight size={14} />
          </button>
        )}
      </div>

      {/* Main area: top 60% (editor + checklist + examples) + bottom 40% (testing) */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top section: rule text editor, checklist, examples side by side */}
        <div className="flex min-h-0 border-b border-gray-200" style={{ flex: '3 3 0%' }}>
          {/* Rule text editor */}
          <div className="flex-1 flex flex-col min-w-0 border-r border-gray-200 bg-white overflow-hidden">
            {selectedRule ? (
              <>
                <div className="flex flex-col px-4 pt-3 pb-2 border-b border-gray-200 flex-shrink-0 gap-1.5">
                  {/* Row 1: title */}
                  <input
                    className="font-semibold text-gray-900 bg-transparent border-b border-transparent hover:border-gray-300 focus:border-indigo-500 focus:outline-none px-0.5 text-base w-full"
                    value={editingTitle}
                    onChange={e => setEditingTitle(e.target.value)}
                  />
                  {/* Row 2: badge + buttons */}
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className={`badge ${RULE_TYPE_COLORS[selectedRule.rule_type] || 'badge-gray'}`}>
                      {selectedRule.rule_type}
                    </span>
                    <div className="w-px h-3.5 bg-gray-200 mx-0.5" />
                    {/* {!isEditingRuleText && (
                      <button
                        className="btn-secondary text-xs"
                        onClick={handleSaveRule}
                        disabled={isSaving || editingTitle === selectedRule.title}
                      >
                        {isSaving ? <Loader2 size={12} className="animate-spin" /> : null}
                        {isSaving ? 'Saving...' : 'Save'}
                      </button>
                    )} */}
                    {selectedRule.rule_type === 'actionable' && (
                      <>
                        <button
                          className="btn-secondary text-xs"
                          onClick={() => recompileMutation.mutate()}
                          disabled={recompileMutation.isPending}
                          title="Recompile rule into checklist"
                        >
                          <RefreshCw size={12} className={recompileMutation.isPending ? 'animate-spin' : ''} />
                          Recompile
                        </button>
                      </>
                    )}
                    {pendingSuggestions.length > 0 && (
                      <button
                        className="btn-secondary text-xs border-amber-300"
                        onClick={() => setShowSuggestions(true)}
                      >
                        <AlertCircle size={12} className="text-amber-500" />
                        {pendingSuggestions.length} suggestions
                      </button>
                    )}
                  </div>
                </div>

                {/* Text editor */}
                <div className="flex-1 p-4 overflow-hidden flex flex-col">
                  {isEditingRuleText ? (
                    <div className="flex-1 flex flex-col gap-2 min-h-0">
                      <textarea
                        className="flex-1 resize-none border border-indigo-300 rounded-lg p-3 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
                        value={editingText}
                        onChange={e => { setEditingText(e.target.value); setPreviewResult(null); setDraftEvalResults(null) }}
                        placeholder="Rule text..."
                        autoFocus
                      />
                      <div className="flex gap-2 justify-end flex-shrink-0">
                        <button className="btn-secondary text-xs" onClick={handleDiscardEdit}>
                          <X size={12} /> Discard
                        </button>
                        {previewResult ? (
                          <button className="btn-primary text-xs" onClick={handleConfirmSave} disabled={isSaving}>
                            {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                            {isSaving ? 'Saving...' : 'Confirm & Save'}
                          </button>
                        ) : selectedRule?.rule_type === 'actionable' ? (
                          <button
                            className="btn-primary text-xs"
                            onClick={handlePreviewChanges}
                            disabled={isPreviewLoading || editingText === selectedRule?.text}
                          >
                            {isPreviewLoading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                            {isPreviewLoading ? 'Previewing...' : 'Preview Changes'}
                          </button>
                        ) : (
                          <button
                            className="btn-primary text-xs"
                            onClick={handleConfirmSave}
                            disabled={isSaving}
                          >
                            {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                            {isSaving ? 'Saving...' : 'Save'}
                          </button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="relative flex-1 min-h-0">
                      <div className="w-full h-full border border-gray-200 rounded-lg p-3 text-sm font-mono overflow-auto bg-gray-50 whitespace-pre-wrap text-gray-700 leading-relaxed">
                        {renderTextWithHighlight(editingText, hoveredAnchor)}
                      </div>
                      <button
                        className="absolute top-2 right-2 btn-secondary text-xs py-1"
                        onClick={() => setIsEditingRuleText(true)}
                        title="Edit rule text"
                      >
                        <Edit2 size={12} /> Edit
                      </button>
                    </div>
                  )}
                </div>

                {/* Override rule type */}
                <div className="px-4 pb-3 flex items-center gap-2 flex-shrink-0">
                  <span className="text-xs text-gray-500">Override type:</span>
                  {['actionable', 'procedural', 'meta', 'informational'].map(type => (
                    <button
                      key={type}
                      className={`text-xs px-2 py-0.5 rounded border transition-colors ${selectedRule.rule_type === type
                        ? 'bg-indigo-600 text-white border-indigo-600'
                        : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                        }`}
                      onClick={async () => {
                        await overrideRuleType(selectedRule.id, type)
                        queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
                      }}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-gray-400">
                <BookOpen size={40} className="mb-3 opacity-30" />
                <p className="text-sm">Select a rule to edit</p>
              </div>
            )}
          </div>

          {/* Checklist column */}
          <div className="w-[35%] flex-shrink-0 flex flex-col border-r border-gray-200 bg-white overflow-hidden">
            <div className="px-3 py-2 border-b border-gray-100 flex-shrink-0 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Automoderator Logic</h3>
              {previewResult && (
                <span className="text-xs font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded px-1.5 py-0.5">Preview</span>
              )}
            </div>
            {!previewResult && selectedRule && (selectedRule.override_count ?? 0) >= 3 && (
              <div className="mx-3 mt-2 mb-1 flex-shrink-0 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-xs text-amber-800 flex items-start gap-2">
                <AlertCircle size={13} className="mt-0.5 flex-shrink-0 text-amber-500" />
                <span>
                  <strong>{selectedRule.override_count} moderator overrides</strong> suggest this checklist may need updating.{' '}
                  <button
                    className="underline hover:no-underline"
                    onClick={() => recompileMutation.mutate()}
                    disabled={recompileMutation.isPending}
                  >
                    Analyze
                  </button>
                </span>
              </div>
            )}
            <div className="flex-1 overflow-auto p-3">
              {previewResult ? (
                <ChecklistPreview operations={previewResult.operations} existingItems={checklist} />
              ) : selectedRuleId ? (
                selectedRule?.rule_type === 'actionable' ? (
                  checklist.length === 0 ? (
                    <div className="flex items-center gap-2 text-xs text-gray-400 italic p-1">
                      <svg className="animate-spin h-3 w-3 text-indigo-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                      </svg>
                      Compiling checklist… this may take a moment.
                    </div>
                  ) : (
                    <ChecklistTree items={checklist} ruleId={selectedRuleId} onAnchorHover={setHoveredAnchor} selectedItemId={selectedChecklistItemId} onItemSelect={setSelectedChecklistItemId} highlightedItemId={highlightedItemId} />
                  )
                ) : (
                  <div className="text-xs text-gray-400 italic">Only actionable rules have checklists.</div>
                )
              ) : (
                <div className="text-xs text-gray-400 italic">Select a rule to view its checklist.</div>
              )}
            </div>
          </div>

          {/* Examples column */}
          <div className="w-[35%] flex-shrink-0 flex flex-col bg-white overflow-hidden">
            <div className="px-3 py-2 border-b border-gray-100 flex-shrink-0 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Examples</h3>
              {previewResult && (
                <button
                  className="btn-secondary text-xs"
                  onClick={handleDraftEvaluate}
                  disabled={isDraftEvaluating}
                  title="Evaluate examples against the draft checklist"
                >
                  {isDraftEvaluating ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
                  {isDraftEvaluating ? 'Evaluating…' : 'Test examples'}
                </button>
              )}
            </div>
            <div className="flex-1 overflow-hidden">
              {selectedRuleId ? (
                <ExamplesPanel
                  ruleId={selectedRuleId}
                  filterItemId={selectedChecklistItemId}
                  onItemHighlight={setHighlightedItemId}
                  previewVerdicts={previewResult?.example_verdicts}
                  draftEvalResults={draftEvalResults ?? undefined}
                />
              ) : (
                <div className="p-3 text-xs text-gray-400 italic">Select a rule to view examples.</div>
              )}
            </div>
          </div>
        </div>

        {/* Bottom section: test panel */}
        <div className="min-h-0 flex flex-col overflow-hidden bg-white" style={{ flex: '2 2 0%' }}>
          <div className="flex-1 min-h-0 overflow-hidden">
            <TestingPanel communityId={communityId} checklist={checklist} />
          </div>
        </div>
      </div>

      {/* Batch Import Modal */}
      {showImport && (
        <BatchImportModal
          communityId={communityId}
          onClose={() => setShowImport(false)}
          onSuccess={() => {
            queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
            setShowImport(false)
          }}
        />
      )}

      {/* New Rule Modal */}
      {showNewRule && (
        <NewRuleModal
          onClose={() => setShowNewRule(false)}
          onCreate={(title, text) => createRuleMutation.mutate({ title, text })}
          loading={createRuleMutation.isPending}
        />
      )}

      {/* Suggestions overlay */}
      {showSuggestions && selectedRuleId && (
        <SuggestionDiff
          suggestions={suggestions}
          ruleId={selectedRuleId}
          currentRuleText={selectedRule?.text}
          onClose={() => setShowSuggestions(false)}
        />
      )}
    </div>
  )
}

// ── Testing Panel ───────────────────────────────────────────────────────────────

function flattenChecklist(items: ChecklistItem[]): Record<string, ChecklistItem> {
  const map: Record<string, ChecklistItem> = {}
  const visit = (list: ChecklistItem[]) => {
    for (const item of list) {
      map[item.id] = item
      if (item.children?.length) visit(item.children)
    }
  }
  visit(items)
  return map
}

function TestingPanel({
  communityId,
  checklist,
}: {
  communityId: string
  checklist: ChecklistItem[]
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [threadContext, setThreadContext] = useState('')
  const [imageUrls, setImageUrls] = useState<string[]>([])
  const [imageInput, setImageInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState<Decision | null>(null)
  const [error, setError] = useState<string | null>(null)

  const addImageUrl = () => {
    const url = imageInput.trim()
    if (url && !imageUrls.includes(url)) {
      setImageUrls(prev => [...prev, url])
    }
    setImageInput('')
  }

  const checklistMap = useMemo(() => flattenChecklist(checklist), [checklist])

  const handleTest = async () => {
    setIsLoading(true)
    setError(null)
    setResult(null)
    try {
      const post = {
        content: {
          title: title || undefined,
          body: body || undefined,
          ...(imageUrls.length ? { media: imageUrls } : {}),
        },
        ...(threadContext.trim() ? {
          context: { platform_metadata: { thread_context: threadContext } },
        } : {}),
      }
      const { decision } = await evaluatePost(communityId, post)
      setResult(decision)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Evaluation failed')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <h3 className="font-semibold text-sm flex items-center gap-1.5 text-gray-700">
          <Play size={13} className="text-indigo-500" />
          Test Post / Comment
        </h3>
        <button
          className="btn-primary text-xs"
          onClick={handleTest}
          disabled={isLoading || (!title.trim() && !body.trim())}
        >
          {isLoading
            ? <><Loader2 size={12} className="animate-spin" /> Testing...</>
            : <><Play size={12} /> Test</>
          }
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 flex min-h-0">
        {/* Input area */}
        <div className="w-1/2 flex flex-col gap-2 p-3 border-r border-gray-200 overflow-auto">
          <div className="flex-shrink-0">
            <label className="block text-xs font-medium text-gray-600 mb-0.5">Title</label>
            <input
              className="w-full border border-gray-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="Post title (optional)"
            />
          </div>
          <div className="flex-1 flex flex-col min-h-0">
            <label className="block text-xs font-medium text-gray-600 mb-0.5">Content</label>
            <textarea
              className="flex-1 min-h-[50px] border border-gray-300 rounded px-2.5 py-1.5 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={body}
              onChange={e => setBody(e.target.value)}
              placeholder="Post or comment text..."
            />
          </div>
          <div className="flex-shrink-0">
            <label className="block text-xs font-medium text-gray-600 mb-0.5">
              Images{' '}
              <span className="text-gray-400 font-normal">(paste image URLs)</span>
            </label>
            <div className="flex gap-1.5 mb-1.5">
              <input
                className="flex-1 border border-gray-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={imageInput}
                onChange={e => setImageInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addImageUrl()}
                placeholder="https://..."
              />
              <button
                className="btn-secondary text-xs flex items-center gap-1 px-2"
                onClick={addImageUrl}
                disabled={!imageInput.trim()}
              >
                <Plus size={12} /> Add
              </button>
            </div>
            {imageUrls.length > 0 && (
              <ul className="space-y-1">
                {imageUrls.map(url => (
                  <li key={url} className="flex items-center gap-1.5 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1">
                    <Image size={11} className="text-gray-400 flex-shrink-0" />
                    <span className="flex-1 truncate text-gray-600">{url}</span>
                    <button onClick={() => setImageUrls(prev => prev.filter(u => u !== url))} className="text-gray-400 hover:text-red-500">
                      <X size={11} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="flex-shrink-0">
            <label className="block text-xs font-medium text-gray-600 mb-0.5">
              Context{' '}
              <span className="text-gray-400 font-normal">(previous conversation / comment thread)</span>
            </label>
            <textarea
              className="w-full min-h-[50px] border border-gray-300 rounded px-2.5 py-1.5 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={threadContext}
              onChange={e => setThreadContext(e.target.value)}
              placeholder="Paste the comment thread or conversation context here (optional)..."
            />
          </div>
        </div>

        {/* Results area */}
        <div className="w-1/2 overflow-auto p-3">
          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}
          {isLoading && (
            <div className="flex items-center justify-center gap-2 mt-6 text-gray-400 text-sm">
              <Loader2 size={16} className="animate-spin" />
              Evaluating...
            </div>
          )}
          {!result && !error && !isLoading && (
            <p className="text-xs text-gray-400 text-center mt-6">
              Results will appear here after testing.
            </p>
          )}
          {result && (
            <TestResults result={result} checklistMap={checklistMap} />
          )}
        </div>
      </div>
    </div>
  )
}

// ── Test Results ────────────────────────────────────────────────────────────────

const VERDICT_STYLES: Record<string, string> = {
  approve: 'text-green-700 bg-green-50 border-green-200',
  remove: 'text-red-700 bg-red-50 border-red-200',
  review: 'text-amber-700 bg-amber-50 border-amber-200',
  pending: 'text-gray-600 bg-gray-50 border-gray-200',
}

const VERDICT_HEADER_STYLES: Record<string, string> = {
  approve: 'bg-green-50 text-green-700',
  remove: 'bg-red-50 text-red-700',
  review: 'bg-amber-50 text-amber-700',
}

function TestResults({
  result,
  checklistMap,
}: {
  result: Decision
  checklistMap: Record<string, ChecklistItem>
}) {
  const reasoning = result.agent_reasoning as Record<string, Record<string, unknown>>

  return (
    <div className="space-y-2">
      {/* Overall verdict banner */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded border text-sm font-medium ${VERDICT_STYLES[result.agent_verdict] || VERDICT_STYLES.pending}`}>
        <span className="uppercase text-xs tracking-wider font-semibold">{result.agent_verdict}</span>
        <span className="ml-auto text-xs opacity-75">
          {Math.round(result.agent_confidence * 100)}% confidence
        </span>
      </div>

      {/* Per-rule breakdown */}
      {Object.entries(reasoning).map(([ruleId, ruleData]) => {
        if (ruleId === '__community_norms__') {
          return (
            <div key={ruleId} className="border border-amber-200 rounded overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 text-xs font-medium text-amber-800">
                <AlertCircle size={12} />
                Community Norms
                <span className="ml-auto uppercase tracking-wider opacity-75">
                  {String(ruleData.verdict)}
                </span>
              </div>
              {!!ruleData.reasoning && (
                <div className="px-3 py-1.5 text-xs text-gray-600">
                  {String(ruleData.reasoning)}
                </div>
              )}
            </div>
          )
        }

        const verdict = String(ruleData.verdict || 'approve')
        const confidence = Number(ruleData.confidence ?? 0)
        const itemReasoning = (ruleData.item_reasoning ?? {}) as Record<string, Record<string, unknown>>

        // Only hide non-triggered items when the rule actually produced a violation verdict.
        // If verdict=approve (e.g. parent action=continue + child not triggered), show all
        // visited items so the user can see the full chain and understand why it passed.
        const hasViolations = verdict !== 'approve'

        // Recursive renderer — hierarchy derived from parent_id in item data,
        // so it works for all rules, not just the currently selected one.
        // checklistMap is used only for description fallback and ordering.
        const renderNode = (itemId: string, depth: number): React.ReactNode => {
          const data = itemReasoning[itemId]
          if (!data) return null
          const triggered = Boolean(data.triggered)
          if (!triggered && hasViolations) return null
          const desc = String(checklistMap[itemId]?.description || data.description || itemId)
          const reasoningText = data.reasoning ? String(data.reasoning) : null
          const conf = Number(data.confidence ?? 0)
          const childEntries = Object.entries(itemReasoning)
            .filter(([_, d]) => d.parent_id === itemId)
            .sort(([idA], [idB]) => (checklistMap[idA]?.order ?? 0) - (checklistMap[idB]?.order ?? 0))
          return (
            <React.Fragment key={itemId}>
              <div
                style={{ paddingLeft: `${depth * 16 + 12}px` }}
                className={`flex items-start gap-2 pr-3 py-1.5 text-xs border-t border-gray-100 ${triggered ? 'bg-red-50' : ''}`}
              >
                {triggered
                  ? <XCircle size={12} className="text-red-500 mt-0.5 flex-shrink-0" />
                  : <CheckCircle size={12} className="text-green-500 mt-0.5 flex-shrink-0" />
                }
                <div className="flex-1 min-w-0">
                  <p className={`font-medium leading-tight ${triggered ? 'text-red-700' : 'text-gray-700'}`}>
                    {desc}
                  </p>
                  {reasoningText && (
                    <p className="text-gray-500 mt-0.5 leading-tight">{reasoningText}</p>
                  )}
                </div>
                <span className="text-gray-400 flex-shrink-0 mt-0.5">
                  {Math.round(conf * 100)}%
                </span>
              </div>
              {childEntries.map(([childId]) => renderNode(childId, depth + 1))}
            </React.Fragment>
          )
        }

        const rootEntries = Object.entries(itemReasoning)
          .filter(([_, data]) => !data.parent_id)
          .sort(([idA], [idB]) => (checklistMap[idA]?.order ?? 0) - (checklistMap[idB]?.order ?? 0))

        return (
          <div key={ruleId} className="border border-gray-200 rounded overflow-hidden">
            {/* Rule header */}
            <div className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium ${VERDICT_HEADER_STYLES[verdict] || 'bg-gray-50 text-gray-600'}`}>
              <span className="flex-1 truncate">{String(ruleData.rule_title || ruleId)}</span>
              <span className="uppercase tracking-wider opacity-75 flex-shrink-0">{verdict}</span>
              <span className="opacity-60 flex-shrink-0">{Math.round(confidence * 100)}%</span>
            </div>

            {/* Checklist items — hierarchical */}
            {rootEntries.length > 0 && (
              <div>
                {rootEntries.map(([itemId]) => renderNode(itemId, 0))}
              </div>
            )}

            {rootEntries.length === 0 && (
              <div className="px-3 py-1.5 text-xs text-gray-400 border-t border-gray-100">
                No checklist items evaluated.
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Batch Import Modal ─────────────────────────────────────────────────────────

type ImportFormat = 'text' | 'json'

function parseTextBlocks(raw: string): BatchImportRuleItem[] {
  const blocks = raw.split(/^---$/m).map(b => b.trim()).filter(Boolean)
  return blocks.map((block, i) => {
    const lines = block.split('\n')
    const title = lines[0].replace(/^#+\s*/, '').trim()
    const text = lines.slice(1).join('\n').trim() || title
    return { title, text, priority: i }
  })
}

function parseJsonRules(raw: string): BatchImportRuleItem[] {
  const parsed = JSON.parse(raw)
  if (!Array.isArray(parsed)) throw new Error('Expected a JSON array')
  return parsed.map((item: Record<string, unknown>, i: number) => {
    if (!item.title || !item.text) throw new Error(`Item ${i} missing title or text`)
    return { title: String(item.title), text: String(item.text), priority: i }
  })
}

function BatchImportModal({
  communityId,
  onClose,
  onSuccess,
}: {
  communityId: string
  onClose: () => void
  onSuccess: () => void
}) {
  const [format, setFormat] = useState<ImportFormat>('text')
  const [raw, setRaw] = useState('')
  const [parseError, setParseError] = useState<string | null>(null)
  const [preview, setPreview] = useState<BatchImportRuleItem[] | null>(null)
  const [result, setResult] = useState<BatchImportResponse | null>(null)
  const [importing, setImporting] = useState(false)

  const handleParse = () => {
    setParseError(null)
    try {
      const items = format === 'text' ? parseTextBlocks(raw) : parseJsonRules(raw)
      if (items.length === 0) throw new Error('No rules found — check your formatting.')
      setPreview(items)
    } catch (e) {
      setParseError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleImport = async () => {
    if (!preview) return
    setImporting(true)
    try {
      const res = await batchImportRules(communityId, preview)
      setResult(res)
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const textPlaceholder = `# No Self-Promotion
No self-promotion or spam. Posts should contribute to the community, not advertise products or services.
---
# Be Respectful
Be respectful to other members. No personal attacks, harassment, or hate speech.
---
# Stay On Topic
Posts must be relevant to the community topic. Off-topic posts will be removed.`

  const jsonPlaceholder = `[
  { "title": "No Self-Promotion", "text": "No self-promotion or spam..." },
  { "title": "Be Respectful", "text": "Be respectful to other members..." }
]`

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="card p-6 w-full max-w-2xl max-h-[90vh] flex flex-col">
        <h2 className="text-lg font-semibold mb-1">Batch Import Rules</h2>
        <p className="text-sm text-gray-500 mb-4">
          Import multiple rules at once. All rules are triaged concurrently — actionable ones will be compiled in the background.
        </p>

        {!result ? (
          <>
            <div className="flex gap-2 mb-3">
              {(['text', 'json'] as ImportFormat[]).map(f => (
                <button
                  key={f}
                  className={`text-xs px-3 py-1 rounded border transition-colors ${format === f
                    ? 'bg-indigo-600 text-white border-indigo-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                    }`}
                  onClick={() => { setFormat(f); setPreview(null); setParseError(null) }}
                >
                  {f === 'text' ? 'Text blocks (---  separated)' : 'JSON array'}
                </button>
              ))}
            </div>

            <textarea
              className="flex-1 min-h-48 border border-gray-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none mb-3"
              value={raw}
              onChange={e => { setRaw(e.target.value); setPreview(null); setParseError(null) }}
              placeholder={format === 'text' ? textPlaceholder : jsonPlaceholder}
            />

            {parseError && (
              <p className="text-xs text-red-600 mb-3">⚠ {parseError}</p>
            )}

            {preview && (
              <div className="mb-3 border border-gray-200 rounded overflow-hidden max-h-48 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 sticky top-0">
                    <tr>
                      <th className="text-left px-3 py-2 text-gray-500 font-medium w-6">#</th>
                      <th className="text-left px-3 py-2 text-gray-500 font-medium">Title</th>
                      <th className="text-left px-3 py-2 text-gray-500 font-medium">Text preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.map((item, i) => (
                      <tr key={i} className="border-t border-gray-100">
                        <td className="px-3 py-2 text-gray-400">{i + 1}</td>
                        <td className="px-3 py-2 font-medium text-gray-800 whitespace-nowrap">{item.title}</td>
                        <td className="px-3 py-2 text-gray-500 truncate max-w-xs">{item.text.slice(0, 100)}{item.text.length > 100 ? '…' : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex gap-2 justify-end">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              {!preview ? (
                <button className="btn-secondary" onClick={handleParse} disabled={!raw.trim()}>
                  Preview ({format === 'text' ? 'parse blocks' : 'parse JSON'})
                </button>
              ) : (
                <>
                  <button className="btn-secondary" onClick={() => setPreview(null)}>Back</button>
                  <button className="btn-primary" onClick={handleImport} disabled={importing}>
                    {importing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                    {importing ? 'Importing…' : `Import ${preview.length} rule${preview.length !== 1 ? 's' : ''}`}
                  </button>
                </>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex gap-4 mb-4">
              <div className="flex-1 bg-green-50 border border-green-200 rounded p-3 text-center">
                <p className="text-2xl font-bold text-green-700">{result.actionable_count}</p>
                <p className="text-xs text-green-600">actionable (compiling…)</p>
              </div>
              <div className="flex-1 bg-gray-50 border border-gray-200 rounded p-3 text-center">
                <p className="text-2xl font-bold text-gray-600">{result.skipped_count}</p>
                <p className="text-xs text-gray-500">non-actionable</p>
              </div>
              <div className="flex-1 bg-blue-50 border border-blue-200 rounded p-3 text-center">
                <p className="text-2xl font-bold text-blue-700">{result.total}</p>
                <p className="text-xs text-blue-600">total imported</p>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto border border-gray-200 rounded mb-4">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    <th className="text-left px-3 py-2 text-gray-500 font-medium">Title</th>
                    <th className="text-left px-3 py-2 text-gray-500 font-medium">Type</th>
                    <th className="text-left px-3 py-2 text-gray-500 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {result.imported.map(({ rule, triage_error }) => (
                    <tr key={rule.id} className="border-t border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-800">{rule.title}</td>
                      <td className="px-3 py-2">
                        <span className={`badge ${RULE_TYPE_COLORS[rule.rule_type] || 'badge-gray'}`}>
                          {rule.rule_type}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        {triage_error ? (
                          <span className="flex items-center gap-1 text-red-500">
                            <XCircle size={12} /> triage failed
                          </span>
                        ) : rule.rule_type === 'actionable' ? (
                          <span className="flex items-center gap-1 text-green-600">
                            <CheckCircle size={12} /> compiling…
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-gray-400">
                            <CheckCircle size={12} /> saved
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end">
              <button className="btn-primary" onClick={onSuccess}>Done</button>
            </div>
          </>
        )}
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
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={loading || !title.trim() || !text.trim()}
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : null}
              {loading ? 'Creating...' : 'Create Rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
