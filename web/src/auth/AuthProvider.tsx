import { useCallback, useMemo, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, apiGet, type Me } from '../lib/api'
import { AuthContext, type AuthState } from './context'

async function fetchMe(): Promise<{ me: Me | null; signupsOpen: boolean }> {
  try {
    return { me: await apiGet<Me>('/api/auth/me'), signupsOpen: false }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const body = err.body as { signupsOpen?: boolean } | undefined
      return { me: null, signupsOpen: body?.signupsOpen ?? false }
    }
    throw err
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000, retry: false })
  const me = q.data?.me ?? null
  const setMe = useCallback((next: Me | null) => qc.setQueryData(['me'], { me: next, signupsOpen: false }), [qc])
  const logout = useCallback(async () => {
    await api('POST', '/api/auth/logout')
    // Update "me" first so guards see the signed-out state synchronously, then
    // drop everything else so no org data survives into the next sign-in.
    setMe(null)
    qc.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
  }, [qc, setMe])
  const value = useMemo<AuthState>(
    () => ({
      me,
      loading: q.isPending,
      signupsOpen: q.data?.signupsOpen ?? false,
      can: (p) => !!me && me.permissions.includes(p),
      setMe,
      logout,
    }),
    [me, q.isPending, q.data?.signupsOpen, setMe, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

