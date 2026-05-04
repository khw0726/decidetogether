import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowBigUp, MessageSquare } from 'lucide-react'
import { getScenarioAtmosphere, AtmospherePost } from '../api/client'

function formatScore(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function PostCard({ post }: { post: AtmospherePost }) {
  const ratio = post.upvote_ratio == null ? null : Math.round(post.upvote_ratio * 100)
  const snippet = post.body.length > 320 ? post.body.slice(0, 320).trimEnd() + '…' : post.body
  return (
    <div className="bg-white border border-gray-200 rounded-md hover:border-gray-400 transition-colors flex">
      <div className="bg-gray-50 rounded-l-md w-12 flex flex-col items-center py-2 text-gray-500">
        <ArrowBigUp className="w-5 h-5" />
        <span className="text-xs font-bold text-gray-700 my-1">{formatScore(post.score)}</span>
      </div>
      <div className="p-3 flex-1 min-w-0">
        <h3 className="text-base font-medium text-gray-900 mb-1 leading-snug">{post.title}</h3>
        {snippet && (
          <p className="text-sm text-gray-700 whitespace-pre-wrap break-words mb-2 leading-relaxed">{snippet}</p>
        )}
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <MessageSquare className="w-3.5 h-3.5" />
            {post.num_comments} comments
          </span>
          {ratio != null && <span>{ratio}% upvoted</span>}
        </div>
      </div>
    </div>
  )
}

export default function CommunityFeedView() {
  const { id } = useParams<{ id: string }>()
  const { data, isLoading, error } = useQuery({
    queryKey: ['scenario-atmosphere', id],
    queryFn: () => getScenarioAtmosphere(id!),
    enabled: !!id,
  })

  if (isLoading) {
    return <div className="p-8 text-center text-gray-500">Loading…</div>
  }
  if (error || !data) {
    return <div className="p-8 text-center text-red-600">Failed to load community.</div>
  }

  return (
    <div className="min-h-screen bg-gray-100">
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-3xl mx-auto px-4 py-5">
          <h1 className="text-2xl font-bold text-gray-900">r/{data.community_name}</h1>
          {data.description && (
            <p className="text-sm text-gray-600 mt-1 whitespace-pre-wrap">{data.description}</p>
          )}
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-6">
        <div className="text-xs uppercase tracking-wide text-gray-500 mb-3">A sample of recent posts</div>
        <div className="space-y-2">
          {data.posts.map((p, i) => <PostCard key={i} post={p} />)}
        </div>

        {data.comments.length > 0 && (
          <div className="mt-10">
            <div className="text-xs uppercase tracking-wide text-gray-500 mb-3">Recent comments</div>
            <div className="space-y-2">
              {data.comments.map((c, i) => (
                <div key={i} className="bg-white border border-gray-200 rounded-md p-3">
                  {c.post_title && (
                    <div className="text-xs text-gray-500 mb-2 pl-2 border-l-2 border-gray-200">
                      <span className="text-gray-400">Replying to </span>
                      <span className="italic">{c.post_title}</span>
                    </div>
                  )}
                  <p className="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed">{c.body}</p>
                  <div className="text-xs text-gray-500 mt-2 flex items-center gap-1">
                    <ArrowBigUp className="w-3.5 h-3.5" /> {formatScore(c.score)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
