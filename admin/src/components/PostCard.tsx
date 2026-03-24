import { ExternalLink, User, Clock, Tag } from 'lucide-react'

interface PostCardProps {
  post: Record<string, unknown>
  compact?: boolean
}

export default function PostCard({ post, compact = false }: PostCardProps) {
  const content = (post.content as Record<string, unknown>) || {}
  const author = (post.author as Record<string, unknown>) || {}
  const context = (post.context as Record<string, unknown>) || {}

  const title = (content.title as string) || ''
  const body = (content.body as string) || ''
  const links = (content.links as string[]) || []
  const username = (author.username as string) || 'unknown'
  const accountAge = author.account_age_days as number | undefined
  const flair = context.flair as string | null | undefined
  const postType = context.post_type as string | undefined
  const channel = context.channel as string | undefined
  const timestamp = post.timestamp as string | undefined

  return (
    <div className="space-y-2">
      {/* Header */}
      <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
        {channel && (
          <span className="font-medium text-gray-700">{channel}</span>
        )}
        {flair && (
          <span className="badge badge-gray">
            <Tag size={10} className="mr-1" />
            {flair}
          </span>
        )}
        {postType && <span className="badge badge-gray">{postType}</span>}
      </div>

      {/* Title */}
      {title && (
        <h4 className={`font-medium text-gray-900 ${compact ? 'text-sm' : 'text-base'}`}>
          {title}
        </h4>
      )}

      {/* Body */}
      {body && (
        <p className={`text-gray-600 ${compact ? 'text-xs line-clamp-3' : 'text-sm'}`}>
          {body}
        </p>
      )}

      {/* Links */}
      {links.length > 0 && !compact && (
        <div className="space-y-1">
          {links.map((link, i) => (
            <a
              key={i}
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-xs text-indigo-600 hover:underline"
            >
              <ExternalLink size={12} />
              {link}
            </a>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center gap-3 text-xs text-gray-400 pt-1 border-t border-gray-100">
        <span className="flex items-center gap-1">
          <User size={11} />
          {username}
          {accountAge !== undefined && <span className="text-gray-300 ml-1">({accountAge}d old)</span>}
        </span>
        {timestamp && (
          <span className="flex items-center gap-1">
            <Clock size={11} />
            {new Date(timestamp).toLocaleString()}
          </span>
        )}
      </div>
    </div>
  )
}
