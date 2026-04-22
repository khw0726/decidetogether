import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronUp, Loader2, Sparkles } from 'lucide-react'
import { showErrorToast } from './Toast'
import {
  listUnlinkedOverrides,
  suggestRuleFromOverrides,
  acceptSuggestion,
  dismissSuggestion,
  Example,
  NewRuleSuggestion,
} from '../api/client'
import NewRuleSuggestionModal from './NewRuleSuggestionModal'

interface UnlinkedOverridesPanelProps {
  communityId: string
}

export default function UnlinkedOverridesPanel({ communityId }: UnlinkedOverridesPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [suggestion, setSuggestion] = useState<NewRuleSuggestion | null>(null)
  const [suggesting, setSuggesting] = useState(false)

  const queryClient = useQueryClient()

  const { data: overrides = [] } = useQuery({
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

  if (overrides.length === 0) return null

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSuggest = async () => {
    if (selectedIds.size === 0) return
    setSuggesting(true)
    try {
      const result = await suggestRuleFromOverrides(communityId, [...selectedIds])
      setSuggestion(result)
    } catch (e) {
      showErrorToast(e instanceof Error ? e.message : 'Failed to generate suggestion')
    } finally {
      setSuggesting(false)
    }
  }

  const selectedCount = selectedIds.size
  const showWarning = selectedCount > 0 && selectedCount < 3

  return (
    <div className="mx-6 mt-4 border border-amber-200 rounded-lg bg-amber-50">
      {/* Header */}
      <button
        className="w-full flex items-center gap-2 px-4 py-3 text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <AlertTriangle size={14} className="text-amber-600 flex-shrink-0" />
        <span className="text-sm font-medium text-amber-900">
          {overrides.length} override{overrides.length !== 1 ? 's' : ''} with no matching rule
        </span>
        <span className="text-xs text-amber-700 ml-1">— agent missed these, may need a new rule</span>
        <div className="flex-1" />
        {expanded ? <ChevronUp size={14} className="text-amber-600" /> : <ChevronDown size={14} className="text-amber-600" />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          {/* Example list */}
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {overrides.map((ex: Example) => {
              const postContent = (ex.content.content as Record<string, unknown>) || {}
              const title = (postContent.title as string) || ''
              const body = (postContent.body as string) || ''
              return (
                <label
                  key={ex.id}
                  className="flex items-start gap-2 cursor-pointer p-2 rounded hover:bg-amber-100"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 flex-shrink-0"
                    checked={selectedIds.has(ex.id)}
                    onChange={() => toggleSelect(ex.id)}
                  />
                  <div className="min-w-0">
                    {title && <p className="text-sm font-medium truncate">{title}</p>}
                    {body && <p className="text-xs text-gray-500 line-clamp-1">{body}</p>}
                    {ex.moderator_reasoning && (
                      <p className="text-xs text-amber-700 italic mt-0.5">{ex.moderator_reasoning}</p>
                    )}
                  </div>
                </label>
              )
            })}
          </div>

          {/* Warning + action */}
          <div className="flex items-center gap-3">
            {showWarning && (
              <p className="text-xs text-amber-700 flex items-center gap-1">
                <AlertTriangle size={12} />
                Fewer than 3 selected — suggestion may over-fit to a one-off
              </p>
            )}
            <div className="flex-1" />
            <button
              className="btn-primary text-xs flex items-center gap-1.5"
              disabled={selectedCount === 0 || suggesting}
              onClick={handleSuggest}
            >
              {suggesting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              Suggest New Rule
              {selectedCount > 0 && ` (${selectedCount})`}
            </button>
          </div>
        </div>
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
