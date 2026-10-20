export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export type Health = { status: 'ok' | 'degraded'; db: boolean }
export const getHealth = () => apiGet<Health>('/api/health')
