import { ReactNode, useState } from 'react'

interface TooltipProps {
  content: ReactNode
  children: ReactNode
  className?: string
  side?: 'top' | 'bottom'
  // When true, render an info-cue dot to advertise the tooltip on focusable elements
  // that would otherwise look the same as their non-tooltipped neighbors.
  showCue?: boolean
}

// Lightweight hover/focus tooltip. We avoid a portal since the content is short
// and our containers are not clipping-overflow constrained.
export default function Tooltip({ content, children, className, side = 'top', showCue }: TooltipProps) {
  const [open, setOpen] = useState(false)
  if (!content) {
    return <>{children}</>
  }
  const positionCls = side === 'top'
    ? 'bottom-full mb-1.5'
    : 'top-full mt-1.5'

  return (
    <span
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
      {open && (
        <span
          role="tooltip"
          className={`absolute z-30 left-1/2 -translate-x-1/2 ${positionCls} whitespace-normal max-w-xs min-w-[12rem] text-xs leading-snug bg-gray-900 text-gray-100 rounded shadow-lg px-2.5 py-1.5 pointer-events-none`}
        >
          {content}
        </span>
      )}
    </span>
  )
}
