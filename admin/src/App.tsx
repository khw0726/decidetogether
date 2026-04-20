import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState } from 'react'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import RuleEditor from './pages/RuleEditor'
import DecisionQueue from './pages/DecisionQueue'
import CommunitySettings from './pages/CommunitySettings'
import CommunitySetup from './pages/CommunitySetup'
import ExamplesPage from './pages/ExamplesPage'
import UnlinkedOverridesPage from './pages/UnlinkedOverridesPage'

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
    <BrowserRouter>
      <Routes>
        <Route path="/setup" element={<CommunitySetup onCommunityChange={handleCommunityChange} />} />
        <Route
          path="/"
          element={
            <Layout communityId={communityId} onCommunityChange={handleCommunityChange} />
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard communityId={communityId} />} />
          <Route path="rules" element={<RuleEditor communityId={communityId} />} />
          <Route path="examples" element={<ExamplesPage communityId={communityId} />} />
          <Route path="decisions" element={<DecisionQueue communityId={communityId} />} />
          <Route path="overrides" element={<UnlinkedOverridesPage communityId={communityId} />} />
          <Route path="settings" element={<CommunitySettings communityId={communityId} />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
