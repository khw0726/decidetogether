import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X, AlertCircle } from 'lucide-react'
import { acceptSuggestion, acceptRecompile, dismissSuggestion, Suggestion } from '../api/client'

interface SuggestionDiffProps {
  suggestions: Suggestion[]
  ruleId: string
  onClose: () => void
}

export default function SuggestionDiff({ suggestions, ruleId, onClose }: SuggestionDiffProps) {
  const queryClient = useQueryClient()

  const acceptMutation = useMutation({
    mutationFn: (suggestion: Suggestion) =>
      suggestion.suggestion_type === 'checklist'
        ? acceptRecompile(ruleId, suggestion.id)
        : acceptSuggestion(suggestion.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['examples', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
    },
  })

  const dismissMutation = useMutation({
    mutationFn: dismissSuggestion,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', ruleId] })
    },
  })

  const pending = suggestions.filter(s => s.status === 'pending')

  if (pending.length === 0) {
    return null
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="card w-full max-w-2xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <AlertCircle size={18} className="text-amber-500" />
            <h3 className="font-semibold">Pending Suggestions</h3>
            <span className="badge badge-yellow">{pending.length}</span>
          </div>
          <button className="text-gray-400 hover:text-gray-600" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4 space-y-4">
          {pending.map(suggestion => (
            <SuggestionCard
              key={suggestion.id}
              suggestion={suggestion}
              onAccept={() => acceptMutation.mutate(suggestion)}
              onDismiss={() => dismissMutation.mutate(suggestion.id)}
              isPending={acceptMutation.isPending || dismissMutation.isPending}
            />
          ))}
        </div>

        <div className="p-4 border-t border-gray-200 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

function SuggestionCard({
  suggestion,
  onAccept,
  onDismiss,
  isPending,
}: {
  suggestion: Suggestion
  onAccept: () => void
  onDismiss: () => void
  isPending: boolean
}) {
  const typeLabels: Record<string, string> = {
    checklist: 'Checklist Update',
    rule_text: 'Rule Text Update',
    example: 'New Example',
  }

  const content = suggestion.content as Record<string, unknown>
  const description = (content.description as string) || ''
  const reasoning = (content.reasoning as string) || ''
  const proposed = content.proposed_change || content.proposed_text || content.content

  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span className="badge badge-blue">{typeLabels[suggestion.suggestion_type] || suggestion.suggestion_type}</span>
          </div>
          {description && <p className="text-sm font-medium mb-1">{description}</p>}
          {reasoning && <p className="text-xs text-gray-500 mb-2">{reasoning}</p>}
          {proposed && (
            <div className="bg-gray-50 rounded p-3 text-xs font-mono overflow-auto max-h-40 border border-gray-200">
              {typeof proposed === 'string' ? proposed : JSON.stringify(proposed, null, 2)}
            </div>
          )}
        </div>
        <div className="flex flex-col gap-1.5 flex-shrink-0">
          <button
            className="btn-success text-xs py-1"
            onClick={onAccept}
            disabled={isPending}
            title="Accept suggestion"
          >
            <Check size={12} />
            Accept
          </button>
          <button
            className="btn-secondary text-xs py-1"
            onClick={onDismiss}
            disabled={isPending}
            title="Dismiss suggestion"
          >
            <X size={12} />
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}
