import { createContext, useContext, useEffect, useRef, ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getReevalStatus, ReevalStatus } from '../api/client'

interface ReevalStatusValue {
  status: ReevalStatus | null
}

const ReevalStatusContext = createContext<ReevalStatusValue | null>(null)

interface Props {
  communityId: string
  children: ReactNode
}

/**
 * Global poller for the per-community reeval/compile status.
 *
 * Lives above the route tree so polling, banner visibility, and the
 * "in_progress → idle" transition that re-fetches decisions all keep working
 * regardless of which page is mounted. Without this, switching off the queue
 * dropped the banner and silenced the post-reeval decisions refresh.
 */
export function ReevalStatusProvider({ communityId, children }: Props) {
  const queryClient = useQueryClient()

  const { data: status = null } = useQuery({
    queryKey: ['reeval-status', communityId],
    queryFn: () => getReevalStatus(communityId),
    enabled: !!communityId,
    refetchInterval: (q) => (q.state.data?.in_progress ? 1500 : 5000),
  })

  // Force a decisions refetch on the in_progress → idle edge so verdicts land
  // promptly without waiting for the queue's own 30s interval. The ref is held
  // at the provider level so the edge survives page navigation.
  const wasInProgress = useRef(false)
  useEffect(() => {
    if (!communityId) return
    const inProgress = !!status?.in_progress
    if (wasInProgress.current && !inProgress) {
      queryClient.invalidateQueries({ queryKey: ['decisions', communityId] })
    }
    wasInProgress.current = inProgress
  }, [status?.in_progress, communityId, queryClient])

  return (
    <ReevalStatusContext.Provider value={{ status }}>
      {children}
    </ReevalStatusContext.Provider>
  )
}

export function useReevalStatus(): ReevalStatusValue {
  const v = useContext(ReevalStatusContext)
  if (!v) throw new Error('useReevalStatus must be used inside ReevalStatusProvider')
  return v
}
