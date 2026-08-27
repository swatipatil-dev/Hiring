import '@testing-library/jest-dom'

/**
 * jsdom has no ResizeObserver, and Recharts' ResponsiveContainer constructs one on
 * mount. Without this every chart throws an uncaught ReferenceError that fails the
 * whole file rather than the one assertion that touched a chart.
 *
 * A no-op is the right stub: the tests that render a figure also pin its size, so
 * nothing here needs to report a real measurement.
 */
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
