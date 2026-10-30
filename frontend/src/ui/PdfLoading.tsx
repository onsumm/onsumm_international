import { useEffect, useState } from 'react'

type PdfLoadingProps = {
  /** 'fetching' = the signed file is still downloading; 'rendering' = pdf.js is drawing it. */
  stage?: 'fetching' | 'rendering'
  className?: string
}

const CAPTION = {
  fetching: 'Fetching the document securely',
  rendering: 'Rendering the first page',
} as const

/**
 * Placeholder shown while a PDF preview loads: a paper-shaped skeleton that
 * sketches an invoice layout, a scan beam passing over it, and a caption that
 * says which step is actually running. After a few seconds it adds a calm note
 * so a large file doesn't read as a hang.
 */
export function PdfLoading({ stage = 'rendering', className = '' }: PdfLoadingProps) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    setSlow(false)
    const t = window.setTimeout(() => setSlow(true), 6000)
    return () => window.clearTimeout(t)
  }, [stage])

  return (
    <div className={`u-pdfload ${className}`.trim()} role="status" aria-live="polite">
      <div className="u-pdfload__sheet" aria-hidden="true">
        <div className="u-pdfload__sec u-pdfload__head">
          <span className="u-pdfload__logo" />
          <div className="u-pdfload__stack u-pdfload__stack--end">
            <Bar w={60} h={9} d={0} />
            <Bar w={40} d={1} />
          </div>
        </div>

        <div className="u-pdfload__sec u-pdfload__cols">
          <div className="u-pdfload__stack">
            <Bar w={70} d={2} /><Bar w={90} d={3} /><Bar w={55} d={4} />
          </div>
          <div className="u-pdfload__stack u-pdfload__stack--end">
            <Bar w={70} d={2} /><Bar w={85} d={3} /><Bar w={50} d={4} />
          </div>
        </div>

        <div className="u-pdfload__sec u-pdfload__table">
          <div className="u-pdfload__row u-pdfload__row--th">
            <Bar w={30} d={5} /><Bar w={14} d={5} /><Bar w={16} d={5} />
          </div>
          {[64, 48, 58, 42, 54].map((w, i) => (
            <div className="u-pdfload__row" key={i}>
              <Bar w={w} d={6 + i} /><Bar w={8} d={6 + i} /><Bar w={14} d={6 + i} />
            </div>
          ))}
        </div>

        <div className="u-pdfload__sec u-pdfload__totals">
          <div className="u-pdfload__row"><Bar w={22} d={11} /><Bar w={18} d={11} /></div>
          <div className="u-pdfload__row"><Bar w={18} d={12} /><Bar w={14} d={12} /></div>
          <div className="u-pdfload__row u-pdfload__row--grand"><Bar w={26} h={9} d={13} strong /><Bar w={22} h={9} d={13} strong /></div>
        </div>

        <div className="u-pdfload__sec u-pdfload__foot">
          <Bar w={46} d={14} /><Bar w={28} d={14} />
        </div>

        <span className="u-pdfload__beam" />
      </div>

      <div className="u-pdfload__caption">
        <span key={stage} className="u-pdfload__text">
          {CAPTION[stage]}
          <span className="u-pdfload__dots"><i>.</i><i>.</i><i>.</i></span>
        </span>
        {slow && <span className="u-pdfload__hint">Larger files take a few more seconds.</span>}
      </div>
    </div>
  )
}

/** One skeleton line; `d` is its place in the reading order, driving the stagger. */
function Bar({ w, h, d, strong }: { w: number; h?: number; d: number; strong?: boolean }) {
  return (
    <span
      className={`u-pdfload__bar${strong ? ' u-pdfload__bar--strong' : ''}`}
      style={{ width: `${w}%`, height: h, ['--d' as string]: d }}
    />
  )
}
