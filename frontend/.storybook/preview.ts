import type { Preview } from '@storybook/react'
import '../src/index.css'

const preview: Preview = {
  parameters: {
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
    // Phase 1 target: every story clean. Reported, not blocking, until the
    // primitive library replaces the legacy components.
    a11y: { test: 'todo' },
    backgrounds: {
      default: 'canvas',
      values: [
        { name: 'canvas', value: '#f5f7fb' },
        { name: 'surface', value: '#ffffff' },
      ],
    },
  },
}

export default preview
