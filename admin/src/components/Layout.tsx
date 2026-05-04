import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, Settings, Inbox, BookOpen, Plus, Trash2, ChevronDown, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { listCommunities, deleteCommunity, Community } from '../api/client'
import { useImportProgress } from '../contexts/ImportProgress'
import { useReevalStatus } from '../contexts/ReevalStatus'

interface LayoutProps {
  communityId: string
  onCommunityChange: (id: string) => void
}

const NAV_ITEMS = [
  { to: '/settings', icon: Settings, label: 'Community Profile' },
  { to: '/decisions', icon: Inbox, label: 'Moderation Queue' },
  { to: '/editor', icon: BookOpen, label: 'Rules & Logics Editor' },
]

export default function Layout({ communityId, onCommunityChange }: LayoutProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [communityMenuOpen, setCommunityMenuOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement | null>(null)

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

  const activeCommunity = communities.find((c: Community) => c.id === communityId) ?? null
  const { importInFlight, arrivedCount } = useImportProgress()
  // Only show the global banner when the import targets the active community —
  // otherwise it's noise across an unrelated workspace.
  const importBannerVisible = importInFlight && importInFlight.communityId === communityId
  const { status: reevalStatus } = useReevalStatus()

  useEffect(() => {
    if (!communityMenuOpen) return
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setCommunityMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [communityMenuOpen])

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      {/* Top nav */}
      <header className="flex items-center bg-gray-900 text-gray-100 h-12 flex-shrink-0 shadow-sm">
        {/* Logo */}
        <div className="flex items-center gap-2 px-4 h-full border-r border-gray-800">
          <Shield size={18} className="text-indigo-400 flex-shrink-0" />
          <span className="font-semibold text-sm whitespace-nowrap">AutoMod Agent</span>
        </div>

        {/* Tabs */}
        <nav className="flex items-center h-full">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-1.5 h-full px-4 text-sm border-b-2 transition-colors ${
                  isActive
                    ? 'border-indigo-400 text-white bg-gray-800'
                    : 'border-transparent text-gray-300 hover:text-white hover:bg-gray-800'
                }`
              }
            >
              <Icon size={14} className="flex-shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Community selector (right) */}
        <div className="ml-auto h-full flex items-center pr-3" ref={dropdownRef}>
          <div className="relative">
            <button
              className="flex items-center gap-2 text-sm px-3 py-1.5 rounded border border-gray-700 bg-gray-800 hover:bg-gray-700 transition-colors"
              onClick={() => setCommunityMenuOpen(o => !o)}
            >
              <span className="max-w-48 truncate">
                {activeCommunity ? activeCommunity.name : 'Select community'}
              </span>
              <ChevronDown size={14} className="text-gray-400" />
            </button>

            {communityMenuOpen && (
              <div className="absolute right-0 mt-1.5 w-64 bg-gray-900 border border-gray-700 rounded-md shadow-lg z-50 overflow-hidden">
                <div className="px-3 py-2 text-xs uppercase tracking-wider text-gray-500 border-b border-gray-800">
                  Communities
                </div>
                <div className="max-h-64 overflow-y-auto">
                  {communities.length === 0 && (
                    <p className="text-xs text-gray-500 px-3 py-2">No communities yet.</p>
                  )}
                  {communities.map((c: Community) => (
                    <div
                      key={c.id}
                      className={`flex items-center gap-1 px-3 py-2 cursor-pointer group ${communityId === c.id ? 'bg-indigo-700 text-white' : 'hover:bg-gray-800 text-gray-200'}`}
                      onClick={() => {
                        onCommunityChange(c.id)
                        setCommunityMenuOpen(false)
                      }}
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
                  className="w-full flex items-center gap-1.5 px-3 py-2 text-xs text-gray-400 hover:bg-gray-800 hover:text-white transition-colors border-t border-gray-800"
                  onClick={() => {
                    setCommunityMenuOpen(false)
                    navigate('/setup')
                  }}
                >
                  <Plus size={12} />
                  New community
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Global reeval/compile-progress banner — also persists across pages. */}
      {reevalStatus?.in_progress && (
        <div className="flex items-center gap-2 px-4 py-1.5 border-b border-amber-200 bg-amber-50 text-amber-800 text-xs flex-shrink-0">
          <Loader2 size={12} className="animate-spin" />
          {reevalStatus.rules_compiling.length > 0 && reevalStatus.rules_reevaluating.length === 0
            ? `Compiling rule logic (${reevalStatus.rules_compiling.length})…`
            : reevalStatus.rules_reevaluating.length > 0 && reevalStatus.rules_compiling.length === 0
              ? `Re-evaluating queue against updated rule logic (${reevalStatus.rules_reevaluating.length} rule${reevalStatus.rules_reevaluating.length === 1 ? '' : 's'})…`
              : `Compiling and re-evaluating rules (${reevalStatus.rules_compiling.length + reevalStatus.rules_reevaluating.length})…`}
          <span className="text-amber-700/70">Verdicts will refresh automatically when this finishes.</span>
        </div>
      )}

      {/* Global import-progress banner — visible from any page so polling and
          progress feedback survive page changes. */}
      {importBannerVisible && (
        <div className="flex items-center gap-2 px-4 py-1.5 border-b border-indigo-200 bg-indigo-50 text-indigo-800 text-xs flex-shrink-0">
          <Loader2 size={12} className="animate-spin" />
          Importing and evaluating {importInFlight!.expected} new post{importInFlight!.expected === 1 ? '' : 's'}
          {' '}({Math.min(arrivedCount, importInFlight!.expected)} ready)…
          <span className="text-indigo-700/70">Decisions will appear in the moderation queue as they finish.</span>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}
