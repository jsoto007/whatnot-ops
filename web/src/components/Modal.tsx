import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Icon } from './Icon'

/**
 * A centred dialog for one short form (add an item). Escape, the scrim and the
 * close button all close it; focus moves in on open and back where it was on close.
 */
export function Modal({ title, sub, onClose, children, footer }: { title: string; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    const first = panel.current?.querySelector<HTMLElement>('input, select, textarea')
    ;(first ?? panel.current)?.focus()
    document.body.classList.add('modal-open')
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.classList.remove('modal-open')
      before?.focus?.()
    }
  }, [onClose])
  return (
    <div className="modal-layer">
      <div className="modal-scrim" onClick={onClose} aria-hidden="true" />
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={panel}>
        <header className="modal-head">
          <div>
            <h3 id={titleId}>{title}</h3>
            {sub && <div className="sheet-sub">{sub}</div>}
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <Icon name="close" size={18} />
          </button>
        </header>
        {children}
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}
