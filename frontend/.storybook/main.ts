import type { StorybookConfig } from '@storybook/react-vite'

/**
 * Storybook is the workbench for the Phase 1 primitive library. Every primitive
 * ships with a story covering all of its variants and states, and the a11y addon
 * checks each one — so accessibility is verified as components are built rather
 * than retrofitted later.
 */
const config: StorybookConfig = {
  stories: ['../src/**/*.mdx', '../src/**/*.stories.@(ts|tsx)'],
  addons: ['@storybook/addon-essentials', '@storybook/addon-a11y'],
  framework: { name: '@storybook/react-vite', options: {} },
  typescript: { reactDocgen: 'react-docgen-typescript' },
  core: { disableTelemetry: true },
}

export default config
