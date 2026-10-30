import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useEffect } from 'react'

// The viewer loads through react-pdf's Document; getDocument decides how each load ends.
const getDocument = vi.fn()
vi.mock('react-pdf', () => ({
  pdfjs: { GlobalWorkerOptions: { workerSrc: 'x' } },
  Page: () => null,
  Document: ({ file, onLoadSuccess, onLoadError }: { file: string; onLoadSuccess: (d: unknown) => void; onLoadError: (e: unknown) => void }) => {
    useEffect(() => { getDocument(file).promise.then(onLoadSuccess, onLoadError) }, [file])
    return null
  },
}))

import PdfFullscreen from './PdfFullscreen'

// jsdom lays nothing out; the viewer sizes the page from its body's width.
Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 900 })

// jsdom has no Web Animations API; the viewer's fly-in only needs a finished promise.
if (!Element.prototype.animate) {
  Element.prototype.animate = function () {
    return { cancel() {}, finished: Promise.resolve() } as unknown as Animation
  }
}

describe('PdfFullscreen', () => {
  it('opens straight away, with a blank sheet, while the file is still on its way', async () => {
    const onClose = vi.fn()
    render(<PdfFullscreen url={null} name="bill.pdf" onClose={onClose} />)
    const dialog = screen.getByRole('dialog', { name: 'bill.pdf, full screen' })
    expect(dialog.querySelector('.bpf__sheet')).not.toBeNull()
    expect(screen.queryByText(/Opening/)).not.toBeInTheDocument()
    expect(getDocument).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('opens the bill viewer once the PDF loads', async () => {
    getDocument.mockReturnValueOnce({ promise: Promise.resolve({ numPages: 3 }), destroy: () => {} })
    render(<PdfFullscreen url="blob:test/1" name="rate-card.pdf" onClose={() => {}} />)
    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'rate-card.pdf, full screen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument()
  })

  it('says so when the file is not a readable PDF', async () => {
    getDocument.mockReturnValueOnce({ promise: Promise.reject(new Error('bad')), destroy: () => {} })
    render(<PdfFullscreen url="blob:test/2" onClose={() => {}} />)
    expect(await screen.findByText('Couldn’t open this PDF.')).toBeInTheDocument()
  })
})
