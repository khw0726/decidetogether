import React, { useMemo, useState } from 'react'
import {
  AlertCircle, CheckCircle, Image, Loader2, Play, Plus, X, XCircle,
} from 'lucide-react'
import { ChecklistItem, Decision, evaluatePost } from '../api/client'

function flattenChecklist(items: ChecklistItem[]): Record<string, ChecklistItem> {
  const map: Record<string, ChecklistItem> = {}
  const visit = (list: ChecklistItem[]) => {
    for (const item of list) {
      map[item.id] = item
      if (item.children?.length) visit(item.children)
    }
  }
  visit(items)
  return map
}

const VERDICT_STYLES: Record<string, string> = {
  approve: 'text-green-700 bg-green-50 border-green-200',
  warn: 'text-amber-700 bg-amber-50 border-amber-200',
  remove: 'text-red-700 bg-red-50 border-red-200',
  review: 'text-purple-700 bg-purple-50 border-purple-200',
  pending: 'text-gray-600 bg-gray-50 border-gray-200',
}

const VERDICT_HEADER_STYLES: Record<string, string> = {
  approve: 'bg-green-50 text-green-700',
  warn: 'bg-amber-50 text-amber-700',
  remove: 'bg-red-50 text-red-700',
  review: 'bg-purple-50 text-purple-700',
}

interface TestModalProps {
  communityId: string
  checklist: ChecklistItem[]
  onClose: () => void
}

