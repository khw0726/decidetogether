import { useQuery } from '@tanstack/react-query'
import { BarChart2, TrendingUp, AlertTriangle, BookOpen } from 'lucide-react'
import { getDecisionStats, listRules, listDecisions, Rule, Decision } from '../api/client'

interface AlignmentDashboardProps {
  communityId: string
}

export default function AlignmentDashboard({ communityId }: AlignmentDashboardProps) {
  const { data: stats } = useQuery({
    queryKey: ['stats', communityId],
    queryFn: () => getDecisionStats(communityId),
    enabled: !!communityId,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['rules', communityId],
    queryFn: () => listRules(communityId),
    enabled: !!communityId,
  })

  const { data: decisions = [] } = useQuery({
    queryKey: ['decisions', communityId, 'resolved'],
    queryFn: () => listDecisions(communityId, { status: 'resolved', limit: 200 }),
    enabled: !!communityId,
  })

  if (!communityId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <p>Select a community to view alignment stats.</p>
      </div>
    )
  }

  // Compute per-rule override stats
  const ruleOverrides: Record<string, { title: string; total: number; overrides: number }> = {}
  decisions.forEach((d: Decision) => {
    if (d.was_override) {
      for (const ruleId of d.triggered_rules) {
        if (!ruleOverrides[ruleId]) {
          const rule = rules.find((r: Rule) => r.id === ruleId)
          ruleOverrides[ruleId] = { title: rule?.title || ruleId, total: 0, overrides: 0 }
        }
        ruleOverrides[ruleId].overrides += 1
      }
    }
    for (const ruleId of d.triggered_rules) {
      if (!ruleOverrides[ruleId]) {
        const rule = rules.find((r: Rule) => r.id === ruleId)
        ruleOverrides[ruleId] = { title: rule?.title || ruleId, total: 0, overrides: 0 }
      }
      ruleOverrides[ruleId].total += 1
    }
  })

  const sortedRuleOverrides = Object.entries(ruleOverrides)
    .map(([id, data]) => ({ id, ...data, rate: data.total > 0 ? data.overrides / data.total : 0 }))
    .sort((a, b) => b.overrides - a.overrides)
    .slice(0, 10)

  // Rules with few examples (< 3)
  const rulesNeedingExamples = rules
    .filter((r: Rule) => r.rule_type === 'actionable' && r.is_active)
    .slice(0, 5) // Simplified — would need example counts from API

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Alignment Dashboard</h1>

      {/* Stats overview */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatBox
          label="Override Rate"
          value={stats ? `${(stats.override_rate * 100).toFixed(1)}%` : '—'}
          icon={<TrendingUp size={20} className="text-purple-600" />}
          bg="bg-purple-50"
        />
        <StatBox
          label="Resolved"
          value={stats?.resolved_decisions ?? '—'}
          icon={<BarChart2 size={20} className="text-green-600" />}
          bg="bg-green-50"
        />
        <StatBox
          label="Active Rules"
          value={rules.filter((r: Rule) => r.is_active).length}
          icon={<BookOpen size={20} className="text-indigo-600" />}
          bg="bg-indigo-50"
        />
        <StatBox
          label="Actionable Rules"
          value={rules.filter((r: Rule) => r.rule_type === 'actionable' && r.is_active).length}
          icon={<AlertTriangle size={20} className="text-amber-600" />}
          bg="bg-amber-50"
        />
      </div>

      {/* Override reasons breakdown */}
      {stats && Object.keys(stats.override_categories).length > 0 && (
        <div className="card p-5">
          <h2 className="font-semibold text-gray-800 mb-4">Override Reason Breakdown</h2>
          <div className="space-y-3">
            {Object.entries(stats.override_categories).map(([cat, count]) => {
              const total = Object.values(stats.override_categories).reduce((a, b) => a + b, 0)
              const pct = Math.round((count / total) * 100)
              const labels: Record<string, string> = {
                agree: 'Agent was correct',
                rule_doesnt_apply: "Rule doesn't apply",
                edge_case_allow: 'Edge case — allowed',
                rule_needs_update: 'Rule needs update',
                agent_wrong_interpretation: 'Agent misinterpreted',
              }
              return (
                <div key={cat}>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span>{labels[cat] || cat}</span>
                    <span>{count} ({pct}%)</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-indigo-400 transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Most overridden rules */}
      {sortedRuleOverrides.length > 0 && (
        <div className="card p-5">
          <h2 className="font-semibold text-gray-800 mb-4">Most Overridden Rules</h2>
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
                    <td className="py-2 text-gray-800">{r.title}</td>
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
      )}

      {/* Rules needing attention */}
      {rulesNeedingExamples.length > 0 && (
        <div className="card p-5">
          <h2 className="font-semibold text-gray-800 mb-1">Actionable Rules</h2>
          <p className="text-xs text-gray-500 mb-4">
            All active actionable rules. Use the Rule Editor to add examples and improve coverage.
          </p>
          <div className="space-y-2">
            {rulesNeedingExamples.map((rule: Rule) => (
              <div
                key={rule.id}
                className="flex items-center gap-3 p-3 bg-gray-50 rounded border border-gray-200 text-sm"
              >
                <div className="flex-1">
                  <span className="font-medium">{rule.title}</span>
                </div>
                <span className="badge badge-green">{rule.rule_type}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!stats && decisions.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <BarChart2 size={48} className="mb-4 opacity-30" />
          <p className="text-lg font-medium">No alignment data yet</p>
          <p className="text-sm mt-1">
            Evaluate some posts and resolve decisions to see alignment stats.
          </p>
        </div>
      )}
    </div>
  )
}

function StatBox({
  label,
  value,
  icon,
  bg,
}: {
  label: string
  value: string | number
  icon: React.ReactNode
  bg: string
}) {
  return (
    <div className={`card p-4 ${bg} border-0`}>
      <div className="mb-2">{icon}</div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      <div className="text-sm font-medium text-gray-600 mt-0.5">{label}</div>
    </div>
  )
}
