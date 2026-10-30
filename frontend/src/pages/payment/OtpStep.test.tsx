import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { OtpStep } from './OtpStep'

function Harness(props: Partial<React.ComponentProps<typeof OtpStep>> = {}) {
  const [otp, setOtp] = useState('')
  return (
    <OtpStep
      sentTo="ad***@onsumm.ai"
      expiresAt={Date.now() + 120_000}
      demoOtp={null}
      isBulk={false}
      otp={otp}
      setOtp={setOtp}
      error=""
      submitting={false}
      onVerify={() => {}}
      onResend={() => {}}
      onRestart={() => {}}
      {...props}
    />
  )
}

describe('confirmation code step', () => {
  it('is one labelled input the OS can autofill, not six boxes', () => {
    render(<Harness />)
    const field = screen.getByLabelText('6-digit code')
    // Six segmented inputs lose OS autofill, paste and screen-reader support.
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(field).toHaveAttribute('autocomplete', 'one-time-code')
    expect(field).toHaveAttribute('inputmode', 'numeric')
    // type=number would strip a leading zero from an otherwise valid code.
    expect(field).toHaveAttribute('type', 'text')
  })

  it('focuses the field on arrival so the user can just type', () => {
    render(<Harness />)
    expect(screen.getByLabelText('6-digit code')).toHaveFocus()
  })

  it('accepts only digits and stops at six', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const field = screen.getByLabelText('6-digit code')
    await user.type(field, '12ab34cd567')
    expect(field).toHaveValue('1234567'.slice(0, 6))
  })

  it('keeps Verify unavailable until the code is complete', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const verify = screen.getByRole('button', { name: /verify and pay/i })
    expect(verify).toBeDisabled()
    await user.type(screen.getByLabelText('6-digit code'), '123456')
    expect(verify).toBeEnabled()
  })

  it('submits on Enter once six digits are in', async () => {
    const user = userEvent.setup()
    const onVerify = vi.fn()
    render(<Harness onVerify={onVerify} />)
    await user.type(screen.getByLabelText('6-digit code'), '123456{Enter}')
    expect(onVerify).toHaveBeenCalledWith('123456')
  })

  it('shows the demo code and fills it in one click, only when the backend offers one', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<Harness demoOtp="123456" />)
    expect(screen.getByText('123456')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /use demo code/i }))
    expect(screen.getByLabelText('6-digit code')).toHaveValue('123456')
    unmount()

    render(<Harness demoOtp={null} />)
    expect(screen.queryByRole('button', { name: /use demo code/i })).not.toBeInTheDocument()
  })

  it('blocks Verify and announces the state once the code has expired', () => {
    // The old page only revealed expiry as a red error after a failed submit.
    render(<Harness expiresAt={Date.now() - 1_000} otp="123456" />)
    expect(screen.getByRole('button', { name: /verify and pay/i })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('expired')
  })

  it('offers Resend once expired, and never for a bulk batch', () => {
    const { unmount } = render(<Harness expiresAt={Date.now() - 1_000} />)
    expect(screen.getByRole('button', { name: /resend code/i })).toBeEnabled()
    unmount()

    // Bulk rows share one OTP hash, so reissuing for one would desync the rest.
    render(<Harness isBulk expiresAt={Date.now() - 1_000} />)
    expect(screen.queryByRole('button', { name: /resend code/i })).not.toBeInTheDocument()
    expect(screen.getByText(/share one code/i)).toBeInTheDocument()
  })

  it('withholds Resend while the code is still fresh', () => {
    render(<Harness />)
    expect(screen.getByRole('button', { name: /resend code/i })).toBeDisabled()
  })
})

describe('failed verification', () => {
  it('shows nothing extra when there is no error', () => {
    render(<Harness error="" />)
    expect(screen.queryByText(/expired|incorrect|attempts|payment failed/i)).not.toBeInTheDocument()
  })

  it('tells the user to request a new code when the backend says the OTP expired', () => {
    render(<Harness error="OTP expired — request a new one" />)
    expect(screen.getByText('This code has expired')).toBeInTheDocument()
    expect(screen.getByText(/request a new one below/i)).toBeInTheDocument()
  })

  it('distinguishes a wrong code from a locked-out one', () => {
    const { unmount } = render(<Harness error="Incorrect OTP" />)
    expect(screen.getByText('That code didn’t match')).toBeInTheDocument()
    unmount()

    render(<Harness error="Too many attempts — request a new OTP" />)
    expect(screen.getByText('Too many incorrect attempts')).toBeInTheDocument()
  })

  it('falls back to a generic panel — with the raw backend message — for anything unrecognised', () => {
    render(<Harness error="Network Error" />)
    expect(screen.getByText('Payment failed')).toBeInTheDocument()
    expect(screen.getByText(/Network Error/)).toBeInTheDocument()
  })
})

describe('countdown', () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it('ticks down and is hidden from assistive tech so it does not announce every second', () => {
    render(<Harness expiresAt={Date.now() + 65_000} />)
    expect(screen.getByText(/Expires in 1:0\d/)).toBeInTheDocument()
    // The tick is a state update, so React has to flush it inside act().
    act(() => vi.advanceTimersByTime(10_000))
    expect(screen.getByText(/Expires in 0:5\d/)).toBeInTheDocument()
    expect(screen.getByText(/Expires in/).closest('div')).toHaveAttribute('aria-hidden', 'true')
  })
})
