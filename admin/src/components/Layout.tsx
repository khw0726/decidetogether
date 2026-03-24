import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Shield, LayoutDashboard, BookOpen, Inbox, BarChart2, Plus, ChevronLeft, ChevronRight } from 'lucide-react'
import { listCommunities, createCommunity, Community } from '../api/client'
import { useState } from 'react'

interface LayoutProps {
  communityId: string
  onCommunityChange: (id: string) => void
}

export default function Layout({ communityId, onCommunityChange }: LayoutProps) {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const navigate = useNavigate()

  const { data: communities = [], refetch } = useQuery({
    queryKey: ['communities'],
    queryFn: listCommunities,
  })

  const navItems = [
    { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/rules', icon: BookOpen, label: 'Rules' },
    { to: '/decisions', icon: Inbox, label: 'Decisions' },
    { to: '/alignment', icon: BarChart2, label: 'Alignment' },
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
            <select
              className="w-full bg-gray-800 text-gray-100 text-sm rounded px-2 py-1.5 border border-gray-600 focus:outline-none focus:border-indigo-500"
              value={communityId}
              onChange={e => onCommunityChange(e.target.value)}
            >
              <option value="">Select community...</option>
              {communities.map((c: Community) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.platform})
                </option>
              ))}
            </select>
            <button
              className="mt-2 w-full flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-100 transition-colors py-1"
              onClick={() => setShowCreateModal(true)}
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
              v2.0.0 — All decisions require human review
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

      {/* Create Community Modal */}
      {showCreateModal && (
        <CreateCommunityModal
          onClose={() => setShowCreateModal(false)}
          onCreate={async (name, platform) => {
            const comm = await createCommunity({ name, platform })
            await refetch()
            onCommunityChange(comm.id)
            setShowCreateModal(false)
          }}
        />
      )}
    </div>
  )
}

function CreateCommunityModal({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (name: string, platform: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [platform, setPlatform] = useState('reddit')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setLoading(true)
    setError('')
    try {
      await onCreate(name.trim(), platform)
    } catch (err) {
      setError('Failed to create community')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="card p-6 w-full max-w-sm">
        <h2 className="text-lg font-semibold mb-4">New Community</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Community Name</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="e.g., r/programming"
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Platform</label>
            <select
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={platform}
              onChange={e => setPlatform(e.target.value)}
            >
              <option value="reddit">Reddit</option>
              <option value="chatroom">Chatroom</option>
              <option value="forum">Forum</option>
            </select>
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex gap-2 justify-end">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={loading || !name.trim()}>
              {loading ? 'Creating...' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
