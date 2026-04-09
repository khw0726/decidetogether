import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Inbox, AlertTriangle, BookOpen } from 'lucide-react'
import { listRules, getDecisionStats, getCommunity, listDecisions, Rule, Decision } from '../api/client'

interface DashboardProps {
  communityId: string
}

export default function Dashboard({ communityId }: DashboardProps) {
  const { data: community } = useQuery({
    queryKey: ['community', communityId],
    queryFn: () => getCommunity(communityId),
    enabled: !!communityId,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const { data: stats } = useQuery({
    queryKey: ['stats', communityId],
    queryFn: () => getDecisionStats(communityId),
    enabled: !!communityId,
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', communityId, 'resolved'],
    queryFn: () => listDecisions(communityId, { status: 'resolved', limit: 200 }),
    enabled: !!communityId,
  })

  if (!communityId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <AlertTriangle size={48} className="mb-4 opacity-40" />
        <p className="text-lg font-medium">No community selected</p>
        <p className="text-sm mt-1">Select or create a community from the sidebar to get started.</p>
      </div>
    )
  }

  // Compute per-rule override stats from resolved decisions
  const ruleOverrides: Record<string, { title: string; total: number; overrides: number }> = {}
  decisions.forEach((d: Decision) => {
    for (const ruleId of d.triggered_rules) {
      if (!ruleOverrides[ruleId]) {
        const rule = rules.find((r: Rule) => r.id === ruleId)
        ruleOverrides[ruleId] = { title: rule?.title || ruleId, total: 0, overrides: 0 }
      }
      ruleOverrides[ruleId].total += 1
      if (d.was_override) {
        ruleOverrides[ruleId].overrides += 1
      }
    }
  })

  const sortedRuleOverrides = Object.entries(ruleOverrides)
    .map(([id, data]) => ({ id, ...data, rate: data.total > 0 ? data.overrides / data.total : 0 }))
    .sort((a, b) => b.overrides - a.overrides)
    .slice(0, 10)

  const pendingCount = stats?.pending_decisions ?? 0

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">{community?.name || 'Dashboard'}</h1>
        <p className="text-sm text-gray-500 mt-1">
          {community?.platform} community — AI-assisted moderation
        </p>
      </div>

      {/* Decision queue banner */}
      <Link
        to="/decisions"
        className={`flex items-center gap-4 p-5 rounded-lg mb-8 transition-shadow hover:shadow-md ${
          pendingCount > 0
            ? 'bg-amber-50 border border-amber-200'
            : 'bg-green-50 border border-green-200'
        }`}
      >
        <Inbox size={28} className={pendingCount > 0 ? 'text-amber-600' : 'text-green-600'} />
        <div>
          <div className="text-2xl font-bold text-gray-900">{pendingCount}</div>
          <div className="text-sm text-gray-600">
            {pendingCount === 1 ? 'decision' : 'decisions'} awaiting review
          </div>
        </div>
      </Link>

      {/* Per-rule override table */}
      {sortedRuleOverrides.length > 0 ? (
        <div className="card p-5">
          <h2 className="font-semibold text-gray-800 mb-1">Overrides by Rule</h2>
          <p className="text-xs text-gray-500 mb-4">
            Rules with frequent overrides may need checklist or rule text updates.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 border-b border-gray-200">
                  <th className="text-left pb-2 font-medium">Rule</th>
                  <th className="text-right pb-2 font-medium">Triggered</th>
                  <th className="text-right pb-2 font-medium">Overrides</th>
                  <th className="text-right pb-2 font-medium">Override Rate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sortedRuleOverrides.map(r => (
                  <tr key={r.id} className="hover:bg-gray-50">
                    <td className="py-2 text-gray-800">
                      <Link to="/rules" className="hover:underline">{r.title}</Link>
                    </td>
                    <td className="py-2 text-right text-gray-500">{r.total}</td>
                    <td className="py-2 text-right text-red-600">{r.overrides}</td>
                    <td className="py-2 text-right">
                      <span className={`font-medium ${r.rate > 0.3 ? 'text-red-600' : r.rate > 0.1 ? 'text-amber-600' : 'text-green-600'}`}>
                        {(r.rate * 100).toFixed(0)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sortedRuleOverrides.some(r => r.rate > 0.3) && (
            <div className="mt-4 p-3 bg-amber-50 rounded border border-amber-200 text-xs text-amber-800">
              <AlertTriangle size={14} className="inline mr-1" />
              Some rules have high override rates. Consider reviewing their checklists or rule text.
            </div>
          )}
        </div>
      ) : (
        <div className="card p-5 text-center text-gray-400">
          <BookOpen size={32} className="mx-auto mb-2 opacity-30" />
          <p className="text-sm">No resolved decisions yet. Override stats will appear here once moderators start reviewing.</p>
        </div>
      )}
    </div>
  )
}
