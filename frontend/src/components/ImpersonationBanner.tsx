/**
 * Shown in the TENANT shell whenever ops is viewing a customer's account.
 *
 * Lives in components/ rather than admin/ so tenant code never imports from the
 * admin tree. Deliberately loud and impossible to mistake for normal chrome: the
 * failure this prevents is an ops user forgetting which account they are looking
 * at.
 */
import { useEffect, useState } from 'react'
import { Eye } from 'lucide-react'
import { Button } from '../ui'
import { exitImpersonation, readImpersonation } from '../admin/impersonation'
import '../admin/admin.css'

function remaining(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (ms <= 0) return '0:00'
  const total = Math.floor(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export default function ImpersonationBanner() {
  const meta = readImpersonation()
  const [left, setLeft] = useState(() => (meta ? remaining(meta.expires_at) : ''))

  useEffect(() => {
    if (!meta) return
    const id = setInterval(() => {
      const next = remaining(meta.expires_at)
      setLeft(next)
      // The token is dead server-side at this point; leaving the browser in a
      // half-authenticated tenant session is worse than bouncing back to the panel.
      if (next === '0:00') exitImpersonation()
    }, 1000)
    return () => clearInterval(id)
  }, [meta])

  if (!meta) return null

  return (
    <div className="adm-imp" role="status">
      <span>
        <Eye size={14} aria-hidden="true" />{' '}
        Viewing <strong>{meta.tenant_name}</strong> as Onsumm Support
        {meta.read_only && ' · read-only'} · ends in {left}
      </span>
      <Button size="sm" variant="secondary" onClick={exitImpersonation}>
        Exit to ops
      </Button>
    </div>
  )
}
