// Shared option lists for company-profile dropdowns. Imported by both the
// SuperAdmin onboarding wizard and the tenant Admin profile editor so the two
// surfaces always show the same choices.

export const COMPANY_TYPE_OPTIONS = [
  { value: '',               label: '— Select —' },
  { value: 'pvt_ltd',        label: 'Private Limited' },
  { value: 'llp',            label: 'LLP' },
  { value: 'public',         label: 'Public Limited' },
  { value: 'proprietorship', label: 'Proprietorship' },
  { value: 'partnership',    label: 'Partnership' },
  { value: 'other',          label: 'Other' },
]

export const COMPANY_SIZE_OPTIONS = [
  { value: '',         label: '— Select —' },
  { value: '1-10',     label: '1 – 10' },
  { value: '11-50',    label: '11 – 50' },
  { value: '51-200',   label: '51 – 200' },
  { value: '201-1000', label: '201 – 1,000' },
  { value: '1000+',    label: '1,000+' },
]

export const PLAN_OPTIONS = [
  { value: '',           label: '— None / decide later —' },
  { value: 'starter',    label: 'Starter' },
  { value: 'growth',     label: 'Growth' },
  { value: 'enterprise', label: 'Enterprise' },
]

export const PAYMENT_METHOD_OPTIONS = [
  { value: '',              label: '— Select —' },
  { value: 'bank_transfer', label: 'Bank Transfer' },
  { value: 'card',          label: 'Card' },
  { value: 'upi',           label: 'UPI' },
  { value: 'other',         label: 'Other' },
]

export const FONT_OPTIONS = [
  { label: 'Default (system)', value: '' },
  { label: 'Inter',            value: 'Inter' },
  { label: 'Poppins',          value: 'Poppins' },
  { label: 'Roboto',           value: 'Roboto' },
  { label: 'Montserrat',       value: 'Montserrat' },
  { label: 'Open Sans',        value: 'Open Sans' },
  { label: 'Lato',             value: 'Lato' },
  { label: 'Nunito',           value: 'Nunito' },
]

// ── Soft format validators ──
// Return null if value is empty or valid, or a short hint string if malformed.
// These are HINTS — they never block form submission.

const CIN_RE = /^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/
const INDIA_PIN_RE = /^\d{6}$/
const CURRENCY_RE = /^[A-Z]{3}$/

export function validateCIN(value) {
  if (!value) return null
  return CIN_RE.test(value.trim().toUpperCase())
    ? null
    : 'Indian CIN is 21 chars (e.g. U72200KA2020PTC012345). Looks off — double-check.'
}

export function validatePostalCode(value, country) {
  if (!value) return null
  if ((country || '').toLowerCase() === 'india') {
    return INDIA_PIN_RE.test(value.trim())
      ? null
      : 'Indian PIN codes are 6 digits.'
  }
  return null
}

export function validateCurrency(value) {
  if (!value) return null
  return CURRENCY_RE.test(value.trim())
    ? null
    : 'Currency must be a 3-letter ISO code (e.g. INR, USD, EUR).'
}
