import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useState } from 'react'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import RuleEditor from './pages/RuleEditor'
import DecisionQueue from './pages/DecisionQueue'
import CommunitySettings from './pages/CommunitySettings'
import CommunitySetup from './pages/CommunitySetup'
import ExamplesPage from './pages/ExamplesPage'

export default function App() {
  const [communityId, setCommunityId] = useState<string>('')

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/setup" element={<CommunitySetup onCommunityChange={setCommunityId} />} />
        <Route
          path="/"
          element={
            <Layout communityId={communityId} onCommunityChange={setCommunityId} />
          }
        >
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard communityId={communityId} />} />
          <Route path="rules" element={<RuleEditor communityId={communityId} />} />
          <Route path="examples" element={<ExamplesPage communityId={communityId} />} />
          <Route path="decisions" element={<DecisionQueue communityId={communityId} />} />
          <Route path="settings" element={<CommunitySettings communityId={communityId} />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
