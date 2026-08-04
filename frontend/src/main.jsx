import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { AppErrorBoundary } from './components/AppErrorBoundary.jsx'

const FRONTEND_RECOVERY_STORAGE_KEY = 'robotineeko:frontend-recovery-reload-at'
const FRONTEND_RECOVERY_WINDOW_MS = 30_000

function readDynamicImportFailureMessage(candidate) {
    if (!candidate) {
        return ''
    }
    if (typeof candidate === 'string') {
        return candidate
    }
    if (typeof candidate?.message === 'string') {
        return candidate.message
    }
    if (typeof candidate?.reason?.message === 'string') {
        return candidate.reason.message
    }
    return String(candidate)
}

function isDynamicImportFetchFailure(candidate) {
    const message = readDynamicImportFailureMessage(candidate).toLowerCase()
    return (
        message.includes('failed to fetch dynamically imported module')
        || message.includes('error loading dynamically imported module')
        || message.includes('importing a module script failed')
    )
}

function tryRecoverStaleFrontend(reason) {
    if (!isDynamicImportFetchFailure(reason)) {
        return false
    }

    try {
        const now = Date.now()
        const lastAttemptRaw = window.sessionStorage.getItem(FRONTEND_RECOVERY_STORAGE_KEY)
        const lastAttempt = Number.parseInt(lastAttemptRaw || '0', 10)
        if (Number.isFinite(lastAttempt) && lastAttempt > 0 && now - lastAttempt < FRONTEND_RECOVERY_WINDOW_MS) {
            return false
        }
        window.sessionStorage.setItem(FRONTEND_RECOVERY_STORAGE_KEY, String(now))
    } catch {
        // Continue to reload even if session storage is unavailable.
    }

    window.location.reload()
    return true
}

window.addEventListener('vite:preloadError', (event) => {
    if (tryRecoverStaleFrontend(event?.payload)) {
        event.preventDefault?.()
    }
})

window.addEventListener('unhandledrejection', (event) => {
    if (tryRecoverStaleFrontend(event?.reason)) {
        event.preventDefault?.()
    }
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>,
)
