import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BookOpen, Inbox, BarChart2, AlertTriangle, CheckCircle, Flag, Clock } from 'lucide-react'
import { listRules, getDecisionStats, getCommunity } from '../api/client'

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

  if (!communityId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <AlertTriangle size={48} className="mb-4 opacity-40" />
        <p className="text-lg font-medium">No community selected</p>
        <p className="text-sm mt-1">Select or create a community from the sidebar to get started.</p>
      </div>
    )
  }

  const activeRules = rules.filter(r => r.is_active).length
  const actionableRules = rules.filter(r => r.rule_type === 'actionable' && r.is_active).length

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">{community?.name || 'Dashboard'}</h1>
        <p className="text-sm text-gray-500 mt-1">
          {community?.platform} community — AI-assisted moderation
        </p>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          icon={<BookOpen size={20} className="text-indigo-600" />}
          label="Active Rules"
          value={activeRules}
          sub={`${actionableRules} actionable`}
          bg="bg-indigo-50"
        />
        <StatCard
          icon={<Clock size={20} className="text-amber-600" />}
          label="Pending Decisions"
          value={stats?.pending_decisions ?? '—'}
          sub="awaiting review"
          bg="bg-amber-50"
        />
        <StatCard
          icon={<BarChart2 size={20} className="text-purple-600" />}
          label="Override Rate"
          value={stats ? `${(stats.override_rate * 100).toFixed(1)}%` : '—'}
          sub={`${stats?.resolved_decisions ?? 0} resolved`}
          bg="bg-purple-50"
        />
        <StatCard
          icon={<CheckCircle size={20} className="text-green-600" />}
          label="Total Decisions"
          value={stats?.total_decisions ?? '—'}
          sub="all time"
          bg="bg-green-50"
        />
      </div>

      {/* Verdict breakdown */}
      {stats && (
        <div className="card p-5 mb-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">Agent Verdict Breakdown</h2>
          <div className="space-y-3">
            {Object.entries(stats.verdicts_breakdown).map(([verdict, count]) => {
              const total = stats.total_decisions || 1
              const pct = Math.round((count / total) * 100)
              const colors: Record<string, string> = {
                approve: 'bg-green-500',
                remove: 'bg-red-500',
                flag: 'bg-amber-500',
              }
              return (
                <div key={verdict}>
                  <div className="flex justify-between text-xs text-gray-600 mb-1">
                    <span className="capitalize">{verdict}</span>
                    <span>{count} ({pct}%)</span>
                  </div>
                  <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${colors[verdict] || 'bg-gray-400'} transition-all`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Quick links */}
      <div className="grid grid-cols-3 gap-4">
        <QuickLink
          to="/decisions"
          icon={<Inbox size={24} className="text-amber-600" />}
          label="Review Decisions"
          desc="Handle pending moderation decisions"
          highlight={!!stats?.pending_decisions}
        />
        <QuickLink
          to="/rules"
          icon={<BookOpen size={24} className="text-indigo-600" />}
          label="Edit Rules"
          desc="Manage rules and compiled checklists"
        />
        <QuickLink
          to="/alignment"
          icon={<BarChart2 size={24} className="text-purple-600" />}
          label="Alignment Stats"
          desc="Override patterns and rule suggestions"
        />
      </div>
    </div>
  )
}

function StatCard({
  icon,
  label,
  value,
  sub,
  bg,
}: {
  icon: React.ReactNode
  label: string
  value: string | number
  sub: string
  bg: string
}) {
  return (
    <div className={`card p-4 ${bg} border-0`}>
      <div className="flex items-center gap-2 mb-2">{icon}</div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      <div className="text-sm font-medium text-gray-700 mt-0.5">{label}</div>
      <div className="text-xs text-gray-500 mt-0.5">{sub}</div>
    </div>
  )
}

function QuickLink({
  to,
  icon,
  label,
  desc,
  highlight = false,
}: {
  to: string
  icon: React.ReactNode
  label: string
  desc: string
  highlight?: boolean
}) {
  return (
    <Link
      to={to}
      className={`card p-4 hover:shadow-md transition-shadow block ${highlight ? 'border-amber-300' : ''}`}
    >
      <div className="mb-2">{icon}</div>
      <div className="font-medium text-gray-900 text-sm">{label}</div>
      <div className="text-xs text-gray-500 mt-1">{desc}</div>
    </Link>
  )
}
