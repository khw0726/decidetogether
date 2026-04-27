import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState } from 'react'
import Layout from './components/Layout'
import RulesLogicsEditor from './pages/RulesLogicsEditor'
import DecisionQueue from './pages/DecisionQueue'
import CommunitySettings from './pages/CommunitySettings'
import CommunitySetup from './pages/CommunitySetup'
import UnlinkedOverridesPage from './pages/UnlinkedOverridesPage'
import ToastContainer from './components/Toast'

export default function App() {
  const [communityId, setCommunityId] = useState<string>(
    () => localStorage.getItem('activeCommunityId') ?? ''
  )

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
        <Routes>
          <Route path="/setup" element={<CommunitySetup onCommunityChange={handleCommunityChange} />} />
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
      </BrowserRouter>
      <ToastContainer />
    </>
  )
}
