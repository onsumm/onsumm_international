type LoadingMarkProps = {
  size?: number
  label?: string
  className?: string
}

/** The product mark is the shared progress affordance across the app. */
export function LoadingMark({ size = 16, label = 'Loading', className = '' }: LoadingMarkProps) {
  return (
    <span className={`u-loading-mark ${className}`.trim()} role="status" aria-label={label} style={{ width: size, height: size }}>
      <img src="/brand/onsumm-mark.svg" alt="" />
    </span>
  )
}
