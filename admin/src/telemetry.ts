// Frontend telemetry: capture every click + route change, batch-flush to backend.
//
// Usage:
//   import { initTelemetry, logEvent } from './telemetry'
//   initTelemetry()  // once, in main.tsx
//   logEvent('approve_post', { post_id })  // optional explicit events
//
// Annotations:
//   <button data-log="approve-post">           // names the action
//   <div data-log-context='{"post_id":"abc"}'> // ancestor context, JSON
//
// Auto-capture works without annotations; annotations just sharpen intent.

type AnyRecord = Record<string, unknown>

interface UIEvent {
  ts: string
  session_id: string
  kind: 'click' | 'input' | 'nav' | 'custom'
  route: string
  target_tag?: string
  target_role?: string
  target_text?: string
  target_id?: string
  target_classes?: string
  data_log?: string
  log_context?: AnyRecord
  name?: string
  payload?: AnyRecord
  context?: AnyRecord
}

let sessionContext: AnyRecord = {}

export function setTelemetryContext(patch: AnyRecord) {
  sessionContext = { ...sessionContext, ...patch }
}

export function clearTelemetryContext(keys?: string[]) {
  if (!keys) {
    sessionContext = {}
    return
  }
  const next = { ...sessionContext }
  for (const k of keys) delete next[k]
  sessionContext = next
}

const ENDPOINT = '/api/telemetry/events'
const FLUSH_INTERVAL_MS = 3000
const MAX_BATCH = 50
const TEXT_LIMIT = 120

function makeId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    const b = new Uint8Array(16)
    crypto.getRandomValues(b)
    b[6] = (b[6] & 0x0f) | 0x40
    b[8] = (b[8] & 0x3f) | 0x80
    const hex = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
  return `sid-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

const SESSION_ID = (() => {
  const k = 'automod.telemetry.sid'
  let v = sessionStorage.getItem(k)
  if (!v) {
    v = makeId()
    sessionStorage.setItem(k, v)
  }
  return v
})()

const queue: UIEvent[] = []
let flushTimer: number | null = null

function nowIso() {
  return new Date().toISOString()
}

function currentRoute() {
  return window.location.pathname + window.location.search + window.location.hash
}

function truncate(s: string | null | undefined, n = TEXT_LIMIT) {
  if (!s) return undefined
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n) + '…' : t
}

function climbForLogAttrs(el: Element | null): {
  data_log?: string
  log_context?: AnyRecord
} {
  let data_log: string | undefined
  let log_context: AnyRecord | undefined
  let cur: Element | null = el
  while (cur && cur !== document.body) {
    if (!data_log && cur instanceof HTMLElement && cur.dataset.log) {
      data_log = cur.dataset.log
    }
    if (cur instanceof HTMLElement && cur.dataset.logContext) {
      try {
        const parsed = JSON.parse(cur.dataset.logContext) as AnyRecord
        log_context = { ...parsed, ...(log_context ?? {}) } // child wins
      } catch {
        // ignore malformed context
      }
    }
    cur = cur.parentElement
  }
  return { data_log, log_context }
}

function findInteractiveAncestor(el: Element | null): Element | null {
  let cur: Element | null = el
  while (cur && cur !== document.body) {
    const tag = cur.tagName
    if (
      tag === 'BUTTON' ||
      tag === 'A' ||
      tag === 'INPUT' ||
      tag === 'SELECT' ||
      tag === 'TEXTAREA' ||
      tag === 'LABEL' ||
      cur.getAttribute('role') === 'button' ||
      (cur as HTMLElement).onclick != null ||
      (cur instanceof HTMLElement && cur.dataset.log)
    ) {
      return cur
    }
    cur = cur.parentElement
  }
  return null
}

function enqueue(ev: UIEvent) {
  if (Object.keys(sessionContext).length > 0) {
    ev.context = { ...sessionContext, ...(ev.context ?? {}) }
  }
  queue.push(ev)
  if (queue.length >= MAX_BATCH) {
    flush()
  } else if (flushTimer == null) {
    flushTimer = window.setTimeout(flush, FLUSH_INTERVAL_MS)
  }
}

function flush() {
  if (flushTimer != null) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
  if (queue.length === 0) return
  const batch = queue.splice(0, queue.length)
  const body = JSON.stringify({ events: batch })
  // Prefer sendBeacon on unload paths; fall back to fetch keepalive.
  if (navigator.sendBeacon) {
    const blob = new Blob([body], { type: 'application/json' })
    if (navigator.sendBeacon(ENDPOINT, blob)) return
  }
  fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // Silent: don't let telemetry failures pollute the user UI.
  })
}

function onClick(e: MouseEvent) {
  const target = findInteractiveAncestor(e.target as Element | null) ?? (e.target as Element | null)
  if (!target) return
  const html = target as HTMLElement
  const { data_log, log_context } = climbForLogAttrs(html)
  enqueue({
    ts: nowIso(),
    session_id: SESSION_ID,
    kind: 'click',
    route: currentRoute(),
    target_tag: html.tagName,
    target_role: html.getAttribute('role') ?? undefined,
    target_text: truncate(html.innerText ?? html.getAttribute('aria-label') ?? html.getAttribute('title')),
    target_id: html.id || undefined,
    target_classes: html.className && typeof html.className === 'string' ? html.className : undefined,
    data_log,
    log_context,
  })
}

function onChange(e: Event) {
  const t = e.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | null
  if (!t) return
  const { data_log, log_context } = climbForLogAttrs(t)
  enqueue({
    ts: nowIso(),
    session_id: SESSION_ID,
    kind: 'input',
    route: currentRoute(),
    target_tag: t.tagName,
    target_id: t.id || undefined,
    target_classes: typeof t.className === 'string' ? t.className : undefined,
    data_log,
    log_context,
    payload: {
      name: t.getAttribute('name') ?? undefined,
      type: (t as HTMLInputElement).type ?? undefined,
      value: (t as HTMLInputElement).value ?? '',
    },
  })
}

let lastRoute = ''
function onRouteMaybeChanged() {
  const r = currentRoute()
  if (r === lastRoute) return
  lastRoute = r
  enqueue({
    ts: nowIso(),
    session_id: SESSION_ID,
    kind: 'nav',
    route: r,
  })
}

export function logEvent(name: string, payload?: AnyRecord) {
  enqueue({
    ts: nowIso(),
    session_id: SESSION_ID,
    kind: 'custom',
    route: currentRoute(),
    name,
    payload,
  })
}

export function initTelemetry() {
  document.addEventListener('click', onClick, { capture: true })
  document.addEventListener('change', onChange, { capture: true })

  // Catch SPA navigation (react-router uses pushState/replaceState).
  const wrap = (k: 'pushState' | 'replaceState') => {
    const orig = history[k]
    history[k] = function (...args: Parameters<typeof orig>) {
      const r = orig.apply(this, args)
      queueMicrotask(onRouteMaybeChanged)
      return r
    }
  }
  wrap('pushState')
  wrap('replaceState')
  window.addEventListener('popstate', onRouteMaybeChanged)
  onRouteMaybeChanged() // initial

  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
  window.addEventListener('pagehide', flush)
  window.addEventListener('beforeunload', flush)
}
