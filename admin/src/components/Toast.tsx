import { useState, useEffect, useCallback } from 'react'
import { X, AlertCircle } from 'lucide-react'

interface ToastMessage {
  id: number
  message: string
}

let nextId = 0
let addToastGlobal: ((message: string) => void) | null = null

export function showErrorToast(message: string) {
  addToastGlobal?.(message)
}

export default function ToastContainer() {
  const [toasts, setToasts] = useState<ToastMessage[]>([])

  const addToast = useCallback((message: string) => {
    const id = nextId++
    setToasts(prev => [...prev, { id, message }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 6000)
  }, [])

  useEffect(() => {
    addToastGlobal = addToast
    return () => { addToastGlobal = null }
  }, [addToast])

  const dismiss = (id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }

  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 max-w-sm">
      {toasts.map(t => (
        <div
          key={t.id}
          className="bg-red-50 border border-red-200 text-red-800 rounded-lg shadow-lg px-4 py-3 flex items-start gap-2 animate-slide-in"
        >
          <AlertCircle size={16} className="flex-shrink-0 mt-0.5 text-red-500" />
          <p className="text-sm flex-1">{t.message}</p>
          <button
            className="flex-shrink-0 text-red-400 hover:text-red-600"
            onClick={() => dismiss(t.id)}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}
