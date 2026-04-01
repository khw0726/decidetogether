import { AlertTriangle, Loader2, Sparkles, X } from 'lucide-react'
import { NewRuleSuggestion, Suggestion } from '../api/client'

interface NewRuleSuggestionModalProps {
  result: NewRuleSuggestion
  onAccept: () => void
  onDismiss: () => void
  onClose: () => void
  accepting: boolean
  dismissing: boolean
}

export default function NewRuleSuggestionModal({
  result,
  onAccept,
  onDismiss,
  onClose,
  accepting,
  dismissing,
}: NewRuleSuggestionModalProps) {
  const s: Suggestion = result.suggestion
  const content = s.content as Record<string, unknown>
  const confidence = content.confidence as string

  const confidenceColor =
    confidence === 'high' ? 'text-green-700 bg-green-50 border-green-200' :
    confidence === 'medium' ? 'text-amber-700 bg-amber-50 border-amber-200' :
    'text-red-700 bg-red-50 border-red-200'

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold flex items-center gap-2">
            <Sparkles size={16} className="text-indigo-600" />
            Suggested New Rule
          </h3>
          <button className="text-gray-400 hover:text-gray-600" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {result.warning && (
          <div className="mb-3 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700 flex items-center gap-1.5">
            <AlertTriangle size={12} />
            {result.warning}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Title</label>
            <p className="mt-0.5 font-medium">{content.title as string}</p>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Rule Text</label>
            <p className="mt-0.5 text-sm text-gray-700 whitespace-pre-wrap">{content.text as string}</p>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Confidence</label>
            <span className={`mt-0.5 inline-block text-xs px-2 py-0.5 rounded border ${confidenceColor}`}>
              {confidence}
            </span>
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Reasoning</label>
            <p className="mt-0.5 text-xs text-gray-600 italic">{content.reasoning as string}</p>
          </div>
        </div>

        <div className="flex gap-2 justify-end mt-5">
          <button
            className="btn-secondary text-sm"
            onClick={onDismiss}
            disabled={dismissing || accepting}
          >
            {dismissing && <Loader2 size={12} className="animate-spin" />}
            Dismiss
          </button>
          <button
            className="btn-primary text-sm"
            onClick={onAccept}
            disabled={accepting || dismissing}
          >
            {accepting && <Loader2 size={12} className="animate-spin" />}
            Accept & Create Rule
          </button>
        </div>
      </div>
    </div>
  )
}
