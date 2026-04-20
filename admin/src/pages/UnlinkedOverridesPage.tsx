import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Sparkles, CheckCircle } from 'lucide-react'
import {
  listUnlinkedOverrides,
  suggestRuleFromOverrides,
  acceptSuggestion,
  dismissSuggestion,
  Example,
  NewRuleSuggestion,
} from '../api/client'
import NewRuleSuggestionModal from '../components/NewRuleSuggestionModal'

interface UnlinkedOverridesPageProps {
  communityId: string
}

export default function UnlinkedOverridesPage({ communityId }: UnlinkedOverridesPageProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [suggestion, setSuggestion] = useState<NewRuleSuggestion | null>(null)
  const [suggesting, setSuggesting] = useState(false)

  const queryClient = useQueryClient()

  const { data: overrides = [], isLoading } = useQuery({
    queryKey: ['unlinked-overrides', communityId],
    queryFn: () => listUnlinkedOverrides(communityId),
    enabled: !!communityId,
  })

  const acceptMutation = useMutation({
    mutationFn: (suggestionId: string) => acceptSuggestion(suggestionId),
    onSuccess: () => {
      setSuggestion(null)
      setSelectedIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['unlinked-overrides', communityId] })
      queryClient.invalidateQueries({ queryKey: ['rules', communityId] })
    },
  })

  const dismissMutation = useMutation({
    mutationFn: (suggestionId: string) => dismissSuggestion(suggestionId),
    onSuccess: () => setSuggestion(null),
  })

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (selectedIds.size === overrides.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(overrides.map((ex: Example) => ex.id)))
    }
  }

  const handleSuggest = async () => {
    if (selectedIds.size === 0) return
    setSuggesting(true)
    try {
      const result = await suggestRuleFromOverrides(communityId, [...selectedIds])
      setSuggestion(result)
    } finally {
      setSuggesting(false)
    }
  }

  const selectedCount = selectedIds.size
  const showWarning = selectedCount > 0 && selectedCount < 3

  if (!communityId) {
    return (
      <div className="p-8 text-center text-gray-500">
        Select a community from the sidebar to view unlinked overrides.
      </div>
    )
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-gray-900">Unlinked Overrides</h1>
        <p className="text-sm text-gray-500 mt-1">
          Posts the agent approved but a moderator removed, with no matching rule.
          Select examples to suggest a new rule that would catch them.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16 text-gray-400">
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : overrides.length === 0 ? (
        <div className="card p-12 text-center">
          <CheckCircle size={32} className="mx-auto text-green-400 mb-3" />
          <p className="text-gray-600 font-medium">No unlinked overrides</p>
          <p className="text-sm text-gray-400 mt-1">
            All moderator removals are covered by existing rules.
          </p>
        </div>
      ) : (
        <>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4">
            <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedIds.size === overrides.length}
                onChange={toggleAll}
              />
              Select all ({overrides.length})
            </label>
            <div className="flex-1" />
            {showWarning && (
              <p className="text-xs text-amber-700 flex items-center gap-1">
                <AlertTriangle size={12} />
                Fewer than 3 selected — suggestion may over-fit
              </p>
            )}
            <button
              className="btn-primary text-sm flex items-center gap-1.5"
              disabled={selectedCount === 0 || suggesting}
              onClick={handleSuggest}
            >
              {suggesting ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              Suggest New Rule
              {selectedCount > 0 && ` (${selectedCount})`}
            </button>
          </div>

          {/* Override cards */}
          <div className="space-y-2">
            {overrides.map((ex: Example) => {
              const postContent = (ex.content.content as Record<string, unknown>) || {}
              const title = (postContent.title as string) || ''
              const body = (postContent.body as string) || ''
              const author = (postContent.author as string) || ''
              const isSelected = selectedIds.has(ex.id)

              return (
                <div
                  key={ex.id}
                  className={`card p-4 cursor-pointer transition-colors ${
                    isSelected ? 'ring-2 ring-indigo-400 bg-indigo-50/30' : 'hover:bg-gray-50'
                  }`}
                  onClick={() => toggleSelect(ex.id)}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-1 flex-shrink-0"
                      checked={isSelected}
                      onChange={() => toggleSelect(ex.id)}
                      onClick={e => e.stopPropagation()}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        {title && <p className="font-medium text-sm text-gray-900 truncate">{title}</p>}
                        {author && <span className="text-xs text-gray-400 flex-shrink-0">u/{author}</span>}
                      </div>
                      {body && (
                        <p className="text-sm text-gray-600 line-clamp-3 whitespace-pre-wrap">{body}</p>
                      )}
                      {ex.moderator_reasoning && (
                        <p className="text-xs text-amber-700 italic mt-2 flex items-center gap-1">
                          <AlertTriangle size={10} className="flex-shrink-0" />
                          {ex.moderator_reasoning}
                        </p>
                      )}
                    </div>
                    <span className="text-xs font-medium text-red-600 bg-red-50 px-2 py-0.5 rounded flex-shrink-0">
                      removed
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* Suggestion modal */}
      {suggestion && (
        <NewRuleSuggestionModal
          result={suggestion}
          onAccept={() => acceptMutation.mutate(suggestion.suggestion.id)}
          onDismiss={() => dismissMutation.mutate(suggestion.suggestion.id)}
          accepting={acceptMutation.isPending}
          dismissing={dismissMutation.isPending}
          onClose={() => setSuggestion(null)}
        />
      )}
    </div>
  )
}
