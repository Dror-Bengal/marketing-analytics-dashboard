import type { Post } from '@/pages/Dashboard'
import type { DashboardStats, KpiMatrixResponse } from '@/types'
type Filters = {page_id?: string; time_filter?: string; start_date?: string; end_date?: string}
async function read<T>(path: string, params: Record<string, string | number | undefined> = {}): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value))
  }
  const response = await fetch('/api/' + path + '?' + query)
  if (!response.ok) throw new Error('Request failed: ' + response.status)
  return response.json()
}
export const getPages = () => read<{pages: {id: string; name: string}[]}>('pages')
export const getDashboardStats = (filters: Filters) => read<DashboardStats>('dashboard/stats', filters)
export const getKpiMatrix = (filters: Filters) => read<KpiMatrixResponse>('dashboard/kpi-matrix', filters)
export function getPosts(params: Filters & {page: number; limit: number; sort_by: string; sort_order: string}) {
  const {page, ...filters} = params
  return read<{posts: Post[]; pages: number; total: number}>('posts', {...filters, skip: (page - 1) * params.limit})
}
export async function getPost(id: string): Promise<Post | null> {
  const response = await fetch('/api/posts/' + encodeURIComponent(id))
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Unable to load post')
  return response.json()
}
