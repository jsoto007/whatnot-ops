import { useCallback, useMemo, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api, apiGet, type Me } from '../lib/api'
import { AuthContext, type AuthState } from './context'

type MeState = { me: Me | null; signupsOpen: boolean; signupAdminOnly: boolean; setupCodeRequired: boolean }

async function fetchMe(): Promise<MeState> {
  try {
    return { me: await apiGet<Me>('/api/auth/me'), signupsOpen: false, signupAdminOnly: false, setupCodeRequired: false }
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const body = err.body as { signupsOpen?: boolean; signupAdminOnly?: boolean; setupCodeRequired?: boolean } | undefined
      return { me: null, signupsOpen: body?.signupsOpen ?? false, signupAdminOnly: body?.signupAdminOnly ?? false, setupCodeRequired: body?.setupCodeRequired ?? false }
    }
    throw err
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['me'], queryFn: fetchMe, staleTime: 60_000, retry: false })
  const me = q.data?.me ?? null
  const setMe = useCallback((next: Me | null) => qc.setQueryData<MeState>(['me'], { me: next, signupsOpen: false, signupAdminOnly: false, setupCodeRequired: false }), [qc])
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
      signupAdminOnly: q.data?.signupAdminOnly ?? false,
      setupCodeRequired: q.data?.setupCodeRequired ?? false,
      can: (p) => !!me && me.permissions.includes(p),
      setMe,
      logout,
    }),
    [me, q.isPending, q.data?.signupsOpen, q.data?.signupAdminOnly, q.data?.setupCodeRequired, setMe, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

