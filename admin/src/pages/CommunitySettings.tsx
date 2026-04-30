import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Settings, Plus, Trash2, AlertTriangle, Loader2, Link, Inbox, Check, X, RefreshCw } from 'lucide-react'
import {
  getCommunity,
  generateCommunityContext,
  updateCommunityContext,
  listSamplePosts,
  addSamplePost,
  deleteSamplePost,
  importSamplePostFromUrl,
  pullModqueue,
  approveSamplePost,
  type CommunitySamplePost,
  type CommunityContext,
  type CommunityContextDimension,
} from '../api/client'
import ContextDimensionsView from '../components/ContextDimensionsView'

interface CommunitySettingsProps {
  communityId: string
}

export default function CommunitySettings({ communityId }: CommunitySettingsProps) {
  const queryClient = useQueryClient()
  const { data: community } = useQuery({
    queryKey: ['community', communityId],
    queryFn: () => getCommunity(communityId),
    enabled: !!communityId,
  })

  const { data: samplePosts = [] } = useQuery({
    queryKey: ['sample-posts', communityId],
    queryFn: () => listSamplePosts(communityId),
    enabled: !!communityId,
  })

  const contextMutation = useMutation({
    mutationFn: () => generateCommunityContext(communityId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['community', communityId] })
    },
  })

  const contextUpdateMutation = useMutation({
    mutationFn: (data: Partial<CommunityContext>) => updateCommunityContext(communityId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['community', communityId] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (postId: string) => deleteSamplePost(communityId, postId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })
      queryClient.invalidateQueries({ queryKey: ['community', communityId] })
    },
  })

  const approveMutation = useMutation({
    mutationFn: ({ postId, label }: { postId: string; label?: 'acceptable' | 'unacceptable' }) =>
      approveSamplePost(communityId, postId, label ? { label } : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })
      queryClient.invalidateQueries({ queryKey: ['community', communityId] })
    },
  })

  const committedSamples = samplePosts.filter(p => p.status === 'committed')
  const pendingSamples = samplePosts.filter(p => p.status === 'pending')

  if (!communityId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <AlertTriangle size={48} className="mb-4 opacity-40" />
        <p className="text-lg font-medium">No community selected</p>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Settings size={22} />
          Community Profile
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {community?.name} — {community?.platform}
        </p>
      </div>

      {/* Community Context */}
      <section>
        <div className="mb-3">
          <h2 className="text-base font-semibold text-gray-800">Community Context</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Four-dimensional profile used to calibrate moderation thresholds and rubrics.
          </p>
        </div>

        {community?.context_stale && (
          <div className="mb-3 flex items-center justify-between gap-3 px-3 py-2 rounded border border-amber-200 bg-amber-50 text-amber-900 text-sm">
            <span className="flex items-center gap-2">
              <RefreshCw size={14} />
              Sample posts have changed since this context was generated.
            </span>
            <button
              className="btn-primary text-xs flex items-center gap-1.5"
              onClick={() => contextMutation.mutate()}
              disabled={contextMutation.isPending}
            >
              {contextMutation.isPending && <Loader2 size={11} className="animate-spin" />}
              Regenerate
            </button>
          </div>
        )}

        {contextMutation.isPending && !community?.community_context ? (
          <div className="card p-8 flex flex-col items-center justify-center gap-3 text-gray-500">
            <Loader2 size={24} className="animate-spin text-indigo-500" />
            <p className="text-sm">Crawling posts and generating context...</p>
          </div>
        ) : community?.community_context ? (
          <ContextDimensionsView
            context={community.community_context}
            communityId={communityId}
            onRegenerate={() => contextMutation.mutate()}
            isRegenerating={contextMutation.isPending}
            onSaveDimension={async (key: keyof CommunityContext, dim: CommunityContextDimension) => {
              await contextUpdateMutation.mutateAsync({ [key]: dim })
            }}
            isSaving={contextUpdateMutation.isPending}
          />
        ) : (
          <div className="card p-6 text-center text-gray-400 text-sm space-y-3">
            <p>No community context yet.</p>
            <button
              className="btn-primary text-sm"
              onClick={() => contextMutation.mutate()}
              disabled={contextMutation.isPending}
            >
              Generate Context
            </button>
          </div>
        )}
      </section>

      {/* Pending review (modqueue-sourced) */}
      {pendingSamples.length > 0 && (
        <section>
          <div className="mb-3">
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2">
              <Inbox size={16} />
              Pending review
              <span className="text-xs font-normal text-gray-500">
                ({pendingSamples.length} from modqueue)
              </span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Posts pulled from the modlog. Approve to add as samples, or reject to discard.
            </p>
          </div>
          <div className="space-y-2">
            {pendingSamples.map(post => (
              <PendingPostCard
                key={post.id}
                post={post}
                onApprove={(label) => approveMutation.mutate({ postId: post.id, label })}
                onReject={() => deleteMutation.mutate(post.id)}
                busy={approveMutation.isPending || deleteMutation.isPending}
              />
            ))}
          </div>
        </section>
      )}

      {/* Sample Posts */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold text-gray-800">Sample Posts</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Representative posts that shape this community's context (and through it, every rule).
            </p>
          </div>
          <div className="flex items-center gap-2">
            <PullModqueueButton
              communityId={communityId}
              onPulled={() => queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })}
            />
            <AddSamplePostButton
              communityId={communityId}
              onAdded={() => {
                queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })
                queryClient.invalidateQueries({ queryKey: ['community', communityId] })
              }}
            />
          </div>
        </div>

        {committedSamples.length === 0 ? (
          <div className="card p-6 text-center text-gray-400 text-sm">
            No sample posts yet. Add manually, import a Reddit URL, or pull recent modqueue actions.
          </div>
        ) : (
          <div className="space-y-2">
            {committedSamples.map(post => (
              <SamplePostCard
                key={post.id}
                post={post}
                onDelete={() => deleteMutation.mutate(post.id)}
                deleting={deleteMutation.isPending}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function PendingPostCard({
  post,
  onApprove,
  onReject,
  busy,
}: {
  post: CommunitySamplePost
  onApprove: (label?: 'acceptable' | 'unacceptable') => void
  onReject: () => void
  busy: boolean
}) {
  const content = (post.content as Record<string, unknown>)?.content as Record<string, unknown> | undefined
  const title = (content?.title as string) || '(no title)'
  const body = ((content?.body as string) || '').slice(0, 140)
  const meta = post.source_metadata || {}
  const action = (meta.action as string) || ''
  const mod = (meta.mod_username as string) || ''
  const otherLabel: 'acceptable' | 'unacceptable' =
    post.label === 'acceptable' ? 'unacceptable' : 'acceptable'

  return (
    <div className="card px-4 py-3 flex items-start gap-3 border-amber-200 bg-amber-50/40">
      <span
        className={`mt-0.5 flex-shrink-0 text-xs font-semibold px-2 py-0.5 rounded-full ${
          post.label === 'acceptable'
            ? 'bg-green-100 text-green-700'
            : 'bg-red-100 text-red-700'
        }`}
      >
        {post.label}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-800 truncate">{title}</p>
        {body && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{body}</p>}
        <p className="text-xs text-gray-400 mt-1">
          {action ? `${action}` : 'modqueue'}{mod ? ` by u/${mod}` : ''}
        </p>
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          className="px-2 py-1 text-xs rounded border border-gray-200 hover:bg-gray-50 text-gray-600"
          onClick={() => onApprove(otherLabel)}
          disabled={busy}
          title={`Approve as ${otherLabel} instead`}
        >
          Flip → {otherLabel}
        </button>
        <button
          className="p-1.5 rounded text-green-700 hover:bg-green-100"
          onClick={() => onApprove()}
          disabled={busy}
          title="Approve as-is"
        >
          <Check size={14} />
        </button>
        <button
          className="p-1.5 rounded text-red-600 hover:bg-red-100"
          onClick={onReject}
          disabled={busy}
          title="Reject (discard)"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  )
}

function PullModqueueButton({
  communityId,
  onPulled,
}: {
  communityId: string
  onPulled: () => void
}) {
  const [open, setOpen] = useState(false)
  const [limit, setLimit] = useState(25)
  const [sinceDays, setSinceDays] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ new_count: number; skipped_existing: number } | null>(null)

  const close = () => {
    setOpen(false)
    setError('')
    setResult(null)
  }

  const handleSubmit = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const r = await pullModqueue(communityId, {
        limit,
        since_days: sinceDays.trim() ? Number(sinceDays) : null,
      })
      setResult({ new_count: r.new_count, skipped_existing: r.skipped_existing })
      onPulled()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to pull from modqueue.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  if (!open) {
    return (
      <button
        className="btn-secondary flex items-center gap-1.5 text-sm"
        onClick={() => setOpen(true)}
      >
        <Inbox size={14} />
        Pull from modqueue
      </button>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-md">
        <h3 className="text-base font-semibold mb-3">Pull from modqueue</h3>
        <p className="text-xs text-gray-500 mb-4">
          Fetch recent removelink/approvelink actions from your subreddit's modlog. Each becomes a
          pending sample, labeled by the action — approve them below to make them active.
        </p>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">
              Max actions per type
            </label>
            <input
              type="number"
              min={1}
              max={200}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={limit}
              onChange={e => setLimit(Number(e.target.value) || 25)}
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">
              Only actions from the last N days (optional)
            </label>
            <input
              type="number"
              min={1}
              placeholder="(no limit)"
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={sinceDays}
              onChange={e => setSinceDays(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          {result && (
            <p className="text-sm text-green-700">
              Staged {result.new_count} new pending sample{result.new_count === 1 ? '' : 's'}.
              {result.skipped_existing > 0 && ` Skipped ${result.skipped_existing} duplicates.`}
            </p>
          )}
        </div>
        <div className="flex gap-2 justify-end pt-4">
          <button type="button" className="btn-secondary" onClick={close}>
            {result ? 'Done' : 'Cancel'}
          </button>
          {!result && (
            <button
              type="button"
              className="btn-primary flex items-center gap-1.5"
              onClick={handleSubmit}
              disabled={loading}
            >
              {loading && <Loader2 size={13} className="animate-spin" />}
              {loading ? 'Pulling...' : 'Pull'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function SamplePostCard({
  post,
  onDelete,
  deleting,
}: {
  post: CommunitySamplePost
  onDelete: () => void
  deleting: boolean
}) {
  const content = (post.content as Record<string, unknown>)?.content as Record<string, unknown> | undefined
  const title = (content?.title as string) || '(no title)'
  const body = ((content?.body as string) || '').slice(0, 140)

  return (
    <div className="card px-4 py-3 flex items-start gap-3">
      <span
        className={`mt-0.5 flex-shrink-0 text-xs font-semibold px-2 py-0.5 rounded-full ${
          post.label === 'acceptable'
            ? 'bg-green-100 text-green-700'
            : 'bg-red-100 text-red-700'
        }`}
      >
        {post.label}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-800 truncate">{title}</p>
        {body && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{body}</p>}
        {post.note && (
          <p className="text-xs text-indigo-600 mt-1 italic">{post.note}</p>
        )}
      </div>
      <button
        className="flex-shrink-0 text-gray-400 hover:text-red-500 transition-colors"
        onClick={onDelete}
        disabled={deleting}
        title="Remove sample post"
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}

function AddSamplePostButton({
  communityId,
  onAdded,
}: {
  communityId: string
  onAdded: () => void
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'manual' | 'url'>('url')

  if (!open) {
    return (
      <button
        className="btn-secondary flex items-center gap-1.5 text-sm"
        onClick={() => setOpen(true)}
      >
        <Plus size={14} />
        Add post
      </button>
    )
  }

  const close = () => setOpen(false)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-md">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">Add Sample Post</h3>
          {/* Mode toggle */}
          <div className="flex rounded border border-gray-200 overflow-hidden text-xs">
            <button
              type="button"
              className={`px-3 py-1.5 flex items-center gap-1 ${mode === 'url' ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setMode('url')}
            >
              <Link size={11} />
              From URL
            </button>
            <button
              type="button"
              className={`px-3 py-1.5 ${mode === 'manual' ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setMode('manual')}
            >
              Manual
            </button>
          </div>
        </div>

        {mode === 'url' ? (
          <UrlImportForm communityId={communityId} onAdded={() => { onAdded(); close() }} onCancel={close} />
        ) : (
          <ManualPostForm communityId={communityId} onAdded={() => { onAdded(); close() }} onCancel={close} />
        )}
      </div>
    </div>
  )
}

function UrlImportForm({
  communityId,
  onAdded,
  onCancel,
}: {
  communityId: string
  onAdded: () => void
  onCancel: () => void
}) {
  const [url, setUrl] = useState('')
  const [label, setLabel] = useState<'acceptable' | 'unacceptable'>('acceptable')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return
    setLoading(true)
    setError('')
    try {
      await importSamplePostFromUrl(communityId, {
        url: url.trim(),
        label,
        note: note.trim() || undefined,
      })
      onAdded()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to import post.'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs font-medium mb-1">Reddit Post URL</label>
        <input
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="https://www.reddit.com/r/sub/comments/..."
          value={url}
          onChange={e => setUrl(e.target.value)}
          autoFocus
        />
        <p className="text-xs text-gray-400 mt-1">Paste any Reddit post URL — the post content will be fetched automatically.</p>
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">Label</label>
        <div className="flex gap-3">
          {(['acceptable', 'unacceptable'] as const).map(l => (
            <label key={l} className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" name="url-label" value={l} checked={label === l} onChange={() => setLabel(l)} />
              {l}
            </label>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">Note (optional)</label>
        <input
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="e.g., 'Good example of acceptable game content'"
          value={note}
          onChange={e => setNote(e.target.value)}
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-2 justify-end pt-1">
        <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn-primary flex items-center gap-1.5" disabled={loading || !url.trim()}>
          {loading && <Loader2 size={13} className="animate-spin" />}
          {loading ? 'Importing...' : 'Import'}
        </button>
      </div>
    </form>
  )
}

function ManualPostForm({
  communityId,
  onAdded,
  onCancel,
}: {
  communityId: string
  onAdded: () => void
  onCancel: () => void
}) {
  const [label, setLabel] = useState<'acceptable' | 'unacceptable'>('acceptable')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    setLoading(true)
    setError('')
    try {
      await addSamplePost(communityId, {
        content: {
          content: { title: title.trim(), body: body.trim() },
          author: {},
          context: {},
        },
        label,
        note: note.trim() || undefined,
      })
      onAdded()
    } catch {
      setError('Failed to add post.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="block text-xs font-medium mb-1">Label</label>
        <div className="flex gap-3">
          {(['acceptable', 'unacceptable'] as const).map(l => (
            <label key={l} className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" name="manual-label" value={l} checked={label === l} onChange={() => setLabel(l)} />
              {l}
            </label>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">Post Title</label>
        <input
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="Title of the post"
          value={title}
          onChange={e => setTitle(e.target.value)}
          autoFocus
        />
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">Post Body (optional)</label>
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
          placeholder="Post content..."
          rows={3}
          value={body}
          onChange={e => setBody(e.target.value)}
        />
      </div>
      <div>
        <label className="block text-xs font-medium mb-1">Note (optional)</label>
        <input
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="e.g., 'Removed for being off-topic despite game mention'"
          value={note}
          onChange={e => setNote(e.target.value)}
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-2 justify-end pt-1">
        <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn-primary" disabled={loading || !title.trim()}>
          {loading ? 'Adding...' : 'Add'}
        </button>
      </div>
    </form>
  )
}
