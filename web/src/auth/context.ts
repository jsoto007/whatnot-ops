import { createContext } from 'react'
import type { Me } from '../lib/api'

export interface AuthState {
  me: Me | null
  loading: boolean
  signupsOpen: boolean
  can: (permission: string) => boolean
  setMe: (me: Me | null) => void
  logout: () => Promise<void>
}


export const AuthContext = createContext<AuthState | null>(null)
