import { createContext } from 'react'
import type { Me } from '../lib/api'

export interface AuthState {
  me: Me | null
  loading: boolean
  signupsOpen: boolean
  /** Sign-up is limited to the shop's ADMIN_EMAIL (the server never says which email). */
  signupAdminOnly: boolean
  can: (permission: string) => boolean
  setMe: (me: Me | null) => void
  logout: () => Promise<void>
}


export const AuthContext = createContext<AuthState | null>(null)
