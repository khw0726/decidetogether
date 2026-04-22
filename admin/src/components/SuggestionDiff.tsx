import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X, AlertCircle } from 'lucide-react'
import { acceptSuggestionWithLabel, acceptRecompile, dismissSuggestion, Suggestion } from '../api/client'

interface SuggestionDiffProps {
  suggestions: Suggestion[]
  ruleId: string
  currentRuleText?: string
  onClose: () => void
}

export default function SuggestionDiff({ suggestions, ruleId, currentRuleText, onClose }: SuggestionDiffProps) {
  const queryClient = useQueryClient()

  const acceptMutation = useMutation({
    mutationFn: ({ suggestion, labelOverride }: { suggestion: Suggestion; labelOverride?: string }) =>
      suggestion.suggestion_type === 'checklist'
        ? acceptRecompile(ruleId, suggestion.id)
        : acceptSuggestionWithLabel(suggestion.id, labelOverride),
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
              currentRuleText={currentRuleText}
              onAccept={(labelOverride?: string) => acceptMutation.mutate({ suggestion, labelOverride })}
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
  currentRuleText,
  onAccept,
  onDismiss,
  isPending,
}: {
  suggestion: Suggestion
  currentRuleText?: string
  onAccept: (labelOverride?: string) => void
  onDismiss: () => void
  isPending: boolean
}) {
  const typeLabels: Record<string, string> = {
    checklist: 'Checklist Update',
    rule_text: 'Rule Text Update',
    example: 'New Example',
    new_rule: 'New Rule',
  }

  const content = suggestion.content as Record<string, unknown>

  // Check if this is a borderline example suggestion requiring a verdict
  const isBorderlineExample =
    suggestion.suggestion_type === 'example' && content.label === 'borderline'

  return (
    <div className="border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-3">
            <span className="badge badge-blue">{typeLabels[suggestion.suggestion_type] || suggestion.suggestion_type}</span>
            {isBorderlineExample && (
              <span className="badge badge-yellow">needs verdict</span>
            )}
          </div>
          <SuggestionBody type={suggestion.suggestion_type} content={content} currentRuleText={currentRuleText} />
        </div>
        <div className="flex flex-col gap-1.5 flex-shrink-0">
          {isBorderlineExample ? (
            <>
              <button
                className="btn-success text-xs py-1"
                onClick={() => onAccept('compliant')}
                disabled={isPending}
                title="Mark as compliant"
              >
                <Check size={12} />
                Compliant
              </button>
              <button
                className="btn-danger text-xs py-1"
                onClick={() => onAccept('violating')}
                disabled={isPending}
                title="Mark as violating"
              >
                <X size={12} />
                Violating
              </button>
            </>
          ) : (
            <>
              <button
                className="btn-success text-xs py-1"
                onClick={() => onAccept()}
                disabled={isPending}
                title="Accept suggestion"
              >
                <Check size={12} />
                Accept
              </button>
            </>
          )}
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

function SuggestionBody({ type, content, currentRuleText }: { type: string; content: Record<string, unknown>; currentRuleText?: string }) {
  if (type === 'rule_text') {
    return <RuleTextSuggestion content={content} currentRuleText={currentRuleText} />
  }
  if (type === 'example') {
    return <ExampleSuggestion content={content} />
  }
  if (type === 'checklist') {
    return <ChecklistSuggestion content={content} />
  }
  if (type === 'new_rule') {
    return <NewRuleSuggestion content={content} />
  }
  // Fallback
  const description = (content.description as string) || ''
  const reasoning = (content.reasoning as string) || ''
  return (
    <>
      {description && <p className="text-sm font-medium mb-1">{description}</p>}
      {reasoning && <p className="text-xs text-gray-500">{reasoning}</p>}
    </>
  )
}

type DiffToken = { type: 'equal' | 'remove' | 'add'; text: string }

function lcs<T>(a: T[], b: T[], eq: (x: T, y: T) => boolean): Array<{ type: 'equal' | 'remove' | 'add'; val: T }> {
  const m = a.length, n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = eq(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const ops: Array<{ type: 'equal' | 'remove' | 'add'; val: T }> = []
  let i = 0, j = 0
  while (i < m || j < n) {
    if (i < m && j < n && eq(a[i], b[j])) {
      ops.push({ type: 'equal', val: a[i] }); i++; j++
    } else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) {
      ops.push({ type: 'add', val: b[j] }); j++
    } else {
      ops.push({ type: 'remove', val: a[i] }); i++
    }
  }
  return ops
}

function wordDiff(oldText: string, newText: string): DiffToken[] {
  const tokenize = (s: string) => s.split(/(\s+)/)
  return lcs(tokenize(oldText), tokenize(newText), (a, b) => a === b)
    .map(op => ({ type: op.type, text: op.val }))
}

function InlineDiff({ oldText, newText }: { oldText: string; newText: string }) {
  const tokens = wordDiff(oldText, newText)
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">
      {tokens.map((tok, idx) => {
        if (tok.type === 'equal') return <span key={idx}>{tok.text}</span>
        if (tok.type === 'remove') return (
          <span key={idx} className="bg-red-100 text-red-700 line-through decoration-red-400">{tok.text}</span>
        )
        return (
          <span key={idx} className="bg-green-100 text-green-800 font-medium">{tok.text}</span>
        )
      })}
    </p>
  )
}

function ParagraphDiff({ oldText, newText }: { oldText: string; newText: string }) {
  const splitParas = (s: string) => s.split(/\n\n+/)
  const ops = lcs(splitParas(oldText), splitParas(newText), (a, b) => a === b)

  const nodes: React.ReactNode[] = []
  let i = 0
  while (i < ops.length) {
    const op = ops[i]
    if (op.type === 'equal') {
      nodes.push(
        <p key={i} className="text-sm text-gray-700 leading-relaxed py-0.5">{op.val}</p>
      )
      i++
    } else {
      // Collect a run of non-equal ops, then pair removes+adds
      const removes: string[] = []
      const adds: string[] = []
      const runStart = i
      while (i < ops.length && ops[i].type !== 'equal') {
        if (ops[i].type === 'remove') removes.push(ops[i].val)
        else adds.push(ops[i].val)
        i++
      }
      const pairs = Math.min(removes.length, adds.length)
      // Paired: show word-level inline diff
      for (let k = 0; k < pairs; k++) {
        nodes.push(
          <div key={`pair-${runStart}-${k}`} className="border border-amber-200 rounded px-3 py-2 my-1 bg-amber-50">
            <InlineDiff oldText={removes[k]} newText={adds[k]} />
          </div>
        )
      }
      // Unpaired removes
      for (let k = pairs; k < removes.length; k++) {
        nodes.push(
          <div key={`rem-${runStart}-${k}`} className="border-l-4 border-red-400 bg-red-50 pl-3 py-1.5 my-1 rounded-r">
            <p className="text-xs text-red-500 font-semibold mb-0.5">Removed</p>
            <p className="text-sm text-red-700 leading-relaxed line-through">{removes[k]}</p>
          </div>
        )
      }
      // Unpaired adds
      for (let k = pairs; k < adds.length; k++) {
        nodes.push(
          <div key={`add-${runStart}-${k}`} className="border-l-4 border-green-400 bg-green-50 pl-3 py-1.5 my-1 rounded-r">
            <p className="text-xs text-green-600 font-semibold mb-0.5">Added</p>
            <p className="text-sm text-green-800 leading-relaxed">{adds[k]}</p>
          </div>
        )
      }
    }
  }

  return <div className="space-y-1">{nodes}</div>
}

function extractProposedRuleText(content: Record<string, unknown>): string {
  if (typeof content.proposed_text === 'string') return content.proposed_text
  const change = content.proposed_change as Record<string, unknown> | undefined
  if (change && typeof change.text === 'string') return change.text
  if (change && typeof change.proposed_text === 'string') return change.proposed_text
  return ''
}

function RuleTextSuggestion({ content, currentRuleText }: { content: Record<string, unknown>; currentRuleText?: string }) {
  const description = (content.description as string) || ''
  const reasoning = (content.reasoning as string) || ''
  const proposedText = extractProposedRuleText(content)

  return (
    <div className="space-y-2">
      {description && <p className="text-sm font-medium text-gray-800">{description}</p>}
      {proposedText ? (
        <div className="border border-gray-200 rounded p-3 bg-gray-50">
          {currentRuleText
            ? <ParagraphDiff oldText={currentRuleText} newText={proposedText} />
            : <p className="text-sm text-gray-700 whitespace-pre-wrap">{proposedText}</p>
          }
        </div>
      ) : (
        <p className="text-xs text-red-400 italic">No proposed text found in suggestion payload.</p>
      )}
      {reasoning && <p className="text-xs text-gray-500 italic">{reasoning}</p>}
    </div>
  )
}

const labelColors: Record<string, string> = {
  compliant: 'bg-green-100 text-green-800',
  violating: 'bg-red-100 text-red-800',
  borderline: 'bg-amber-100 text-amber-800',
}

function ExampleSuggestion({ content }: { content: Record<string, unknown> }) {
  const label = (content.label as string) || 'compliant'
  const relevanceNote = (content.relevance_note as string) || ''
  const postBody = (content.content as Record<string, unknown>) || {}
  const inner = (postBody.content as Record<string, unknown>) || {}
  const title = inner.title as string | undefined
  const body = inner.body as string | undefined
  const author = postBody.author as { username: string } | undefined
  const postType = postBody.type as string | undefined

  return (
    <div className="space-y-2">
      <div className="bg-gray-50 border border-gray-200 rounded p-3 space-y-1.5 text-sm">
        {title && <p className="font-medium text-gray-800">{title}</p>}
        {body && <p className="text-gray-600 text-xs leading-relaxed line-clamp-4">{body}</p>}
        {/* {context && <p className="text-xs text-gray-400 italic">{context}</p>} */}
        {author && <p className="text-xs text-gray-400">u/{author.username}{postType && ` · ${postType}`}</p>}
        {!title && !body && <p className="text-xs text-gray-400 italic">No post content preview available.</p>}
      </div>
      <div className="flex items-start gap-2">
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full flex-shrink-0 ${labelColors[label] || 'bg-gray-100 text-gray-700'}`}>
          {label}
        </span>
        {relevanceNote && <span className="text-xs text-gray-500 italic">{relevanceNote}</span>}
      </div>
    </div>
  )
}

function ChecklistSuggestion({ content }: { content: Record<string, unknown> }) {
  const description = (content.description as string) || ''
  const reasoning = (content.reasoning as string) || ''
  const proposed = content.proposed_change as Record<string, unknown> | null | undefined

  const itemTypeColors: Record<string, string> = {
    deterministic: 'bg-violet-100 text-violet-800',
    structural: 'bg-cyan-100 text-cyan-800',
    subjective: 'bg-orange-100 text-orange-800',
  }
  const actionColors: Record<string, string> = {
    remove: 'bg-red-100 text-red-700',
    warn: 'bg-amber-100 text-amber-700',
    continue: 'bg-gray-100 text-gray-600',
  }

  const op = proposed?.op as string | undefined
  const opLabels: Record<string, string> = { add: 'Add item', update: 'Update item', delete: 'Delete item', keep: 'Keep item' }
  const opBorderColors: Record<string, string> = {
    add: 'border-green-400 bg-green-50',
    update: 'border-blue-400 bg-blue-50',
    delete: 'border-red-400 bg-red-50',
    keep: 'border-gray-300 bg-gray-50',
  }
  const opBadgeColors: Record<string, string> = {
    add: 'bg-green-100 text-green-700',
    update: 'bg-blue-100 text-blue-700',
    delete: 'bg-red-100 text-red-700',
    keep: 'bg-gray-100 text-gray-600',
  }

  const itemDescription = proposed?.description as string | undefined
  const itemType = proposed?.item_type as string | undefined
  const action = proposed?.action as string | undefined
  const anchor = proposed?.rule_text_anchor as string | undefined

  return (
    <div className="space-y-2">
      {proposed ? (
        <div className={`border-l-4 rounded-r p-3 space-y-2 ${opBorderColors[op || ''] || 'border-gray-300 bg-gray-50'}`}>
          <div className="flex flex-wrap gap-1.5 items-center">
            {op && (
              <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${opBadgeColors[op] || 'bg-gray-100 text-gray-600'}`}>
                {opLabels[op] || op}
              </span>
            )}
            {itemType && (
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${itemTypeColors[itemType] || 'bg-gray-100 text-gray-600'}`}>
                {itemType}
              </span>
            )}
            {action && (
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${actionColors[action] || 'bg-gray-100 text-gray-600'}`}>
                action: {action}
              </span>
            )}
          </div>
          {itemDescription && <p className="text-sm text-gray-800 font-medium">{itemDescription}</p>}
          {anchor && (
            <p className="text-xs text-gray-500">
              <span className="font-medium">Anchored to:</span> &ldquo;{anchor}&rdquo;
            </p>
          )}
        </div>
      ) : (
        description && <p className="text-sm font-medium text-gray-800">{description}</p>
      )}
      {(reasoning || description) && (
        <p className="text-xs text-gray-500 italic">{reasoning || description}</p>
      )}
    </div>
  )
}

function NewRuleSuggestion({ content }: { content: Record<string, unknown> }) {
  const title = (content.title as string) || ''
  const text = (content.text as string) || ''
  const reasoning = (content.reasoning as string) || ''

  return (
    <div className="space-y-2">
      {reasoning && <p className="text-xs text-gray-500 italic">{reasoning}</p>}
      {title && <p className="text-sm font-semibold text-gray-800">{title}</p>}
      {text && (
        <blockquote className="border-l-4 border-indigo-300 pl-3 text-sm text-gray-700 bg-indigo-50 py-2 pr-3 rounded-r">
          {text}
        </blockquote>
      )}
    </div>
  )
}