export default function TestModal({ communityId, checklist, onClose }: TestModalProps) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [threadContext, setThreadContext] = useState('')
  const [imageUrls, setImageUrls] = useState<string[]>([])
  const [imageInput, setImageInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState<Decision | null>(null)
  const [error, setError] = useState<string | null>(null)

  const addImageUrl = () => {
    const url = imageInput.trim()
    if (url && !imageUrls.includes(url)) {
      setImageUrls(prev => [...prev, url])
    }
    setImageInput('')
  }

  const checklistMap = useMemo(() => flattenChecklist(checklist), [checklist])

  const handleTest = async () => {
    setIsLoading(true)
    setError(null)
    setResult(null)
    try {
      const post = {
        content: {
          title: title || undefined,
          body: body || undefined,
          ...(imageUrls.length ? { media: imageUrls } : {}),
        },
        ...(threadContext.trim() ? {
          context: { platform_metadata: { thread_context: threadContext } },
        } : {}),
      }
      const { decision } = await evaluatePost(communityId, post)
      setResult(decision)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Evaluation failed')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="card bg-white w-full max-w-5xl h-[80vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
          <h3 className="font-semibold text-sm flex items-center gap-1.5 text-gray-700">
            <Play size={13} className="text-indigo-500" />
            Test Post / Comment
          </h3>
          <div className="flex items-center gap-2">
            <button
              className="btn-primary text-xs"
              onClick={handleTest}
              disabled={isLoading || (!title.trim() && !body.trim())}
            >
              {isLoading
                ? <><Loader2 size={12} className="animate-spin" /> Testing...</>
                : <><Play size={12} /> Test</>
              }
            </button>
            <button
              className="text-gray-400 hover:text-gray-700 transition-colors"
              onClick={onClose}
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 flex min-h-0">
          {/* Input area */}
          <div className="w-1/2 flex flex-col gap-2 p-3 border-r border-gray-200 overflow-auto">
            <div className="flex-shrink-0">
              <label className="block text-xs font-medium text-gray-600 mb-0.5">Title</label>
              <input
                className="w-full border border-gray-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Post title (optional)"
              />
            </div>
            <div className="flex-1 flex flex-col min-h-0">
              <label className="block text-xs font-medium text-gray-600 mb-0.5">Content</label>
              <textarea
                className="flex-1 min-h-[50px] border border-gray-300 rounded px-2.5 py-1.5 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={body}
                onChange={e => setBody(e.target.value)}
                placeholder="Post or comment text..."
              />
            </div>
            <div className="flex-shrink-0">
              <label className="block text-xs font-medium text-gray-600 mb-0.5">
                Images{' '}
                <span className="text-gray-400 font-normal">(paste image URLs)</span>
              </label>
              <div className="flex gap-1.5 mb-1.5">
                <input
                  className="flex-1 border border-gray-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  value={imageInput}
                  onChange={e => setImageInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && addImageUrl()}
                  placeholder="https://..."
                />
                <button
                  className="btn-secondary text-xs flex items-center gap-1 px-2"
                  onClick={addImageUrl}
                  disabled={!imageInput.trim()}
                >
                  <Plus size={12} /> Add
                </button>
              </div>
              {imageUrls.length > 0 && (
                <ul className="space-y-1">
                  {imageUrls.map(url => (
                    <li key={url} className="flex items-center gap-1.5 text-xs bg-gray-50 border border-gray-200 rounded px-2 py-1">
                      <Image size={11} className="text-gray-400 flex-shrink-0" />
                      <span className="flex-1 truncate text-gray-600">{url}</span>
                      <button onClick={() => setImageUrls(prev => prev.filter(u => u !== url))} className="text-gray-400 hover:text-red-500">
                        <X size={11} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="flex-shrink-0">
              <label className="block text-xs font-medium text-gray-600 mb-0.5">
                Context{' '}
                <span className="text-gray-400 font-normal">(previous conversation / comment thread)</span>
              </label>
              <textarea
                className="w-full min-h-[50px] border border-gray-300 rounded px-2.5 py-1.5 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                value={threadContext}
                onChange={e => setThreadContext(e.target.value)}
                placeholder="Paste the comment thread or conversation context here (optional)..."
              />
            </div>
          </div>

          {/* Results area */}
          <div className="w-1/2 overflow-auto p-3">
            {error && (
              <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
                {error}
              </div>
            )}
            {isLoading && (
              <div className="flex items-center justify-center gap-2 mt-6 text-gray-400 text-sm">
                <Loader2 size={16} className="animate-spin" />
                Evaluating...
              </div>
            )}
            {!result && !error && !isLoading && (
              <p className="text-xs text-gray-400 text-center mt-6">
                Results will appear here after testing.
              </p>
            )}
            {result && (
              <TestResults result={result} checklistMap={checklistMap} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TestResults({
  result,
  checklistMap,
}: {
  result: Decision
  checklistMap: Record<string, ChecklistItem>
}) {
  const reasoning = result.agent_reasoning as Record<string, Record<string, unknown>>

  return (
    <div className="space-y-2">
      {/* Overall verdict banner */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded border text-sm font-medium ${VERDICT_STYLES[result.agent_verdict] || VERDICT_STYLES.pending}`}>
        <span className="uppercase text-xs tracking-wider font-semibold">{result.agent_verdict}</span>
        <span className="ml-auto text-xs opacity-75">
          {Math.round(result.agent_confidence * 100)}% confidence
        </span>
      </div>

      {/* Per-rule breakdown */}
      {Object.entries(reasoning).map(([ruleId, ruleData]) => {
        if (ruleId === '__community_norms__') {
          return (
            <div key={ruleId} className="border border-amber-200 rounded overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-1.5 bg-amber-50 text-xs font-medium text-amber-800">
                <AlertCircle size={12} />
                Community Norms
                <span className="ml-auto uppercase tracking-wider opacity-75">
                  {String(ruleData.verdict)}
                </span>
              </div>
              {!!ruleData.reasoning && (
                <div className="px-3 py-1.5 text-xs text-gray-600">
                  {String(ruleData.reasoning)}
                </div>
              )}
            </div>
          )
        }

        const verdict = String(ruleData.verdict || 'approve')
        const confidence = Number(ruleData.confidence ?? 0)
        const itemReasoning = (ruleData.item_reasoning ?? {}) as Record<string, Record<string, unknown>>

        const hasViolations = verdict !== 'approve'

        const renderNode = (itemId: string, depth: number): React.ReactNode => {
          const data = itemReasoning[itemId]
          if (!data) return null
          const triggered = Boolean(data.triggered)
          if (!triggered && hasViolations) return null
          const desc = String(checklistMap[itemId]?.description || data.description || itemId)
          const reasoningText = data.reasoning ? String(data.reasoning) : null
          const conf = Number(data.confidence ?? 0)
          const childEntries = Object.entries(itemReasoning)
            .filter(([_, d]) => d.parent_id === itemId)
            .sort(([idA], [idB]) => (checklistMap[idA]?.order ?? 0) - (checklistMap[idB]?.order ?? 0))
          return (
            <React.Fragment key={itemId}>
              <div
                style={{ paddingLeft: `${depth * 16 + 12}px` }}
                className={`flex items-start gap-2 pr-3 py-1.5 text-xs border-t border-gray-100 ${triggered ? 'bg-red-50' : ''}`}
              >
                {triggered
                  ? <XCircle size={12} className="text-red-500 mt-0.5 flex-shrink-0" />
                  : <CheckCircle size={12} className="text-green-500 mt-0.5 flex-shrink-0" />
                }
                <div className="flex-1 min-w-0">
                  <p className={`font-medium leading-tight ${triggered ? 'text-red-700' : 'text-gray-700'}`}>
                    {desc}
                  </p>
                  {reasoningText && (
                    <p className="text-gray-500 mt-0.5 leading-tight">{reasoningText}</p>
                  )}
                </div>
                <span className="text-gray-400 flex-shrink-0 mt-0.5">
                  {Math.round(conf * 100)}%
                </span>
              </div>
              {childEntries.map(([childId]) => renderNode(childId, depth + 1))}
            </React.Fragment>
          )
        }

        const rootEntries = Object.entries(itemReasoning)
          .filter(([_, data]) => !data.parent_id)
          .sort(([idA], [idB]) => (checklistMap[idA]?.order ?? 0) - (checklistMap[idB]?.order ?? 0))

        return (
          <div key={ruleId} className="border border-gray-200 rounded overflow-hidden">
            <div className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium ${VERDICT_HEADER_STYLES[verdict] || 'bg-gray-50 text-gray-600'}`}>
              <span className="flex-1 truncate">{String(ruleData.rule_title || ruleId)}</span>
              <span className="uppercase tracking-wider opacity-75 flex-shrink-0">{verdict}</span>
              <span className="opacity-60 flex-shrink-0">{Math.round(confidence * 100)}%</span>
            </div>

            {rootEntries.length > 0 && (
              <div>
                {rootEntries.map(([itemId]) => renderNode(itemId, 0))}
              </div>
            )}

            {rootEntries.length === 0 && (
              <div className="px-3 py-1.5 text-xs text-gray-400 border-t border-gray-100">
                No checklist items evaluated.
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
