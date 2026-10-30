import { useEffect, useState, useCallback, useRef } from 'react'
import { fetchBills, invalidateBillsCache, type Bill } from '../api/invoices'
import { useAuth } from '../auth/AuthContext'

export function useBills() {
  const { user, activeBrand } = useAuth()
  const [bills, setBills] = useState<Bill[]>([])
  const [loading, setLoading] = useState(true)
  const brandId = activeBrand?.id
  const companyId = user?.company_id
  // Track current brand so stale responses from a previous brand are dropped
  const currentKey = useRef(`${companyId}:${brandId}`)

  const load = useCallback((force = false) => {
    if (!companyId) return
    const key = `${companyId}:${brandId}`
    currentKey.current = key
    setLoading(true)
    if (force) invalidateBillsCache(companyId)
    fetchBills(force, companyId, undefined, undefined, undefined, brandId)
      .then(data => {
        if (currentKey.current !== key) return // stale — brand switched mid-fetch
        setBills(data)
      })
      .finally(() => { if (currentKey.current === key) setLoading(false) })
  }, [companyId, brandId])

  useEffect(() => { load() }, [load])

  const refresh = () => load(true)

  return { bills, loading, refresh }
}
