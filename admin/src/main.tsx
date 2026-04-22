import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider, MutationCache } from '@tanstack/react-query'
import App from './App.tsx'
import { showErrorToast } from './components/Toast.tsx'
import './index.css'

function extractErrorMessage(error: unknown): string {
  if (error && typeof error === 'object') {
    const axiosErr = error as { response?: { data?: { detail?: string } }; message?: string }
    if (axiosErr.response?.data?.detail) return axiosErr.response.data.detail
    if (axiosErr.message) return axiosErr.message
  }
  return 'Something went wrong. Please try again.'
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      // Skip if the mutation has its own onError handler
      if (mutation.options.onError) return
      showErrorToast(extractErrorMessage(error))
    },
  }),
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
