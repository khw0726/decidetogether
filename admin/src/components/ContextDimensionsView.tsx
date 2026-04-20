import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { CommunityContext } from '../api/client'

export const DIMENSION_META: [string, keyof CommunityContext][] = [
  ['Purpose', 'purpose'],
  ['Participants', 'participants'],
  ['Stakes', 'stakes'],
  ['Tone', 'tone'],
]

export default function ContextDimensionsView({
  context,
  onRegenerate,
  isRegenerating,
}: {
  context: CommunityContext
  onRegenerate: () => void
  isRegenerating: boolean
}) {
  const [expandedDim, setExpandedDim] = useState<string | null>(null)

  return (
    <>
      <div className="space-y-2">
        {DIMENSION_META.map(([label, key]) => {
          const dim = context[key]
          if (!dim) return null
          const isOpen = expandedDim === key
          return (
            <div key={key} className="rounded-lg bg-gray-50 border border-gray-200">
              <button
                className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-gray-100 transition-colors rounded-lg"
                onClick={() => setExpandedDim(isOpen ? null : key)}
              >
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider w-24 flex-shrink-0">{label}</span>
                <div className="flex flex-wrap gap-1.5 flex-1">
                  {dim.tags.map(tag => (
                    <span key={tag} className="text-xs px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">
                      {tag}
                    </span>
                  ))}
                </div>
                <span className="text-xs text-gray-300 flex-shrink-0">{isOpen ? '▾' : '▸'}</span>
              </button>
              {isOpen && (
                <div className="px-4 pb-3 pt-0">
                  <p className="text-sm text-gray-600 border-t border-gray-200 pt-2">{dim.prose}</p>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <button
        className="btn-secondary flex items-center gap-2 text-sm"
        onClick={onRegenerate}
        disabled={isRegenerating}
      >
        {isRegenerating && <Loader2 size={14} className="animate-spin" />}
        Regenerate
      </button>
    </>
  )
}
