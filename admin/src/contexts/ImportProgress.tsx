import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { listDecisions } from '../api/client'

interface ImportInFlight {
  communityId: string
  baselineCount: number
  expected: number
  startedAt: number
}

interface ImportProgressValue {
  importInFlight: ImportInFlight | null
  arrivedCount: number
  startImport: (i: Omit<ImportInFlight, 'startedAt'>) => void
  clear: () => void
}

const ImportProgressContext = createContext<ImportProgressValue | null>(null)

const STORAGE_KEY = 'importInFlight'
const TIMEOUT_MS = 90_000
const POLL_MS = 2_000

export function ImportProgressProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [importInFlight, setImportInFlight] = useState<ImportInFlight | null>(() => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY)
      if (!raw) return null
      const parsed = JSON.parse(raw) as ImportInFlight
      // Drop a stale latch if the session predated the timeout window.
      if (Date.now() - parsed.startedAt > TIMEOUT_MS) return null
      return parsed
    } catch {
      return null
    }
  })
  const [arrivedCount, setArrivedCount] = useState(0)

  // Persist
  useEffect(() => {
    if (importInFlight) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(importInFlight))
    else sessionStorage.removeItem(STORAGE_KEY)
  }, [importInFlight])

  // Poll while in-flight, regardless of which page is mounted.
  useEffect(() => {
    if (!importInFlight) {
      setArrivedCount(0)
      return
    }
    let cancelled = false
    const tick = async () => {
      try {
        const decs = await listDecisions(importInFlight.communityId, { limit: 200 })
        if (cancelled) return
        const arrived = Math.max(0, decs.length - importInFlight.baselineCount)
        setArrivedCount(arrived)
        // Refresh any mounted decisions queries so the queue reflects the new arrivals.
        queryClient.invalidateQueries({ queryKey: ['decisions', importInFlight.communityId] })
        if (arrived >= importInFlight.expected) {
          setImportInFlight(null)
          return
        }
        if (Date.now() - importInFlight.startedAt > TIMEOUT_MS) {
          setImportInFlight(null)
          return
        }
      } catch {
        // Transient errors shouldn't kill the latch — next tick will retry.
      }
    }
    tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [importInFlight, queryClient])

  const startImport = useCallback((i: Omit<ImportInFlight, 'startedAt'>) => {
    setImportInFlight({ ...i, startedAt: Date.now() })
  }, [])

  const clear = useCallback(() => setImportInFlight(null), [])

  return (
    <ImportProgressContext.Provider value={{ importInFlight, arrivedCount, startImport, clear }}>
      {children}
    </ImportProgressContext.Provider>
  )
}

export function useImportProgress(): ImportProgressValue {
  const v = useContext(ImportProgressContext)
  if (!v) throw new Error('useImportProgress must be used inside ImportProgressProvider')
  return v
}
