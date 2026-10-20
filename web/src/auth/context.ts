import { createContext } from 'react'
import type { Me } from '../lib/api'

export interface AuthState {
  me: Me | null
  loading: boolean
  signupsOpen: boolean
  /** Sign-up is limited to the shop's ADMIN_EMAIL (the server never says which email). */
  signupAdminOnly: boolean
  /** Creating the shop needs the server's one-time SETUP_TOKEN. */
  setupCodeRequired: boolean
  can: (permission: string) => boolean
  setMe: (me: Me | null) => void
  logout: () => Promise<void>
}


export const AuthContext = createContext<AuthState | null>(null)
