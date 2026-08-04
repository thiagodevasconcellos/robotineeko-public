export function isMobileViewLocation(locationLike) {
    if (!locationLike) {
        return false
    }

    const rawPathname = typeof locationLike.pathname === 'string' ? locationLike.pathname : ''
    if (!rawPathname) {
        return false
    }

    const normalizedPathname = rawPathname.length > 1
        ? rawPathname.replace(/\/+$/, '')
        : rawPathname

    return normalizedPathname === '/mobile'
}
