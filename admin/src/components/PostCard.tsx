import { useState } from 'react'
import { ExternalLink, User, Clock, Tag, FileText, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react'

interface PostCardProps {
  post: Record<string, unknown>
  compact?: boolean
}

export default function PostCard({ post, compact = false }: PostCardProps) {
  const [opExpanded, setOpExpanded] = useState(false)
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
  const platformMeta = (context.platform_metadata as Record<string, unknown>) || {}
  const permalink = platformMeta.permalink as string | undefined
  const postUrl = permalink ? (permalink.startsWith('http') ? permalink : `https://www.reddit.com${permalink}`) : undefined

  const isComment = postType === 'comment'
  const threadContext = (post.thread_context as Array<Record<string, unknown>>) || []
  const opEntry = threadContext.find((t) => t.role === 'op')
  const opContent = (opEntry?.content as Record<string, unknown>) || {}
  const opTitle = (opContent.title as string) || ''
  const opBody = (opContent.body as string) || ''
  // Derive OP submission URL from the comment permalink (strip the comment id segment)
  const opUrl = (() => {
    if (!isComment || !permalink) return undefined
    const trimmed = permalink.replace(/\/$/, '')
    const parent = trimmed.replace(/\/[^/]+$/, '/')
    const url = parent.startsWith('http') ? parent : `https://www.reddit.com${parent}`
    return url
  })()
  const typeChipClass = isComment
    ? 'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border border-purple-200 bg-purple-50 text-purple-700'
    : 'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border border-blue-200 bg-blue-50 text-blue-700'
  const typeIcon = isComment ? <MessageSquare size={10} /> : <FileText size={10} />
  const typeLabel = isComment ? 'comment' : (postType === 'link' ? 'link post' : 'post')

  return (
    <div className="space-y-2">
      {/* Header */}
      <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
        {postType && (
          <span className={typeChipClass}>
            {typeIcon}
            {typeLabel}
          </span>
        )}
        {channel && (
          <span className="font-medium text-gray-700">{channel}</span>
        )}
        {flair && (
          <span className="badge badge-gray">
            <Tag size={10} className="mr-1" />
            {flair}
          </span>
        )}
      </div>

      {/* Title */}
      {title && (
        <h4 className={`font-medium text-gray-900 ${compact ? 'text-sm' : 'text-base'}`}>
          {postUrl ? (
            <a href={postUrl} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-600 inline-flex items-center gap-1">
              {title}
              <ExternalLink size={compact ? 12 : 14} className="text-gray-400 flex-shrink-0" />
            </a>
          ) : title}
        </h4>
      )}

      {/* Parent context for comments */}
      {isComment && (opTitle || opBody) && (
        <div className={`border-l-2 border-gray-200 pl-2 text-gray-500 ${compact ? 'text-xs' : 'text-sm'}`}>
          <button
            type="button"
            onClick={() => setOpExpanded((v) => !v)}
            className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-gray-400 hover:text-gray-600"
          >
            {opExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            Replying to
          </button>
          {opTitle && (
            <div className="font-medium text-gray-700">
              {opUrl ? (
                <a href={opUrl} target="_blank" rel="noopener noreferrer" className="hover:text-indigo-600 inline-flex items-center gap-1">
                  {opTitle}
                  <ExternalLink size={compact ? 11 : 12} className="text-gray-400 flex-shrink-0" />
                </a>
              ) : opTitle}
            </div>
          )}
          {opBody && (
            <div className={`whitespace-pre-wrap ${opExpanded ? '' : 'line-clamp-2'}`}>{opBody}</div>
          )}
        </div>
      )}

      {/* Body */}
      {body && (
        isComment && postUrl ? (
          <a
            href={postUrl}
            target="_blank"
            rel="noopener noreferrer"
            className={`block text-gray-600 hover:text-indigo-600 hover:bg-indigo-50/40 -mx-1 px-1 rounded transition-colors ${compact ? 'text-xs' : 'text-sm'}`}
          >
            {body}
            <ExternalLink size={compact ? 11 : 12} className="inline-block ml-1 -mt-0.5 text-gray-400" />
          </a>
        ) : (
          <p className={`text-gray-600 whitespace-pre-wrap ${compact ? 'text-xs' : 'text-sm'}`}>
            {body}
          </p>
        )
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
