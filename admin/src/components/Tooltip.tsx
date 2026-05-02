import { ReactNode, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

interface TooltipProps {
  content: ReactNode
  children: ReactNode
  className?: string
  side?: 'top' | 'bottom'
  // When true, render an info-cue dot to advertise the tooltip on focusable elements
  // that would otherwise look the same as their non-tooltipped neighbors.
  showCue?: boolean
}

// Lightweight hover/focus tooltip. Renders the popup via a portal so it isn't
// clipped by ancestors with `overflow: auto/hidden`.
export default function Tooltip({ content, children, className, side = 'top', showCue }: TooltipProps) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const r = triggerRef.current.getBoundingClientRect()
    const cx = r.left + r.width / 2
    setPos(side === 'top' ? { top: r.top - 6, left: cx } : { top: r.bottom + 6, left: cx })
  }, [open, side])

  if (!content) {
    return <>{children}</>
  }

  return (
    <span
      ref={triggerRef}
      className={`relative inline-flex items-center ${className || ''}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {showCue && (
        <span
          aria-hidden
          className="ml-0.5 inline-block w-1.5 h-1.5 rounded-full bg-indigo-300 group-hover:bg-indigo-500"
        />
      )}
      {open && pos && createPortal(
        <span
          role="tooltip"
          style={{
            position: 'fixed',
            top: pos.top,
            left: pos.left,
            transform: side === 'top' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
          }}
          className="z-50 whitespace-normal max-w-xs min-w-[12rem] text-xs leading-snug bg-gray-900 text-gray-100 rounded shadow-lg px-2.5 py-1.5 pointer-events-none"
        >
          {content}
        </span>,
        document.body,
      )}
    </span>
  )
}
