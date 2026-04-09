import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, Check, Trash2, Plus, Loader2, AlertTriangle } from 'lucide-react'
import {
  createCommunity,
  generateAtmosphere,
  listSamplePosts,
  addSamplePost,
  deleteSamplePost,
  importSamplePostFromUrl,
  listRules,
  createRule,
  batchImportRules,
  CommunityAtmosphere,
  CommunitySamplePost,
  Rule,
} from '../api/client'

interface CommunitySetupProps {
  onCommunityChange: (id: string) => void
}

const STEPS = [
  { n: 1, label: 'Community' },
  { n: 2, label: 'Sample Posts' },
  { n: 3, label: 'Atmosphere' },
  { n: 4, label: 'Rules' },
]

export default function CommunitySetup({ onCommunityChange }: CommunitySetupProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [communityId, setCommunityId] = useState('')
  const [atmosphere, setAtmosphere] = useState<CommunityAtmosphere | null>(null)
  const [crawledCount, setCrawledCount] = useState<number | null>(null)

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

  // ── Step 2 ────────────────────────────────────────────────────────────────
  const { data: samplePosts = [], refetch: refetchPosts } = useQuery({
    queryKey: ['sample-posts', communityId],
    queryFn: () => listSamplePosts(communityId),
    enabled: !!communityId,
  })

  const [postMode, setPostMode] = useState<'manual' | 'url'>('manual')
  // Manual form
  const [postLabel, setPostLabel] = useState<'acceptable' | 'unacceptable'>('acceptable')
  const [postTitle, setPostTitle] = useState('')
  const [postBody, setPostBody] = useState('')
  const [postNote, setPostNote] = useState('')
  const [postError, setPostError] = useState('')

  const addPostMutation = useMutation({
    mutationFn: () =>
      addSamplePost(communityId, {
        content: { content: { title: postTitle.trim(), body: postBody.trim() }, author: {}, context: {} },
        label: postLabel,
        note: postNote.trim() || undefined,
      }),
    onSuccess: () => {
      refetchPosts()
      setPostTitle('')
      setPostBody('')
      setPostNote('')
      setPostError('')
    },
    onError: () => setPostError('Failed to add post.'),
  })

  // URL form
  const [urlValue, setUrlValue] = useState('')
  const [urlLabel, setUrlLabel] = useState<'acceptable' | 'unacceptable'>('acceptable')
  const [urlNote, setUrlNote] = useState('')
  const [urlError, setUrlError] = useState('')

  const importUrlMutation = useMutation({
    mutationFn: () =>
      importSamplePostFromUrl(communityId, { url: urlValue.trim(), label: urlLabel, note: urlNote.trim() || undefined }),
    onSuccess: () => {
      refetchPosts()
      setUrlValue('')
      setUrlNote('')
      setUrlError('')
    },
    onError: () => setUrlError('Failed to import post. Check the URL and try again.'),
  })

  const deletePostMutation = useMutation({
    mutationFn: (postId: string) => deleteSamplePost(communityId, postId),
    onSuccess: () => refetchPosts(),
  })

  // ── Step 3 ────────────────────────────────────────────────────────────────
  const [atmosphereError, setAtmosphereError] = useState('')
  const generateMutation = useMutation({
    mutationFn: () => generateAtmosphere(communityId),
    onSuccess: (data) => {
      setAtmosphere(data.community.atmosphere)
      setCrawledCount(data.crawled_count)
      setAtmosphereError('')
    },
    onError: () => setAtmosphereError('Failed to generate atmosphere. Make sure you have sample posts added.'),
  })

  // ── Step 4 ────────────────────────────────────────────────────────────────
  const { data: rules = [], refetch: refetchRules } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const [ruleTab, setRuleTab] = useState<'manual' | 'import'>('manual')
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

  const handleFinish = () => {
    onCommunityChange(communityId)
    navigate('/dashboard')
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
                  {step1Mutation.isPending ? <><Loader2 size={14} className="animate-spin mr-1.5 inline" />Creating…</> : 'Continue →'}
                </button>
              </div>
            </div>
          )}

          {/* ── Step 2 ── */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="card p-8">
                <h2 className="text-xl font-bold text-gray-900 mb-1">Add sample posts</h2>
                <p className="text-sm text-gray-500 mb-6">
                  Add representative posts from your community — a mix of acceptable and unacceptable examples. These help calibrate the community atmosphere profile.
                </p>

                {/* Mode toggle */}
                <div className="flex gap-2 mb-4">
                  {(['manual', 'url'] as const).map(m => (
                    <button
                      key={m}
                      className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${postMode === m ? 'bg-indigo-600 text-white border-indigo-600' : 'border-gray-300 text-gray-600 hover:border-indigo-400'}`}
                      onClick={() => setPostMode(m)}
                    >
                      {m === 'manual' ? 'Manual' : 'Import URL'}
                    </button>
                  ))}
                </div>

                {postMode === 'manual' ? (
                  <div className="space-y-3">
                    <div className="flex gap-2">
                      {(['acceptable', 'unacceptable'] as const).map(l => (
                        <button
                          key={l}
                          onClick={() => setPostLabel(l)}
                          className={`flex-1 py-1.5 rounded border text-xs font-medium transition-colors ${postLabel === l ? (l === 'acceptable' ? 'bg-green-600 text-white border-green-600' : 'bg-red-600 text-white border-red-600') : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
                        >
                          {l.charAt(0).toUpperCase() + l.slice(1)}
                        </button>
                      ))}
                    </div>
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Post title"
                      value={postTitle}
                      onChange={e => setPostTitle(e.target.value)}
                    />
                    <textarea
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                      placeholder="Post body"
                      rows={3}
                      value={postBody}
                      onChange={e => setPostBody(e.target.value)}
                    />
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Note (optional) — why is this acceptable/unacceptable?"
                      value={postNote}
                      onChange={e => setPostNote(e.target.value)}
                    />
                    {postError && <p className="text-sm text-red-600">{postError}</p>}
                    <button
                      className="btn-secondary flex items-center gap-1.5 text-sm"
                      disabled={!postTitle.trim() || addPostMutation.isPending}
                      onClick={() => addPostMutation.mutate()}
                    >
                      {addPostMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                      Add post
                    </button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="flex gap-2">
                      {(['acceptable', 'unacceptable'] as const).map(l => (
                        <button
                          key={l}
                          onClick={() => setUrlLabel(l)}
                          className={`flex-1 py-1.5 rounded border text-xs font-medium transition-colors ${urlLabel === l ? (l === 'acceptable' ? 'bg-green-600 text-white border-green-600' : 'bg-red-600 text-white border-red-600') : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}
                        >
                          {l.charAt(0).toUpperCase() + l.slice(1)}
                        </button>
                      ))}
                    </div>
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Reddit post URL"
                      value={urlValue}
                      onChange={e => setUrlValue(e.target.value)}
                    />
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Note (optional)"
                      value={urlNote}
                      onChange={e => setUrlNote(e.target.value)}
                    />
                    {urlError && <p className="text-sm text-red-600">{urlError}</p>}
                    <button
                      className="btn-secondary flex items-center gap-1.5 text-sm"
                      disabled={!urlValue.trim() || importUrlMutation.isPending}
                      onClick={() => importUrlMutation.mutate()}
                    >
                      {importUrlMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                      Import post
                    </button>
                  </div>
                )}
              </div>

              {/* Sample posts list */}
              {samplePosts.length > 0 && (
                <div className="card p-4">
                  <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                    Added posts ({samplePosts.length})
                  </h3>
                  <div className="space-y-2">
                    {samplePosts.map((post: CommunitySamplePost) => {
                      const content = (post.content as any)?.content
                      const title = content?.title || '(untitled)'
                      return (
                        <div key={post.id} className="flex items-start gap-3 py-1.5">
                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0 mt-0.5 ${post.label === 'acceptable' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                            {post.label}
                          </span>
                          <span className="text-sm text-gray-800 flex-1 truncate">{title}</span>
                          <button
                            onClick={() => deletePostMutation.mutate(post.id)}
                            disabled={deletePostMutation.isPending}
                            className="text-gray-400 hover:text-red-500 transition-colors flex-shrink-0"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              <StepNav
                onBack={() => setStep(1)}
                onContinue={() => setStep(3)}
                continueLabel="Continue →"
                skipWarning={samplePosts.length === 0 ? 'No sample posts added — atmosphere generation will be limited.' : undefined}
              />
            </div>
          )}

          {/* ── Step 3 ── */}
          {step === 3 && (
            <div className="card p-8 space-y-5">
              <div>
                <h2 className="text-xl font-bold text-gray-900 mb-1">Generate community atmosphere</h2>
                <p className="text-sm text-gray-500">
                  The atmosphere profile captures the tone, typical content, and moderation style of your community. It is used to calibrate subjective rubrics when compiling rules into checklists.
                </p>
              </div>

              <button
                className="btn-primary flex items-center gap-2"
                onClick={() => generateMutation.mutate()}
                disabled={generateMutation.isPending}
              >
                {generateMutation.isPending && <Loader2 size={14} className="animate-spin" />}
                {atmosphere ? 'Regenerate Atmosphere' : 'Generate Atmosphere'}
              </button>

              {atmosphereError && (
                <div className="flex items-start gap-2 text-sm text-red-600 bg-red-50 rounded-md px-3 py-2">
                  <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                  {atmosphereError}
                </div>
              )}

              {atmosphere && (
                <>
                  <div className="rounded-lg bg-gray-50 border border-gray-200 divide-y divide-gray-200">
                    {(
                      [
                        ['Tone', atmosphere.tone],
                        ['Typical content', atmosphere.typical_content],
                        ['What belongs', atmosphere.what_belongs],
                        ["What doesn't belong", atmosphere.what_doesnt_belong],
                        ['Moderation style', atmosphere.moderation_style],
                      ] as [string, string][]
                    ).map(([label, value]) => (
                      <div key={label} className="px-4 py-3">
                        <div className="text-xs font-semibold text-gray-500 mb-0.5">{label}</div>
                        <div className="text-sm text-gray-800">{value}</div>
                      </div>
                    ))}
                  </div>
                  {crawledCount !== null && (
                    <p className="text-xs text-gray-400">
                      {crawledCount > 0
                        ? `Atmosphere includes ${crawledCount} posts crawled from the subreddit.`
                        : 'Generated from sample posts only.'}
                    </p>
                  )}
                </>
              )}

              <StepNav onBack={() => setStep(2)} onContinue={() => setStep(4)} continueLabel="Continue →" />
            </div>
          )}

          {/* ── Step 4 ── */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="card p-8">
                <h2 className="text-xl font-bold text-gray-900 mb-1">Add moderation rules</h2>
                <p className="text-sm text-gray-500 mb-6">
                  Rules define what is and isn't allowed in your community. You can add them manually or paste an existing ruleset as JSON. Rules can also be added later in the Rules editor.
                </p>

                {/* Tab toggle */}
                <div className="flex gap-2 mb-5">
                  {(['manual', 'import'] as const).map(t => (
                    <button
                      key={t}
                      className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${ruleTab === t ? 'bg-indigo-600 text-white border-indigo-600' : 'border-gray-300 text-gray-600 hover:border-indigo-400'}`}
                      onClick={() => setRuleTab(t)}
                    >
                      {t === 'manual' ? 'Manual' : 'Import JSON'}
                    </button>
                  ))}
                </div>

                {ruleTab === 'manual' ? (
                  <div className="space-y-3">
                    <input
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      placeholder="Rule title (e.g., No personal attacks)"
                      value={ruleTitle}
                      onChange={e => setRuleTitle(e.target.value)}
                    />
                    <textarea
                      className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
                      placeholder="Rule text — write the full rule as you'd present it to your community"
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
                      Add rule
                    </button>
                  </div>
                ) : (
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
                      placeholder="Paste JSON here…"
                      rows={6}
                      value={importJson}
                      onChange={e => { setImportJson(e.target.value); setImportError(''); setImportResult('') }}
                    />
                    {importError && <p className="text-sm text-red-600">{importError}</p>}
                    {importResult && (
                      <div className="space-y-1">
                        <p className="text-sm text-green-700 bg-green-50 rounded px-3 py-2">{importResult}</p>
                        <p className="text-xs text-gray-400">Actionable rules are compiled into checklists in the background — this takes a moment after you finish setup.</p>
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

              {/* Finish */}
              <div className="flex items-center justify-between pt-2">
                <button className="btn-secondary" onClick={() => setStep(3)}>
                  ← Back
                </button>
                <button className="btn-primary" onClick={handleFinish}>
                  Finish Setup →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StepNav({
  onBack,
  onContinue,
  continueLabel,
  skipWarning,
}: {
  onBack: () => void
  onContinue: () => void
  continueLabel: string
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
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button className="btn-primary" onClick={handleContinue}>{continueLabel}</button>
      </div>
    </div>
  )
}
