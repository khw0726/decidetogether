import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, LayoutDashboard, BookOpen, BookMarked, Inbox, AlertTriangle, Settings, Plus, Trash2, ChevronLeft, ChevronRight } from 'lucide-react'
import { listCommunities, deleteCommunity, Community } from '../api/client'
import { useState } from 'react'

interface LayoutProps {
  communityId: string
  onCommunityChange: (id: string) => void
}

export default function Layout({ communityId, onCommunityChange }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: communities = [] } = useQuery({
    queryKey: ['communities'],
    queryFn: listCommunities,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteCommunity(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['communities'] })
      if (communityId === id) onCommunityChange('')
    },
  })

  const navItems = [
    { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/rules', icon: BookOpen, label: 'Rules Editor' },
    { to: '/examples', icon: BookMarked, label: 'Revise Rules from Decisions' },
    { to: '/decisions', icon: Inbox, label: 'Moderation Queue' },
    { to: '/overrides', icon: AlertTriangle, label: 'Unlinked Overrides' },
    { to: '/settings', icon: Settings, label: 'Community Profile' },
  ]

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className={`${sidebarOpen ? 'w-60' : 'w-12'} flex-shrink-0 bg-gray-900 text-gray-100 flex flex-col transition-all duration-200 overflow-hidden`}>
        {/* Logo */}
        <div className="flex items-center gap-2 px-3 py-4 border-b border-gray-700 min-w-0">
          <Shield size={22} className="text-indigo-400 flex-shrink-0" />
          {sidebarOpen && <span className="font-semibold text-sm truncate">AutoMod Agent</span>}
        </div>

        {/* Community selector */}
        {sidebarOpen && (
          <div className="px-3 py-3 border-b border-gray-700">
            <div className="text-xs uppercase tracking-wider text-gray-400 mb-2">Community</div>
            <div className="space-y-0.5 max-h-48 overflow-y-auto">
              {communities.length === 0 && (
                <p className="text-xs text-gray-500 py-1">No communities yet.</p>
              )}
              {communities.map((c: Community) => (
                <div
                  key={c.id}
                  className={`flex items-center gap-1 rounded px-2 py-1.5 cursor-pointer group ${communityId === c.id ? 'bg-indigo-700' : 'hover:bg-gray-800'}`}
                  onClick={() => onCommunityChange(c.id)}
                >
                  <span className="flex-1 text-sm truncate">{c.name}</span>
                  <button
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-400 transition-all flex-shrink-0"
                    title={`Delete ${c.name}`}
                    onClick={e => {
                      e.stopPropagation()
                      if (confirm(`Delete ${c.name} and all its data?`)) deleteMutation.mutate(c.id)
                    }}
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
            <button
              className="mt-2 w-full flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-100 transition-colors py-1"
              onClick={() => navigate('/setup')}
            >
              <Plus size={12} />
              New community
            </button>
          </div>
        )}

        {/* Navigation */}
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              title={!sidebarOpen ? label : undefined}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-2 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-indigo-700 text-white'
                    : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                }`
              }
            >
              <Icon size={16} className="flex-shrink-0" />
              {sidebarOpen && label}
            </NavLink>
          ))}
        </nav>

        {/* Footer / Toggle */}
        <div className="border-t border-gray-700 flex items-center">
          {sidebarOpen && (
            <span className="flex-1 px-4 py-3 text-xs text-gray-500 truncate">
              v2.1.0 — All decisions require human review
            </span>
          )}
          <button
            onClick={() => setSidebarOpen(o => !o)}
            className={`${sidebarOpen ? 'px-2' : 'w-full justify-center'} flex items-center py-3 text-gray-500 hover:text-gray-100 transition-colors`}
            title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {sidebarOpen ? <ChevronLeft size={16} /> : <ChevronRight size={16} />}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>

    </div>
  )
}
