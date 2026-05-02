import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Link2, Loader2, MessageSquare, Send, X } from 'lucide-react'
import {
  RuleIntentMessage,
  acceptSuggestion,
  dismissSuggestion,
  listRuleIntentMessages,
  postRuleIntentMessage,
} from '../api/client'
import { showErrorToast } from './Toast'

function extractErrorMessage(error: unknown): string {
  if (error && typeof error === 'object') {
    const axiosErr = error as { response?: { data?: { detail?: string } }; message?: string }
    if (axiosErr.response?.data?.detail) return axiosErr.response.data.detail
    if (axiosErr.message) return axiosErr.message
  }
  return 'Something went wrong. Please try again.'
}

interface RuleIntentChatProps {
  ruleId: string
  // When set, new messages anchor to this decision (the post is fed to the
  // translator as context). The current rule_text is sent unchanged otherwise.
  decisionId?: string | null
  // Compact mode for narrow surfaces (e.g. inside a decision card).
  compact?: boolean
}

export default function RuleIntentChat({
  ruleId,
  decisionId,
  compact = false,
}: RuleIntentChatProps) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ['rule-intent-messages', ruleId],
    queryFn: () => listRuleIntentMessages(ruleId),
    enabled: !!ruleId,
  })

  const postMutation = useMutation({
    mutationFn: (text: string) => postRuleIntentMessage(ruleId, text, decisionId ?? null),
    onSuccess: () => {
      setDraft('')
      queryClient.invalidateQueries({ queryKey: ['rule-intent-messages', ruleId] })
    },
    onError: err => showErrorToast(extractErrorMessage(err)),
  })

  const acceptMutation = useMutation({
    mutationFn: (suggestionId: string) => acceptSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rule-intent-messages', ruleId] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      queryClient.invalidateQueries({ queryKey: ['checklist', ruleId] })
    },
    onError: err => showErrorToast(extractErrorMessage(err)),
  })

  const dismissMutation = useMutation({
    mutationFn: (suggestionId: string) => dismissSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['rule-intent-messages', ruleId] })
    },
    onError: err => showErrorToast(extractErrorMessage(err)),
  })

  const sortedMessages = useMemo(() => {
    return [...messages].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    )
  }, [messages])

  // Auto-scroll to bottom when new messages arrive.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [sortedMessages.length, postMutation.isPending])

  const handleSend = () => {
    const text = draft.trim()
    if (!text || postMutation.isPending) return
    postMutation.mutate(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter inserts newline.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-gray-100 flex-shrink-0">
        <MessageSquare size={12} className="text-indigo-500" />
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Moderator chat
        </h3>
        {decisionId && (
          <span
            className="ml-auto text-[10px] text-indigo-600 bg-indigo-50 border border-indigo-200 rounded px-1.5 py-0.5 inline-flex items-center gap-0.5"
            title="New messages will be anchored to the post under review"
          >
            <Link2 size={10} /> anchored to post
          </span>
        )}
      </div>

      <div
        ref={scrollRef}
        className={`flex-1 overflow-auto px-3 py-2 space-y-2 ${compact ? 'text-[12px]' : 'text-xs'}`}
      >
        {isLoading && (
          <div className="text-gray-400 italic flex items-center gap-1.5">
            <Loader2 size={11} className="animate-spin" /> Loading…
          </div>
        )}
        {!isLoading && sortedMessages.length === 0 && (
          <p className="text-gray-400 italic">
            Think out loud about what this rule should mean. Casual notes — even questions
            ("…right?") — get translated into proposed rule-text edits you can accept or
            dismiss.
          </p>
        )}
        {sortedMessages.map(m => (
          <MessageRow
            key={m.id}
            message={m}
            onAccept={id => acceptMutation.mutate(id)}
            onDismiss={id => dismissMutation.mutate(id)}
            actionPending={acceptMutation.isPending || dismissMutation.isPending}
          />
        ))}
        {postMutation.isPending && (
          <div className="text-indigo-500 italic flex items-center gap-1.5">
            <Loader2 size={11} className="animate-spin" /> Translating…
          </div>
        )}
      </div>

      <div className="border-t border-gray-100 p-2 flex-shrink-0">
        <div className="flex gap-1.5 items-end">
          <textarea
            className="flex-1 text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
            rows={compact ? 2 : 3}
            placeholder={
              decisionId
                ? 'Thinking about this post and the rule…'
                : 'Casually describe what this rule should mean…'
            }
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={postMutation.isPending}
          />
          <button
            type="button"
            className="btn-primary text-xs"
            onClick={handleSend}
            disabled={!draft.trim() || postMutation.isPending}
            title="Send (Enter)"
          >
            {postMutation.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Send size={12} />
            )}
            Send
          </button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1">
          Enter sends • Shift+Enter for newline
        </p>
      </div>
    </div>
  )
}

function MessageRow({
  message,
  onAccept,
  onDismiss,
  actionPending,
}: {
  message: RuleIntentMessage
  onAccept: (suggestionId: string) => void
  onDismiss: (suggestionId: string) => void
  actionPending: boolean
}) {
  const proposedText = message.suggestion_content?.proposed_text as string | undefined
  const rationale = message.suggestion_content?.rationale as string | undefined
  const status = message.suggestion_status

  return (
    <div className="space-y-1">
      <div className="flex items-start gap-1.5">
        <div className="bg-gray-100 rounded-lg px-2.5 py-1.5 max-w-full whitespace-pre-wrap text-gray-800">
          {message.body}
        </div>
        {message.decision_id && (
          <span
            className="text-[10px] text-indigo-500 mt-1 flex-shrink-0"
            title="Anchored to a specific post"
          >
            <Link2 size={10} className="inline" />
          </span>
        )}
      </div>

      {message.suggestion_id && proposedText && (
        <div
          className={`ml-4 border rounded-lg p-2 ${
            status === 'accepted'
              ? 'border-emerald-200 bg-emerald-50'
              : status === 'dismissed'
                ? 'border-gray-200 bg-gray-50 opacity-70'
                : 'border-indigo-200 bg-indigo-50'
          }`}
        >
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-indigo-700">
              Proposed rule edit
            </span>
            {status === 'accepted' && (
              <span className="text-[10px] text-emerald-700 font-medium">accepted</span>
            )}
            {status === 'dismissed' && (
              <span className="text-[10px] text-gray-500 font-medium">dismissed</span>
            )}
            {status === 'superseded' && (
              <span className="text-[10px] text-gray-500 font-medium">superseded</span>
            )}
          </div>
          {rationale && <p className="text-[11px] text-indigo-800 italic mb-1">{rationale}</p>}
          <pre className="whitespace-pre-wrap text-[11px] text-gray-700 bg-white border border-indigo-100 rounded p-1.5 font-sans">
            {proposedText}
          </pre>
          {status === 'pending' && message.suggestion_id && (
            <div className="flex gap-1.5 mt-1.5 justify-end">
              <button
                type="button"
                className="btn-secondary text-[10px] py-0.5"
                onClick={() => onDismiss(message.suggestion_id!)}
                disabled={actionPending}
              >
                <X size={10} /> Dismiss
              </button>
              <button
                type="button"
                className="btn-primary text-[10px] py-0.5"
                onClick={() => onAccept(message.suggestion_id!)}
                disabled={actionPending}
              >
                <Check size={10} /> Accept &amp; recompile
              </button>
            </div>
          )}
        </div>
      )}

      {!message.suggestion_id && message.no_suggestion_reason && (
        <p className="ml-4 text-[10px] text-gray-400 italic">
          {message.no_suggestion_reason}
        </p>
      )}
    </div>
  )
}
