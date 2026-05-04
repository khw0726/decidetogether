import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getCommunity } from './api/client'
import { setTelemetryContext, clearTelemetryContext } from './telemetry'
import Layout from './components/Layout'
import RulesLogicsEditor from './pages/RulesLogicsEditor'
import DecisionQueue from './pages/DecisionQueue'
import CommunitySettings from './pages/CommunitySettings'
import CommunitySetup from './pages/CommunitySetup'
import CommunityFeedView from './pages/CommunityFeedView'
import UnlinkedOverridesPage from './pages/UnlinkedOverridesPage'
import ToastContainer from './components/Toast'
import { ReevalStatusProvider } from './contexts/ReevalStatus'

export default function App() {
  const [communityId, setCommunityId] = useState<string>(
    () => localStorage.getItem('activeCommunityId') ?? ''
  )

  const { data: activeCommunity } = useQuery({
    queryKey: ['community', communityId],
    queryFn: () => getCommunity(communityId),
    enabled: !!communityId,
  })

  useEffect(() => {
    if (communityId && activeCommunity) {
      setTelemetryContext({
        community_id: communityId,
        community_name: activeCommunity.name,
        community_platform: activeCommunity.platform,
      })
    } else {
      clearTelemetryContext(['community_id', 'community_name', 'community_platform'])
    }
  }, [communityId, activeCommunity])

  const handleCommunityChange = (id: string) => {
    setCommunityId(id)
    if (id) {
      localStorage.setItem('activeCommunityId', id)
    } else {
      localStorage.removeItem('activeCommunityId')
    }
  }

  return (
    <>
      <BrowserRouter>
        <ReevalStatusProvider communityId={communityId}>
          <Routes>
            <Route path="/setup" element={<CommunitySetup onCommunityChange={handleCommunityChange} />} />
            <Route path="/study/scenario/:id" element={<CommunityFeedView />} />
            <Route
              path="/"
              element={
                <Layout communityId={communityId} onCommunityChange={handleCommunityChange} />
              }
            >
              <Route index element={<Navigate to="/editor" replace />} />
              <Route path="editor" element={<RulesLogicsEditor communityId={communityId} />} />
              <Route path="decisions" element={<DecisionQueue communityId={communityId} />} />
              <Route path="overrides" element={<UnlinkedOverridesPage communityId={communityId} />} />
              <Route path="settings" element={<CommunitySettings communityId={communityId} />} />
              <Route path="*" element={<Navigate to="/editor" replace />} />
            </Route>
          </Routes>
        </ReevalStatusProvider>
      </BrowserRouter>
      <ToastContainer />
    </>
  )
}
