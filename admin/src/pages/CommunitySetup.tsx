import { useState, useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, Check, Plus, Loader2, AlertTriangle, ThumbsUp, ThumbsDown, SkipForward, ChevronDown, ChevronRight } from 'lucide-react'
import {
  createCommunity,
  generateCommunityContext,
  updateCommunityContext,
  crawlContextSamples,
  listRules,
  createRule,
  batchImportRules,
  fetchRedditRules,
  getSetupStatus,
  acceptSuggestionWithLabel,
  dismissSuggestion,
  revertSuggestion,
  populateQueue,
  listScenarios,
  createCommunityFromScenario,
  CommunityContext,
  CommunityContextDimension,
  ContextSamples,
  Rule,
  BorderlineItem,
} from '../api/client'
import ContextDimensionsView from '../components/ContextDimensionsView'

interface CommunitySetupProps {
  onCommunityChange: (id: string) => void
}

const STEPS = [
  { n: 1, label: 'Community' },
  { n: 2, label: 'Sample Posts' },
  { n: 3, label: 'Context' },
  { n: 4, label: 'Rules' },
  { n: 5, label: 'Calibrate' },
]

export default function CommunitySetup({ onCommunityChange }: CommunitySetupProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5>(1)
  const [communityId, setCommunityId] = useState('')

  // ── Step 1 ────────────────────────────────────────────────────────────────
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('reddit')
  const [step1Error, setStep1Error] = useState('')
  const step1Mutation = useMutation({
    mutationFn: () => createCommunity({ name: name.trim(), platform }),
    onSuccess: (comm) => {
      setCommunityId(comm.id)
      queryClient.invalidateQueries({ queryKey: ['communities'] })
      setStep(2)
    },
    onError: () => setStep1Error('Failed to create community. Please try again.'),
  })

  // ── Scenario shortcut (user-study setup) ──────────────────────────────────
  const { data: scenarios = [] } = useQuery({
    queryKey: ['scenarios'],
    queryFn: listScenarios,
  })
  const [selectedScenario, setSelectedScenario] = useState('')
  const [scenarioNameOverride, setScenarioNameOverride] = useState('')
  const [scenarioError, setScenarioError] = useState('')
  const scenarioMutation = useMutation({
    mutationFn: (args: { filename: string; communityName?: string }) =>
      createCommunityFromScenario(args.filename, args.communityName),
    onSuccess: (resp) => {
      setCommunityId(resp.community_id)
      setName(resp.community_name)
      setPlatform('hypothetical')
      queryClient.invalidateQueries({ queryKey: ['communities'] })
      // Jump straight to the calibrate/progress step — context, rules, and queue
      // posts are all set up in the background by the from-scenario endpoint.
      setStep(5)
    },
    onError: () => setScenarioError('Failed to set up community from scenario.'),
  })

  // ── Step 2 ────────────────────────────────────────────────────────────────
  const crawlTriggered = useRef(false)
  const [contextSamples, setContextSamples] = useState<ContextSamples | null>(null)

  const contextSamplesMutation = useMutation({
    mutationFn: () => crawlContextSamples(communityId),
    onSuccess: (data) => {
      setContextSamples(data.context_samples)
    },
  })

  useEffect(() => {
    if (step === 2 && communityId && platform === 'reddit' && !crawlTriggered.current) {
      crawlTriggered.current = true
      contextSamplesMutation.mutate()
    }
  }, [step, communityId, platform])

  // ── Step 3 ────────────────────────────────────────────────────────────────
  const [communityContext, setCommunityContext] = useState<CommunityContext | null>(null)
  const [contextError, setContextError] = useState('')
  const contextTriggered = useRef(false)

  const contextMutation = useMutation({
    mutationFn: () => generateCommunityContext(communityId),
    onSuccess: (data) => {
      setCommunityContext(data.community_context)
      setContextError('')
    },
    onError: () => setContextError('Failed to generate community context.'),
  })

  const contextUpdateMutation = useMutation({
    mutationFn: (data: Partial<CommunityContext>) => updateCommunityContext(communityId, data),
    onSuccess: (data) => {
      setCommunityContext(data)
    },
  })

  // Auto-trigger context generation on entering step 3
  useEffect(() => {
    if (step === 3 && communityId && !contextTriggered.current && !communityContext) {
      contextTriggered.current = true
      contextMutation.mutate()
    }
  }, [step, communityId])

  // ── Step 4 ────────────────────────────────────────────────────────────────
  const { data: rules = [], refetch: refetchRules } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const [ruleTab, setRuleTab] = useState<'manual' | 'reddit' | 'markdown' | 'json'>('manual')
  const [ruleTitle, setRuleTitle] = useState('')
  const [ruleText, setRuleText] = useState('')
  const [ruleError, setRuleError] = useState('')

  const addRuleMutation = useMutation({
    mutationFn: () => createRule(communityId, { title: ruleTitle.trim(), text: ruleText.trim() }),
    onSuccess: () => {
      refetchRules()
      setRuleTitle('')
      setRuleText('')
      setRuleError('')
    },
    onError: () => setRuleError('Failed to add rule.'),
  })

  // -- JSON import state --
  const [importJson, setImportJson] = useState('')
  const [importError, setImportError] = useState('')
  const [importResult, setImportResult] = useState<string>('')

  const importMutation = useMutation({
    mutationFn: () => {
      let parsed: { title: string; text: string }[]
      try {
        parsed = JSON.parse(importJson)
      } catch {
        throw new Error('Invalid JSON')
      }
      if (!Array.isArray(parsed)) throw new Error('Expected a JSON array')
      return batchImportRules(communityId, parsed)
    },
    onSuccess: (result) => {
      refetchRules()
      setImportJson('')
      setImportError('')
      const nonActionable = result.total - result.actionable_count
      setImportResult(
        nonActionable > 0
          ? `Added ${result.total} rules (${result.actionable_count} actionable, ${nonActionable} informational/procedural).`
          : `Added ${result.total} actionable rules.`
      )
    },
    onError: (err: unknown) => {
      const axiosDetail = (err as any)?.response?.data?.detail
      setImportError(
        typeof axiosDetail === 'string'
          ? axiosDetail
          : (err as Error)?.message || 'Import failed. Check the JSON format.'
      )
      setImportResult('')
    },
  })

  // -- Reddit import state --
  const [redditSub, setRedditSub] = useState('')
  const [redditPreview, setRedditPreview] = useState<{ title: string; text: string }[] | null>(null)
  const [redditError, setRedditError] = useState('')
  const [redditResult, setRedditResult] = useState('')

  // Auto-fill subreddit from community name
  useEffect(() => {
    if (communityId && name) {
      const m = name.trim().match(/^r\/(.+)$/i)
      if (m) setRedditSub(m[1])
    }
  }, [communityId, name])

  const fetchRedditMutation = useMutation({
    mutationFn: async () => {
      const data = await fetchRedditRules(redditSub.trim())
      if (!data.rules || data.rules.length === 0) {
        return { rules: data.rules, importResult: null }
      }
      const importResult = await batchImportRules(communityId, data.rules)
      return { rules: data.rules, importResult }
    },
    onSuccess: ({ rules: fetched, importResult }) => {
      setRedditPreview(fetched)
      setRedditError('')
      if (importResult) {
        refetchRules()
        const nonActionable = importResult.total - importResult.actionable_count
        setRedditResult(
          nonActionable > 0
            ? `Imported ${importResult.total} rules (${importResult.actionable_count} actionable, ${nonActionable} informational/procedural).`
            : `Imported ${importResult.total} actionable rules.`
        )
      } else {
        setRedditResult('')
      }
    },
    onError: (err: unknown) => {
      const axiosDetail = (err as any)?.response?.data?.detail
      setRedditError(typeof axiosDetail === 'string' ? axiosDetail : 'Failed to fetch rules from Reddit.')
      setRedditPreview(null)
    },
  })

  // -- Markdown import state --
  const [importMarkdown, setImportMarkdown] = useState('')
  const [markdownError, setMarkdownError] = useState('')
  const [markdownResult, setMarkdownResult] = useState('')

  const markdownImportMutation = useMutation({
    mutationFn: () => {
      const blocks = importMarkdown.split(/^---$/m).map(b => b.trim()).filter(Boolean)
      const parsed: { title: string; text: string }[] = []
      for (const block of blocks) {
        const titleMatch = block.match(/^#+\s+(.+)$/m)
        if (!titleMatch) throw new Error(`Block missing a title (# heading):\n"${block.slice(0, 60)}..."`)
        const title = titleMatch[1].trim()
        const text = block.replace(/^#+\s+.+$/m, '').trim()
        parsed.push({ title, text: text || title })
      }
      if (parsed.length === 0) throw new Error('No rules found. Use # for titles and --- to separate rules.')
      return batchImportRules(communityId, parsed)
    },
    onSuccess: (result) => {
      refetchRules()
      setImportMarkdown('')
      setMarkdownError('')
      const nonActionable = result.total - result.actionable_count
      setMarkdownResult(
        nonActionable > 0
          ? `Imported ${result.total} rules (${result.actionable_count} actionable, ${nonActionable} informational/procedural).`
          : `Imported ${result.total} actionable rules.`
      )
    },
    onError: (err: unknown) => {
      const axiosDetail = (err as any)?.response?.data?.detail
      setMarkdownError(
        typeof axiosDetail === 'string'
          ? axiosDetail
          : (err as Error)?.message || 'Import failed.'
      )
      setMarkdownResult('')
    },
  })

  // ── Finish ────────────────────────────────────────────────────────────────
  const populateMutation = useMutation({
    mutationFn: () => populateQueue(communityId),
  })

  const handleFinish = () => {
    // Trigger decision queue population in background
    if (platform === 'reddit') {
      populateMutation.mutate()
    }
    onCommunityChange(communityId)
    navigate('/decisions')
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center gap-3">
        <Shield size={22} className="text-indigo-500" />
        <span className="font-semibold text-gray-900">AutoMod Agent</span>
        <span className="text-gray-400 mx-1">/</span>
        <span className="text-gray-600 text-sm">New Community Setup</span>
      </header>

      <div className="flex-1 flex flex-col items-center py-10 px-4">
        {/* Step indicator */}
        <div className="flex items-center gap-0 mb-10">
          {STEPS.map((s, i) => (
            <div key={s.n} className="flex items-center">
              <div className="flex flex-col items-center">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold border-2 transition-colors ${
                    step > s.n
                      ? 'bg-indigo-600 border-indigo-600 text-white'
                      : step === s.n
                      ? 'border-indigo-600 text-indigo-600 bg-white'
                      : 'border-gray-300 text-gray-400 bg-white'
                  }`}
                >
                  {step > s.n ? <Check size={14} /> : s.n}
                </div>
                <span className={`text-xs mt-1 font-medium ${step === s.n ? 'text-indigo-600' : step > s.n ? 'text-gray-500' : 'text-gray-400'}`}>
                  {s.label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <div className={`w-16 h-0.5 mb-5 mx-1 ${step > s.n ? 'bg-indigo-600' : 'bg-gray-200'}`} />
              )}
            </div>
          ))}
        </div>

        {/* Step panels */}
        <div className="w-full max-w-2xl">
          {/* ── Step 1 ── */}
          {step === 1 && (
            <div className="space-y-4">
              {scenarios.length > 0 && (
                <div className="card p-6 border-indigo-200 bg-indigo-50/30">
                  <h2 className="text-base font-semibold text-gray-900 mb-1">Load from scenario</h2>
                  <p className="text-xs text-gray-500 mb-4">
                    For user studies — instantly create a hypothetical community pre-loaded with rules and a moderation queue. Context is sampled from a real subreddit and cached, so repeated runs of the same scenario produce identical setups.
                  </p>
                  <div className="space-y-2">
                    <select
                      className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      value={selectedScenario}
                      onChange={e => {
                        setSelectedScenario(e.target.value)
                        const sc = scenarios.find(s => s.filename === e.target.value)
                        setScenarioNameOverride(sc?.community_name ?? '')
                        setScenarioError('')
                      }}
                    >
                      <option value="">— Select a scenario —</option>
                      {scenarios.map(s => (
                        <option key={s.filename} value={s.filename}>
                          {s.community_name} · r/{s.base_subreddit} · {s.rule_count}r/{s.queue_post_count}p {s.context_cached ? '· cached' : ''}
                        </option>
                      ))}
                    </select>
                    <div className="flex gap-2">
                      <input
                        className="flex-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                        placeholder="Community name (defaults to the scenario's name)"
                        value={scenarioNameOverride}
                        onChange={e => setScenarioNameOverride(e.target.value)}
                        disabled={!selectedScenario}
                      />
                      <button
                        className="btn-primary whitespace-nowrap"
                        disabled={!selectedScenario || scenarioMutation.isPending}
                        onClick={() => scenarioMutation.mutate({
                          filename: selectedScenario,
                          communityName: scenarioNameOverride,
                        })}
                      >
                        {scenarioMutation.isPending ? <><Loader2 size={14} className="animate-spin mr-1.5 inline" />Setting up…</> : 'Use scenario →'}
                      </button>
                    </div>
                  </div>
                  {scenarioError && <p className="text-sm text-red-600 mt-2">{scenarioError}</p>}
                </div>
              )}

              <div className="card p-8">
                <h2 className="text-xl font-bold text-gray-900 mb-1">Create your community</h2>
              <p className="text-sm text-gray-500 mb-6">Give your community a name and choose the platform it belongs to.</p>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Community name</label>
                  <input
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    placeholder="e.g., r/programming"
                    value={name}
                    onChange={e => { setName(e.target.value); setStep1Error('') }}
                    autoFocus
                    onKeyDown={e => { if (e.key === 'Enter' && name.trim()) step1Mutation.mutate() }}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Platform</label>
                  <select
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    value={platform}
                    onChange={e => setPlatform(e.target.value)}
                  >
                    <option value="reddit">Reddit</option>
                    <option value="chatroom">Chatroom</option>
                    <option value="forum">Forum</option>
                  </select>
                </div>
                {step1Error && <p className="text-sm text-red-600">{step1Error}</p>}
              </div>
              <div className="mt-6 flex justify-end">
                <button
                  className="btn-primary"
                  disabled={!name.trim() || step1Mutation.isPending}
                  onClick={() => step1Mutation.mutate()}
                >
                  {step1Mutation.isPending ? <><Loader2 size={14} className="animate-spin mr-1.5 inline" />Creating...</> : 'Continue →'}
                </button>
              </div>
            </div>
            </div>
          )}

          {/* ── Step 2 ── */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="card p-8">
                <h2 className="text-xl font-bold text-gray-900 mb-1">Sample posts</h2>
                <p className="text-sm text-gray-500 mb-6">
                  {platform === 'reddit'
                    ? 'The system samples posts across different activity categories — hot, top, controversial, ignored, and comments — to build a well-rounded picture of the community. Review the results below before proceeding to context generation.'
                    : 'Context sampling is only available for Reddit communities. You can proceed to add rules directly.'}
                </p>

                {contextSamplesMutation.isPending && (
                  <div className="flex items-center gap-3 text-sm text-gray-600 bg-indigo-50 rounded-lg px-4 py-3">
                    <Loader2 size={16} className="animate-spin text-indigo-500 flex-shrink-0" />
                    <span>Sampling posts from the subreddit (hot, top, controversial, ignored, comments)...</span>
                  </div>
                )}
                {contextSamplesMutation.isError && (
                  <div className="flex items-start gap-2 text-sm text-amber-700 bg-amber-50 rounded-md px-3 py-2">
                    <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                    <span>Could not sample posts from the subreddit. You can retry or skip this step.</span>
                  </div>
                )}
              </div>

              {contextSamples && <ContextSamplesPreview samples={contextSamples} onRecrawl={() => contextSamplesMutation.mutate()} isRecrawling={contextSamplesMutation.isPending} />}

              <StepNav
                onBack={() => setStep(1)}
                onContinue={() => setStep(3)}
                continueLabel="Continue →"
                continueDisabled={contextSamplesMutation.isPending}
              />
            </div>
          )}

          {/* ── Step 3 ── */}
          {step === 3 && (
            <div className="card p-8 space-y-5">
              <div>
                <h2 className="text-xl font-bold text-gray-900 mb-1">Community context</h2>
                <p className="text-sm text-gray-500">
                  The community context captures what this community is about across four dimensions — purpose, participants, stakes, and tone — generated from the sampled posts.
                </p>
              </div>

              {contextMutation.isPending && (
                <div className="flex items-center gap-3 text-sm text-gray-600 bg-indigo-50 rounded-lg px-4 py-3">
                  <Loader2 size={16} className="animate-spin text-indigo-500 flex-shrink-0" />
                  <span>Generating community context...</span>
                </div>
              )}

              {contextError && (
                <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 rounded-md px-3 py-2">
                  <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                  {contextError}
                </div>
              )}

              {communityContext && (
                <ContextDimensionsView
                  context={communityContext}
                  communityId={communityId}
                  onRegenerate={() => { contextTriggered.current = false; contextMutation.mutate() }}
                  isRegenerating={contextMutation.isPending}
                  onSaveDimension={async (key: keyof CommunityContext, dim: CommunityContextDimension) => {
                    await contextUpdateMutation.mutateAsync({ [key]: dim })
                  }}
                  isSaving={contextUpdateMutation.isPending}
                />
              )}

              <StepNav
                onBack={() => setStep(2)}
                onContinue={() => setStep(4)}
                continueLabel="Continue →"
                continueDisabled={contextMutation.isPending}
              />
            </div>
          )}

          {/* ── Step 4 ── */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="card p-8">
                <h2 className="text-xl font-bold text-gray-900 mb-1">Add moderation rules</h2>
                <p className="text-sm text-gray-500 mb-6">
                  Rules define what is and isn't allowed in your community. You can add them manually, import from Reddit, or paste rules as Markdown or JSON. Rules can also be added later in the Rules editor.
                </p>

                {/* Tab toggle */}
                <div className="flex gap-2 mb-5">
                  {([
                    { key: 'manual' as const, label: 'Manual' },
                    { key: 'reddit' as const, label: 'Reddit' },
                    { key: 'markdown' as const, label: 'Markdown' },
                    { key: 'json' as const, label: 'JSON' },
                  ]).map(t => (
                    <button
                      key={t.key}
                      className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${ruleTab === t.key ? 'bg-indigo-600 text-white border-indigo-600' : 'border-gray-300 text-gray-600 hover:border-indigo-400'}`}
                      onClick={() => setRuleTab(t.key)}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>

                {ruleTab === 'manual' && (
                  <div className="space-y-3">
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Rule title (e.g., No personal attacks)"
                      value={ruleTitle}
                      onChange={e => setRuleTitle(e.target.value)}
                    />
                    <textarea
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                      placeholder="Rule text: write the full rule as you'd present it to your community"
                      rows={4}
                      value={ruleText}
                      onChange={e => setRuleText(e.target.value)}
                    />
                    {ruleError && <p className="text-sm text-red-600">{ruleError}</p>}
                    <button
                      className="btn-secondary flex items-center gap-1.5 text-sm"
                      disabled={!ruleTitle.trim() || !ruleText.trim() || addRuleMutation.isPending}
                      onClick={() => addRuleMutation.mutate()}
                    >
                      {addRuleMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                      Save rule
                    </button>
                  </div>
                )}

                {ruleTab === 'reddit' && (
                  <div className="space-y-3">
                    <p className="text-xs text-gray-500">
                      Fetch rules directly from a subreddit's public rules page.
                    </p>
                    <div className="flex gap-2">
                      <div className="flex items-center border border-gray-300 rounded px-3 py-2 text-sm focus-within:ring-2 focus-within:ring-indigo-500 flex-1">
                        <span className="text-gray-400 mr-1">r/</span>
                        <input
                          className="flex-1 outline-none bg-transparent"
                          placeholder="subreddit"
                          value={redditSub}
                          onChange={e => { setRedditSub(e.target.value); setRedditError(''); setRedditResult('') }}
                          onKeyDown={e => { if (e.key === 'Enter' && redditSub.trim()) fetchRedditMutation.mutate() }}
                        />
                      </div>
                      <button
                        className="btn-primary flex items-center gap-1.5 text-sm whitespace-nowrap"
                        disabled={!redditSub.trim() || fetchRedditMutation.isPending}
                        onClick={() => fetchRedditMutation.mutate()}
                      >
                        {fetchRedditMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                        Fetch & Import
                      </button>
                    </div>
                    {redditError && <p className="text-sm text-red-600">{redditError}</p>}
                    {redditResult && (
                      <div className="space-y-1">
                        <p className="text-sm text-green-700 bg-green-50 rounded px-3 py-2">{redditResult}</p>
                        <p className="text-xs text-gray-400">Actionable rules are compiled into checklists in the background.</p>
                      </div>
                    )}
                    {redditPreview && redditPreview.length > 0 && (
                      <div className="space-y-2">
                        <p className="text-xs font-medium text-gray-600">Imported {redditPreview.length} rules:</p>
                        <div className="rounded border border-gray-200 divide-y divide-gray-100 max-h-64 overflow-y-auto">
                          {redditPreview.map((r, i) => (
                            <div key={i} className="px-3 py-2">
                              <div className="text-sm font-medium text-gray-800">{r.title}</div>
                              {r.text && r.text !== r.title && (
                                <div className="text-xs text-gray-500 mt-0.5 line-clamp-2">{r.text}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {redditPreview && redditPreview.length === 0 && (
                      <p className="text-sm text-amber-600">No rules found for this subreddit.</p>
                    )}
                  </div>
                )}

                {ruleTab === 'markdown' && (
                  <div className="space-y-3">
                    <p className="text-xs text-gray-500">
                      Use <code className="bg-gray-100 px-1 rounded">#</code> headings for rule titles and <code className="bg-gray-100 px-1 rounded">---</code> to separate rules.
                    </p>
                    <pre className="text-xs text-gray-400 bg-gray-50 rounded p-2 border border-gray-200 leading-relaxed">
{`# No spam
Do not post spam or self-promotional content.
---
# Be civil
Treat others with respect. No personal attacks.`}
                    </pre>
                    <textarea
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                      placeholder="Paste markdown rules here..."
                      rows={8}
                      value={importMarkdown}
                      onChange={e => { setImportMarkdown(e.target.value); setMarkdownError(''); setMarkdownResult('') }}
                    />
                    {markdownError && <p className="text-sm text-red-600">{markdownError}</p>}
                    {markdownResult && (
                      <div className="space-y-1">
                        <p className="text-sm text-green-700 bg-green-50 rounded px-3 py-2">{markdownResult}</p>
                        <p className="text-xs text-gray-400">Actionable rules are compiled into checklists in the background.</p>
                      </div>
                    )}
                    <button
                      className="btn-secondary flex items-center gap-1.5 text-sm"
                      disabled={!importMarkdown.trim() || markdownImportMutation.isPending}
                      onClick={() => markdownImportMutation.mutate()}
                    >
                      {markdownImportMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                      Import rules
                    </button>
                  </div>
                )}

                {ruleTab === 'json' && (
                  <div className="space-y-3">
                    <p className="text-xs text-gray-500">
                      Paste a JSON array of rules. Each rule needs a <code className="bg-gray-100 px-1 rounded">title</code> and <code className="bg-gray-100 px-1 rounded">text</code>.
                    </p>
                    <pre className="text-xs text-gray-400 bg-gray-50 rounded p-2 border border-gray-200 leading-relaxed">
{`[
  { "title": "No spam", "text": "Do not post spam or self-promotional content." },
  { "title": "Be civil", "text": "Treat others with respect. No personal attacks." }
]`}
                    </pre>
                    <textarea
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                      placeholder="Paste JSON here..."
                      rows={6}
                      value={importJson}
                      onChange={e => { setImportJson(e.target.value); setImportError(''); setImportResult('') }}
                    />
                    {importError && <p className="text-sm text-red-600">{importError}</p>}
                    {importResult && (
                      <div className="space-y-1">
                        <p className="text-sm text-green-700 bg-green-50 rounded px-3 py-2">{importResult}</p>
                        <p className="text-xs text-gray-400">Actionable rules are compiled into checklists in the background.</p>
                      </div>
                    )}
                    <button
                      className="btn-secondary flex items-center gap-1.5 text-sm"
                      disabled={!importJson.trim() || importMutation.isPending}
                      onClick={() => importMutation.mutate()}
                    >
                      {importMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                      Import rules
                    </button>
                  </div>
                )}
              </div>

              {/* Rules list */}
              {rules.length > 0 && (
                <div className="card p-4">
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                    Added rules ({rules.length})
                  </h3>
                  <div className="space-y-1">
                    {rules.map((rule: Rule, i: number) => (
                      <div key={rule.id} className="flex items-center gap-3 py-1.5">
                        <span className="text-xs text-gray-400 w-5 text-right flex-shrink-0">{i + 1}</span>
                        <span className="text-sm text-gray-800 flex-1">{rule.title}</span>
                        {rule.rule_type && (
                          <span className="text-xs px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">{rule.rule_type}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <StepNav
                onBack={() => setStep(3)}
                onContinue={() => setStep(5)}
                continueLabel="Continue →"
              />
            </div>
          )}

          {/* ── Step 5 ── */}
          {step === 5 && (
            <CalibrateStep
              communityId={communityId}
              platform={platform}
              onBack={() => setStep(4)}
              onFinish={handleFinish}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function CalibrateStep({
  communityId,
  platform,
  onBack,
  onFinish,
}: {
  communityId: string
  platform: string
  onBack: () => void
  onFinish: () => void
}) {
  // suggestionId -> chosen label ('compliant' | 'violating' | 'skipped')
  const [resolutions, setResolutions] = useState<Record<string, string>>({})
  const resolved = useMemo(() => new Set(Object.keys(resolutions)), [resolutions])
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(null)

  const { data: status, refetch } = useQuery({
    queryKey: ['setup-status', communityId],
    queryFn: () => getSetupStatus(communityId),
    enabled: !!communityId,
    refetchInterval: (query) => {
      const d = query.state.data
      if (!d) return 2000
      return d.compiled_count < d.actionable_total ? 2000 : false
    },
  })

  // Once compilation finishes, do one final refetch to pick up all borderline examples
  const [finalFetched, setFinalFetched] = useState(false)
  const compilationDone = status && status.compiled_count >= status.actionable_total
  useEffect(() => {
    if (compilationDone && !finalFetched) {
      setFinalFetched(true)
      refetch()
    }
  }, [compilationDone, finalFetched, refetch])

  const resolveMutation = useMutation({
    mutationFn: ({ suggestionId, label }: { suggestionId: string; label: string }) =>
      acceptSuggestionWithLabel(suggestionId, label),
    onSuccess: (_, vars) => {
      setResolutions(prev => ({ ...prev, [vars.suggestionId]: vars.label }))
    },
  })

  const skipMutation = useMutation({
    mutationFn: (suggestionId: string) => dismissSuggestion(suggestionId),
    onSuccess: (_, suggestionId) => {
      setResolutions(prev => ({ ...prev, [suggestionId]: 'skipped' }))
    },
  })

  const undoMutation = useMutation({
    mutationFn: (suggestionId: string) => revertSuggestion(suggestionId),
    onSuccess: (_, suggestionId) => {
      setResolutions(prev => {
        const next = { ...prev }
        delete next[suggestionId]
        return next
      })
    },
  })

  const isCompiling = !status || status.compiled_count < status.actionable_total
  const progressPct = status && status.actionable_total > 0
    ? Math.round((status.compiled_count / status.actionable_total) * 100)
    : 0

  const borderlineItems: BorderlineItem[] = status?.borderline_examples ?? []
  const pending = borderlineItems.filter(b => !resolved.has(b.suggestion_id))
  const allResolved = borderlineItems.length > 0 && pending.length === 0

  // Group borderline examples by rule for collapsible sections
  const groupedByRule = useMemo(() => {
    const groups: [string, string, BorderlineItem[]][] = [] // [ruleId, ruleTitle, items]
    const seen = new Map<string, BorderlineItem[]>()
    for (const item of borderlineItems) {
      if (!seen.has(item.rule_id)) {
        const arr: BorderlineItem[] = []
        seen.set(item.rule_id, arr)
        groups.push([item.rule_id, item.rule_title, arr])
      }
      seen.get(item.rule_id)!.push(item)
    }
    return groups
  }, [borderlineItems])

  // Auto-expand first group, or next unresolved group when current is done
  useEffect(() => {
    if (groupedByRule.length === 0) return
    if (expandedRuleId === null) {
      setExpandedRuleId(groupedByRule[0][0])
      return
    }
    // Check if current expanded group is fully resolved
    const currentGroup = groupedByRule.find(([id]) => id === expandedRuleId)
    if (currentGroup) {
      const allDone = currentGroup[2].every(i => resolved.has(i.suggestion_id))
      if (allDone) {
        const nextGroup = groupedByRule.find(([id, , items]) =>
          id !== expandedRuleId && items.some(i => !resolved.has(i.suggestion_id))
        )
        if (nextGroup) setExpandedRuleId(nextGroup[0])
      }
    }
  }, [resolved, groupedByRule, expandedRuleId])

  return (
    <div className="space-y-4">
      <div className="card p-8">
        <h2 className="text-xl font-bold text-gray-900 mb-1">Calibrate edge cases</h2>
        <p className="text-sm text-gray-500 mb-6">
          For each borderline example below, decide whether it should be considered <strong>compliant</strong> (allowed) or <strong>violating</strong> (should be removed). This helps the system understand where your community draws the line.
        </p>

        {/* Compilation progress bar */}
        {isCompiling && (
          <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2 text-sm text-gray-600">
                <Loader2 size={14} className="animate-spin text-indigo-500" />
                <span className="font-medium">Compiling rules...</span>
              </div>
              {status && (
                <span className="text-xs text-gray-400">
                  {status.compiled_count} / {status.actionable_total}
                </span>
              )}
            </div>
            <div className="w-full bg-gray-200 rounded-full h-2.5 overflow-hidden">
              <div
                className="bg-indigo-600 h-2.5 rounded-full transition-all duration-500 ease-out"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <p className="text-xs text-gray-400 mt-1.5">
              {progressPct}% complete
            </p>
          </div>
        )}

        {/* Compilation done indicator */}
        {!isCompiling && status && status.actionable_total > 0 && (
          <div className="flex items-center gap-2 text-sm text-green-700 bg-green-50 rounded-lg px-4 py-2.5 mb-6">
            <Check size={16} className="flex-shrink-0" />
            <span>All {status.actionable_total} rules compiled successfully.</span>
          </div>
        )}

        {/* Borderline examples */}
        {!isCompiling && borderlineItems.length === 0 && (
          <div className="text-sm text-gray-500 bg-gray-50 rounded-lg px-4 py-3">
            No borderline examples were generated. You can always add examples later from the rule editor.
          </div>
        )}

        {!isCompiling && borderlineItems.length > 0 && (
          <div className="space-y-3">
            <div className="text-xs text-gray-400 font-medium">
              {pending.length} of {borderlineItems.length} remaining
            </div>
            {groupedByRule.map(([ruleId, ruleTitle, items]) => {
              const groupResolved = items.filter(i => resolved.has(i.suggestion_id)).length
              const isExpanded = expandedRuleId === ruleId
              const groupDone = groupResolved === items.length

              return (
                <div key={ruleId} className={`rounded-lg border ${groupDone ? 'border-gray-200' : 'border-gray-300'}`}>
                  <button
                    className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-50 transition-colors rounded-t-lg"
                    onClick={() => setExpandedRuleId(isExpanded ? null : ruleId)}
                  >
                    {isExpanded ? <ChevronDown size={14} className="text-gray-400 flex-shrink-0" /> : <ChevronRight size={14} className="text-gray-400 flex-shrink-0" />}
                    <span className="text-sm font-medium text-gray-900 flex-1 truncate">{ruleTitle}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${groupDone ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      {groupResolved}/{items.length}
                    </span>
                    {groupDone && <Check size={14} className="text-green-500 flex-shrink-0" />}
                  </button>

                  {isExpanded && (
                    <div className="border-t border-gray-200 divide-y divide-gray-100">
                      {items.map((item) => {
                        const chosen = resolutions[item.suggestion_id]
                        const done = !!chosen
                        const content = item.content as Record<string, unknown>
                        const title = (content?.title as string) || (content?.content as any)?.title || '(untitled)'
                        const body = (content?.body as string) || (content?.content as any)?.body || ''
                        const busy = (resolveMutation.isPending || skipMutation.isPending || undoMutation.isPending) &&
                          (resolveMutation.variables?.suggestionId === item.suggestion_id ||
                           skipMutation.variables === item.suggestion_id ||
                           undoMutation.variables === item.suggestion_id)

                        const handlePick = (label: 'compliant' | 'violating') => {
                          if (chosen === label) {
                            // Toggling the already-selected label off — revert.
                            undoMutation.mutate(item.suggestion_id)
                          } else if (chosen) {
                            // Switching label — revert first, then re-pick.
                            undoMutation.mutate(item.suggestion_id, {
                              onSuccess: () => resolveMutation.mutate({ suggestionId: item.suggestion_id, label }),
                            })
                          } else {
                            resolveMutation.mutate({ suggestionId: item.suggestion_id, label })
                          }
                        }

                        return (
                          <div
                            key={item.suggestion_id}
                            className={`px-4 py-3 transition-colors ${done ? 'bg-gray-50' : 'bg-white'}`}
                          >
                            <div className="flex items-start justify-between gap-3 mb-1">
                              <span className="text-sm font-medium text-gray-900 flex-1 min-w-0">{title}</span>
                              {done && <Check size={16} className="text-green-500 flex-shrink-0 mt-0.5" />}
                            </div>
                            {body && (
                              <p className="text-sm text-gray-600 line-clamp-3 mt-1">{body}</p>
                            )}
                            {item.relevance_note && (
                              <p className="text-xs text-gray-400 mt-1 italic">{item.relevance_note}</p>
                            )}

                            <div className="flex items-center gap-2 mt-3">
                              <button
                                className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded border transition-colors ${
                                  chosen === 'compliant'
                                    ? 'bg-green-100 border-green-400 text-green-800 ring-1 ring-green-300'
                                    : 'border-green-300 text-green-700 hover:bg-green-50'
                                }`}
                                disabled={busy}
                                onClick={() => handlePick('compliant')}
                                title={chosen === 'compliant' ? 'Click to undo' : 'Mark as compliant'}
                              >
                                {busy && resolveMutation.variables?.label === 'compliant' ? <Loader2 size={12} className="animate-spin" /> : <ThumbsUp size={12} />}
                                Compliant
                              </button>
                              <button
                                className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded border transition-colors ${
                                  chosen === 'violating'
                                    ? 'bg-red-100 border-red-400 text-red-800 ring-1 ring-red-300'
                                    : 'border-red-300 text-red-700 hover:bg-red-50'
                                }`}
                                disabled={busy}
                                onClick={() => handlePick('violating')}
                                title={chosen === 'violating' ? 'Click to undo' : 'Mark as violating'}
                              >
                                {busy && resolveMutation.variables?.label === 'violating' ? <Loader2 size={12} className="animate-spin" /> : <ThumbsDown size={12} />}
                                Violating
                              </button>
                              {!done && (
                                <button
                                  className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded border border-gray-200 text-gray-400 hover:bg-gray-50 transition-colors ml-auto"
                                  disabled={busy}
                                  onClick={() => skipMutation.mutate(item.suggestion_id)}
                                >
                                  <SkipForward size={12} />
                                  Skip
                                </button>
                              )}
                              {chosen === 'skipped' && (
                                <span className="ml-auto text-xs text-gray-400">Skipped</span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between pt-2">
        <button className="btn-secondary" onClick={onBack}>&larr; Back</button>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">
            {platform === 'reddit' ? 'The decision queue will be auto-populated after setup.' : ''}
          </span>
          <button
            className="btn-primary"
            disabled={isCompiling}
            onClick={onFinish}
          >
            {allResolved || borderlineItems.length === 0 ? 'Finish Setup →' : 'Skip & Finish →'}
          </button>
        </div>
      </div>
    </div>
  )
}


// ContextDimensionsView is imported from ../components/ContextDimensionsView


const CATEGORY_META: Record<string, { label: string; color: string; description: string }> = {
  hot: { label: 'Hot', color: 'text-orange-700 bg-orange-100', description: 'Current front page — typical day-to-day content' },
  top: { label: 'Top (month)', color: 'text-amber-700 bg-amber-100', description: 'What the community celebrates and upvotes' },
  controversial: { label: 'Controversial', color: 'text-purple-700 bg-purple-100', description: 'Where norms are contested' },
  ignored: { label: 'Ignored', color: 'text-gray-600 bg-gray-200', description: 'Low-score posts — content the community doesn\'t engage with' },
  comments: { label: 'Comments', color: 'text-blue-700 bg-blue-100', description: 'Top comments showing actual language and tone' },
}

function ContextSamplesPreview({
  samples,
  onRecrawl,
  isRecrawling,
}: {
  samples: ContextSamples
  onRecrawl: () => void
  isRecrawling: boolean
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const totalPosts = samples.hot.length + samples.top.length + samples.controversial.length + samples.ignored.length
  const totalComments = samples.comments.length

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Context samples ({totalPosts} posts, {totalComments} comments)
        </h3>
        <button
          className="btn-secondary flex items-center gap-1.5 text-xs py-1"
          onClick={onRecrawl}
          disabled={isRecrawling}
        >
          {isRecrawling && <Loader2 size={12} className="animate-spin" />}
          Re-sample
        </button>
      </div>
      <p className="text-xs text-gray-400">
        These posts are sampled across different activity categories to build a well-rounded community context. Click a category to preview.
      </p>
      <div className="space-y-1">
        {(['hot', 'top', 'controversial', 'ignored', 'comments'] as const).map(cat => {
          const meta = CATEGORY_META[cat]
          const items = samples[cat]
          const isOpen = expanded === cat
          return (
            <div key={cat}>
              <button
                className="w-full flex items-center gap-2 px-3 py-2 rounded hover:bg-gray-50 transition-colors text-left"
                onClick={() => setExpanded(isOpen ? null : cat)}
              >
                <span className={`text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${meta.color}`}>
                  {meta.label}
                </span>
                <span className="text-xs text-gray-400 flex-1">{meta.description}</span>
                <span className="text-xs text-gray-400 font-mono">{items.length}</span>
                <span className="text-xs text-gray-300">{isOpen ? '▾' : '▸'}</span>
              </button>
              {isOpen && items.length > 0 && (
                <div className="ml-3 border-l-2 border-gray-100 pl-3 pb-2 space-y-1.5 max-h-60 overflow-y-auto">
                  {items.map((item, i) => (
                    <div key={i} className="text-xs text-gray-600 py-1">
                      {cat === 'comments' ? (
                        <div className="flex gap-2">
                          <span className="text-gray-400 flex-shrink-0">▪ score {(item as any).score}</span>
                          <span className="line-clamp-2">{(item as any).body}</span>
                        </div>
                      ) : (
                        <div>
                          <div className="flex gap-2">
                            <span className="text-gray-400 flex-shrink-0">↑{(item as any).score}</span>
                            <span className="font-medium text-gray-700">{(item as any).title}</span>
                          </div>
                          {(item as any).body && (
                            <p className="text-gray-400 line-clamp-1 ml-8 mt-0.5">{(item as any).body}</p>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {isOpen && items.length === 0 && (
                <p className="ml-3 text-xs text-gray-400 italic py-1 pl-3">No posts in this category</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


function StepNav({
  onBack,
  onContinue,
  continueLabel,
  continueDisabled,
  skipWarning,
}: {
  onBack: () => void
  onContinue: () => void
  continueLabel: string
  continueDisabled?: boolean
  skipWarning?: string
}) {
  const [showWarning, setShowWarning] = useState(false)

  const handleContinue = () => {
    if (skipWarning && !showWarning) {
      setShowWarning(true)
      return
    }
    onContinue()
  }

  return (
    <div className="flex flex-col gap-2 pt-2">
      {showWarning && skipWarning && (
        <div className="flex items-start gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
          <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
          <span>{skipWarning} <button className="underline font-medium ml-1" onClick={onContinue}>Continue anyway</button></span>
        </div>
      )}
      <div className="flex justify-between">
        <button className="btn-secondary" onClick={onBack}>&larr; Back</button>
        <button className="btn-primary" disabled={continueDisabled} onClick={handleContinue}>{continueLabel}</button>
      </div>
    </div>
  )
}
