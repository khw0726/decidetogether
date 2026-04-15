import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Settings, Sparkles, Plus, Trash2, AlertTriangle, Loader2, Link } from 'lucide-react'
import {
  getCommunity,
  generateAtmosphere,
  listSamplePosts,
  addSamplePost,
  deleteSamplePost,
  importSamplePostFromUrl,
  type CommunitySamplePost,
} from '../api/client'

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

  const generateMutation = useMutation({
    mutationFn: () => generateAtmosphere(communityId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['community', communityId] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (postId: string) => deleteSamplePost(communityId, postId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })
    },
  })

  if (!communityId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <AlertTriangle size={48} className="mb-4 opacity-40" />
        <p className="text-lg font-medium">No community selected</p>
      </div>
    )
  }

  const atm = community?.atmosphere

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Settings size={22} />
          Community Settings
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {community?.name} — {community?.platform}
        </p>
      </div>

      {/* Atmosphere Profile */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-1.5">
              <Sparkles size={16} className="text-amber-500" />
              Community Atmosphere
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Used to calibrate subjective rubrics and generate realistic examples during rule compilation.
            </p>
          </div>
          <button
            className="btn-primary flex items-center gap-1.5 text-sm"
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending}
          >
            {generateMutation.isPending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Sparkles size={14} />
            )}
            {atm ? 'Regenerate' : 'Generate'}
          </button>
        </div>

        {generateMutation.isError && (
          <p className="text-sm text-red-600 mb-3">
            {(generateMutation.error as Error)?.message?.includes('No posts available')
              ? 'Add sample posts or resolve moderation decisions first, then regenerate.'
              : 'Failed to generate atmosphere. Try again.'}
          </p>
        )}

        {atm ? (
          <>
            <div className="card divide-y divide-gray-100">
              <AtmosphereRow label="Tone" value={atm.tone} />
              <AtmosphereRow label="Typical content" value={atm.typical_content} />
              <AtmosphereRow label="What belongs" value={atm.what_belongs} />
              <AtmosphereRow label="What doesn't belong" value={atm.what_doesnt_belong} />
              <AtmosphereRow label="Moderation style" value={atm.moderation_style} />
            </div>
          </>
        ) : (
          <div className="card p-6 text-center text-gray-400 text-sm">
            No atmosphere profile yet. Add sample posts below, then click Generate.
          </div>
        )}
      </section>

      {/* Sample Posts */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold text-gray-800">Sample Posts</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Representative posts that signal what's acceptable or unacceptable in this community.
            </p>
          </div>
          <AddSamplePostButton
            communityId={communityId}
            onAdded={() => queryClient.invalidateQueries({ queryKey: ['sample-posts', communityId] })}
          />
        </div>

        {samplePosts.length === 0 ? (
          <div className="card p-6 text-center text-gray-400 text-sm">
            No sample posts yet. Add posts to help the compiler understand your community's norms.
          </div>
        ) : (
          <div className="space-y-2">
            {samplePosts.map(post => (
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

function AtmosphereRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-4 py-3 flex gap-4">
      <span className="text-xs font-medium text-gray-500 w-36 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-sm text-gray-800">{value}</span>
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
