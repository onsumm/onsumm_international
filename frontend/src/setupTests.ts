import '@testing-library/jest-dom/vitest'

// jsdom implements neither of these, and both are used by code under test
// (blob URLs in api/authedFiles.ts, reduced-motion checks in the landing nav).
if (typeof URL.createObjectURL === 'undefined') {
  let n = 0
  Object.defineProperty(URL, 'createObjectURL', { value: () => `blob:test/${++n}`, writable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: () => {}, writable: true })
}

// jsdom has no ResizeObserver, and two components measure themselves with one
// (GridGlow sizes its cell grid to the viewport; FactGraphModal its canvas).
if (typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, 'ResizeObserver', { value: ResizeObserverStub, writable: true })
}

if (typeof window.matchMedia === 'undefined') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
