import { useState } from 'react'
import './AuthManager.css'

export function AuthManager({
    isOpen,
    mode = 'login',
    variant = 'overlay',
    isSubmitting = false,
    error = '',
    currentUser = null,
    allowGuestLogin = true,
    onClose,
    onLogin,
    onGuestLogin,
    onLogout,
}) {
    const [email, setEmail] = useState('')
    const [passwordDraft, setPasswordDraft] = useState({
        resetKey: '',
        value: '',
    })
    const passwordResetKey = `${Number(isOpen)}:${String(mode || 'login')}`
    const password = passwordDraft.resetKey === passwordResetKey ? passwordDraft.value : ''

    if (!isOpen) {
        return null
    }

    const isAuthenticated = Boolean(currentUser)
    const isStandalone = variant === 'standalone'

    async function handleSubmit() {
        const safeEmail = String(email || '').trim()
        const safePassword = String(password || '')

        if (!safeEmail || !safePassword) {
            return
        }

        await onLogin?.({
            email: safeEmail,
            password: safePassword,
        })
    }

    return (
        <div className={isStandalone ? 'authManagerStandalone' : 'overlayContainer authManagerOverlay'}>
            {!isStandalone && (
                <div className='fog' onClick={onClose} />
            )}

            <div className={isStandalone ? 'authManagerWindow authManagerWindowStandalone' : 'overlay authManagerWindow'}>
                {!isStandalone && (
                    <button type='button' className='closeOverlay' onClick={onClose}>
                        x
                    </button>
                )}

                <div className='authManagerPanel'>
                    <div className='authManagerTitle'>Account</div>

                    {isAuthenticated ? (
                        <div className='authManagerLoggedIn'>
                            <div className='authManagerIdentity'>
                                Signed in as <strong>{currentUser.display_name || currentUser.email}</strong>
                                {currentUser.is_guest ? (
                                    <span className='authManagerGuestBadge'>Guest demo mode</span>
                                ) : null}
                            </div>

                            <div className='authManagerActions'>
                                <button type='button' onClick={() => void onLogout?.()}>
                                    Sign out
                                </button>
                            </div>
                        </div>
                    ) : (
                        <>
                            <div className='authManagerField'>
                                <label htmlFor='authEmail'>Email</label>
                                <input
                                    id='authEmail'
                                    type='email'
                                    value={email}
                                    onChange={(event) => setEmail(event.target.value)}
                                    autoComplete='email'
                                />
                            </div>

                            <div className='authManagerField'>
                                <label htmlFor='authPassword'>Password</label>
                                <input
                                    id='authPassword'
                                    type='password'
                                    value={password}
                                    onChange={(event) => setPasswordDraft({
                                        resetKey: passwordResetKey,
                                        value: event.target.value,
                                    })}
                                    autoComplete='current-password'
                                />
                            </div>

                            {error && (
                                <div className='authManagerError'>{error}</div>
                            )}

                            <div className='authManagerActions'>
                                {allowGuestLogin ? (
                                    <button
                                        type='button'
                                        className='authManagerGuest'
                                        disabled={isSubmitting}
                                        onClick={() => void onGuestLogin?.()}
                                    >
                                        Guest demo
                                    </button>
                                ) : null}
                                <button
                                    type='button'
                                    className='authManagerPrimary'
                                    disabled={isSubmitting || !String(email || '').trim() || !String(password || '').trim()}
                                    onClick={() => void handleSubmit()}
                                >
                                    {isSubmitting ? 'Signing in...' : 'Sign in'}
                                </button>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    )
}
