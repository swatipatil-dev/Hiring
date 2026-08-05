import { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Crown, Plus, Building2, Users, Briefcase, ToggleLeft, ToggleRight, Copy, Check, RefreshCw, Eye, EyeOff, Edit3, X, Zap, Layers, Trash2, ArrowLeft, ChevronDown, Upload, LogIn, UserPlus } from 'lucide-react'
import api from '../../api/client'
import {
  COMPANY_TYPE_OPTIONS, COMPANY_SIZE_OPTIONS, PLAN_OPTIONS,
  PAYMENT_METHOD_OPTIONS, FONT_OPTIONS,
  validateCIN, validatePostalCode, validateCurrency,
} from '../../constants/companyProfile'
import { slugify } from '../../utils/slugify'

const DRAFT_KEY = 'hireflow_onboarding_draft'

const EMPTY_FORM = {
  // Basics + admin (required)
  company_name: '', slug: '',
  admin_email: '', admin_password: '', admin_first_name: '', admin_last_name: '',
  // Branding
  logo_url: '', logo_dark_url: '', favicon_url: '',
  primary_color: '#f59e0b', secondary_color: '', accent_color: '',
  font_family: '', tagline: '', support_email: '',
  // Legal & tax identity
  legal_name: '', brand_name: '', company_type: '', cin: '', incorporation_date: '',
  authorized_signatory_name: '', authorized_signatory_designation: '',
  // Role-based contacts
  primary_contact_name: '', primary_contact_email: '', primary_contact_phone: '', primary_contact_designation: '',
  billing_contact_name: '', billing_contact_email: '', billing_contact_phone: '', billing_contact_designation: '',
  technical_contact_name: '', technical_contact_email: '', technical_contact_phone: '', technical_contact_designation: '',
  dpo_contact_name: '', dpo_contact_email: '', dpo_contact_phone: '', dpo_contact_designation: '',
  escalation_contact_name: '', escalation_contact_email: '', escalation_contact_phone: '', escalation_contact_designation: '',
  // Addresses
  reg_address_line1: '', reg_address_line2: '', reg_city: '', reg_state: '', reg_country: 'India', reg_postal_code: '',
  billing_same_as_registered: true,
  billing_address_line1: '', billing_address_line2: '', billing_city: '', billing_state: '', billing_country: '', billing_postal_code: '',
  // Business profile
  industry: '', company_size_band: '', years_in_business: '', website_url: '', description: '',
  // Hiring footprint
  expected_hires_per_year: '', recruiter_count: '', hiring_locations: '', existing_ats: '', role_categories: '',
  // Commercial (writes to subscription_plans)
  plan_name: '', billing_cycle: 'monthly', price: '', currency: 'INR',
  payment_method: '', po_required: false, po_number: '',
  // Departments seeded with the company
  departments: [],
}

// Every onboarding-profile field the Edit modal can load & save (mirrors the
// backend COMPANY_PROFILE_FIELDS). Used to hydrate editForm from the full
// company detail so Edit exposes everything that View shows.
const EDIT_PROFILE_KEYS = [
  'logo_url', 'favicon_url', 'logo_dark_url', 'primary_color', 'secondary_color',
  'accent_color', 'font_family', 'tagline', 'support_email',
  'legal_name', 'brand_name', 'company_type', 'cin', 'incorporation_date',
  'authorized_signatory_name', 'authorized_signatory_designation',
  'primary_contact_name', 'primary_contact_email', 'primary_contact_phone', 'primary_contact_designation',
  'billing_contact_name', 'billing_contact_email', 'billing_contact_phone', 'billing_contact_designation',
  'technical_contact_name', 'technical_contact_email', 'technical_contact_phone', 'technical_contact_designation',
  'dpo_contact_name', 'dpo_contact_email', 'dpo_contact_phone', 'dpo_contact_designation',
  'escalation_contact_name', 'escalation_contact_email', 'escalation_contact_phone', 'escalation_contact_designation',
  'reg_address_line1', 'reg_address_line2', 'reg_city', 'reg_state', 'reg_country', 'reg_postal_code',
  'billing_address_line1', 'billing_address_line2', 'billing_city', 'billing_state', 'billing_country', 'billing_postal_code',
  'industry', 'company_size_band', 'years_in_business', 'website_url', 'description',
  'expected_hires_per_year', 'recruiter_count', 'hiring_locations', 'existing_ats', 'role_categories',
]

const WIZARD_STEPS = [
  { id: 1, label: 'Basics & Admin',    short: 'Basics',     required: true  },
  { id: 2, label: 'Legal Identity',     short: 'Legal',      required: false },
  { id: 3, label: 'Address & Contacts', short: 'Contacts',   required: false },
  { id: 4, label: 'Business Profile',   short: 'Business',   required: false },
  { id: 5, label: 'Hiring Footprint',   short: 'Hiring',     required: false },
  { id: 6, label: 'Branding',           short: 'Branding',   required: false },
  { id: 7, label: 'Commercial & Departments', short: 'Plan', required: false },
]



const FONTS = `
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');
`

const STYLES = `
  ${FONTS}

  .sa-root {
    min-height: 100vh;
    background: #f4f6fb;
    font-family: 'Syne', sans-serif;
    position: relative;
    overflow-x: hidden;
  }

  .sa-dotgrid {
    position: fixed;
    inset: 0;
    background-image: radial-gradient(circle, rgba(0,0,0,0.05) 1px, transparent 1px);
    background-size: 28px 28px;
    pointer-events: none;
    z-index: 0;
  }

  .sa-glow-amber {
    position: fixed;
    top: -200px;
    right: -100px;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(245,158,11,0.08) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .sa-glow-teal {
    position: fixed;
    bottom: -200px;
    left: -100px;
    width: 500px;
    height: 500px;
    background: radial-gradient(circle, rgba(0,212,180,0.06) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }

  .sa-content {
    position: relative;
    z-index: 1;
    padding: 0 28px 60px;
  }

  /* ── HEADER ── */
  .sa-header {
    padding: 32px 28px 28px;
    position: relative;
    z-index: 1;
    border-bottom: 1px solid #e5e7eb;
    margin-bottom: 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .sa-header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .sa-crown-badge {
    width: 48px;
    height: 48px;
    border-radius: 14px;
    background: linear-gradient(135deg, #f59e0b, #d97706);
    display: grid;
    place-items: center;
    box-shadow: 0 0 24px rgba(245,158,11,0.3), 0 4px 16px rgba(245,158,11,0.15);
  }

  .sa-title {
    font-size: 1.55rem;
    font-weight: 800;
    color: #111827;
    letter-spacing: -0.02em;
    line-height: 1;
    margin: 0;
  }

  .sa-subtitle {
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: #9ca3af;
    margin-top: 4px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .sa-add-btn {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 11px 22px;
    border-radius: 10px;
    border: none;
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: #fff;
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 0.85rem;
    cursor: pointer;
    box-shadow: 0 4px 20px rgba(245,158,11,0.3);
    transition: all 0.2s ease;
    letter-spacing: 0.01em;
  }

  .sa-add-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 28px rgba(245,158,11,0.4);
  }

  /* ── STATS ── */
  .sa-stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin-bottom: 32px;
  }

  .sa-stat-card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 20px 22px;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s, box-shadow 0.2s;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }

  .sa-stat-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent);
    opacity: 0.85;
  }

  .sa-stat-card:hover {
    border-color: #d1d5db;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  }

  .sa-stat-num {
    font-size: 2.4rem;
    font-weight: 800;
    color: #111827;
    line-height: 1;
    letter-spacing: -0.03em;
  }

  .sa-stat-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.67rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 6px;
  }

  .sa-stat-icon {
    position: absolute;
    top: 18px;
    right: 18px;
    opacity: 0.1;
  }

  /* ── SECTION HEADER ── */
  .sa-section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 20px;
  }

  .sa-section-title {
    font-size: 0.72rem;
    font-family: 'DM Mono', monospace;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .sa-section-title::before {
    content: '';
    width: 18px;
    height: 1px;
    background: #d1d5db;
  }

  /* ── COMPANY CARDS ── */
  .sa-cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 18px;
  }

  .sa-card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    transition: all 0.25s ease;
    animation: cardIn 0.4s ease both;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }

  .sa-card:hover {
    border-color: #d1d5db;
    transform: translateY(-3px);
    box-shadow: 0 12px 32px rgba(0,0,0,0.1);
  }

  @keyframes cardIn {
    from { opacity: 0; transform: translateY(16px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .sa-card-bar {
    height: 4px;
    width: 100%;
    flex-shrink: 0;
  }

  .sa-card-logo {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 28px 20px 20px;
    min-height: 100px;
    position: relative;
  }

  .sa-card-logo-fallback {
    width: 60px;
    height: 60px;
    border-radius: 14px;
    display: grid;
    place-items: center;
  }

  .sa-card-body {
    padding: 0 18px 14px;
    flex: 1;
  }

  .sa-card-name {
    font-size: 1rem;
    font-weight: 700;
    color: #111827;
    margin-bottom: 3px;
    letter-spacing: -0.01em;
  }

  .sa-card-slug {
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: #9ca3af;
    letter-spacing: 0.04em;
    margin-bottom: 14px;
  }

  .sa-card-stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 12px;
  }

  .sa-card-stat {
    background: #f9fafb;
    border: 1px solid #f3f4f6;
    border-radius: 8px;
    padding: 8px;
    text-align: center;
  }

  .sa-card-stat-num {
    font-size: 1.1rem;
    font-weight: 800;
    color: #1f2937;
    line-height: 1;
  }

  .sa-card-stat-lbl {
    font-family: 'DM Mono', monospace;
    font-size: 0.6rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-top: 3px;
  }

  .sa-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 20px;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    font-weight: 500;
    letter-spacing: 0.06em;
  }

  .sa-badge-active {
    background: rgba(0,168,107,0.08);
    color: #059669;
    border: 1px solid rgba(0,168,107,0.2);
  }

  .sa-badge-disabled {
    background: rgba(239,68,68,0.08);
    color: #dc2626;
    border: 1px solid rgba(239,68,68,0.18);
  }

  /* ── CARD FOOTER ── */
  .sa-card-footer {
    padding: 12px 18px;
    border-top: 1px solid #f3f4f6;
    display: flex;
    gap: 7px;
    align-items: center;
  }

  .sa-view-btn {
    flex: 1;
    padding: 8px 0;
    border-radius: 8px;
    border: none;
    color: #fff;
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 0.78rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    letter-spacing: 0.02em;
    transition: all 0.2s;
    opacity: 0.9;
  }

  .sa-view-btn:hover { opacity: 1; filter: brightness(1.1); }

  .sa-icon-btn {
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
    background: #f9fafb;
    cursor: pointer;
    color: #6b7280;
    display: grid;
    place-items: center;
    transition: all 0.2s;
  }

  .sa-icon-btn:hover {
    border-color: #d1d5db;
    color: #374151;
    background: #f3f4f6;
  }

  /* ── EMPTY STATE ── */
  .sa-empty {
    padding: 80px 40px;
    text-align: center;
    color: #d1d5db;
  }

  /* ── MODAL OVERLAY ── */
  .sa-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.35);
    backdrop-filter: blur(4px);
    display: grid;
    place-items: center;
    z-index: 1000;
    padding: 20px;
  }

  .sa-modal {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 20px;
    padding: 32px;
    width: 100%;
    max-width: 540px;
    max-height: 90vh;
    overflow-y: auto;
    box-shadow: 0 24px 60px rgba(0,0,0,0.15);
    position: relative;
    animation: modalIn 0.25s ease;
  }

  .sa-modal-sm {
    max-width: 460px;
  }

  @keyframes modalIn {
    from { opacity: 0; transform: scale(0.96) translateY(8px); }
    to   { opacity: 1; transform: scale(1) translateY(0); }
  }

  .sa-modal-close {
    position: absolute;
    top: 18px;
    right: 18px;
    width: 32px;
    height: 32px;
    border-radius: 8px;
    border: 1px solid #e5e7eb;
    background: #f9fafb;
    cursor: pointer;
    color: #9ca3af;
    display: grid;
    place-items: center;
    transition: all 0.15s;
  }

  .sa-modal-close:hover {
    background: #f3f4f6;
    color: #374151;
  }

  .sa-modal-title {
    font-size: 1.15rem;
    font-weight: 800;
    color: #111827;
    margin: 0 0 4px;
    letter-spacing: -0.01em;
  }

  .sa-modal-sub {
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: #9ca3af;
    letter-spacing: 0.05em;
    margin-bottom: 26px;
  }

  .sa-divider {
    border: none;
    border-top: 1px solid #f3f4f6;
    margin: 20px 0;
  }

  .sa-section-divider-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 16px;
  }

  /* ── FORM ── */
  .sa-field { margin-bottom: 14px; }

  .sa-label {
    display: block;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    font-weight: 500;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 6px;
  }

  .sa-input {
    width: 100%;
    padding: 10px 14px;
    border-radius: 9px;
    border: 1px solid #e5e7eb;
    background: #f9fafb;
    color: #111827;
    font-family: 'Syne', sans-serif;
    font-size: 0.875rem;
    outline: none;
    box-sizing: border-box;
    transition: border-color 0.2s, background 0.2s, box-shadow 0.2s;
  }

  .sa-input:focus {
    border-color: #f59e0b;
    background: #fff;
    box-shadow: 0 0 0 3px rgba(245,158,11,0.1);
  }

  .sa-input::placeholder { color: #c4c9d4; }

  .sa-input option {
    background: #ffffff;
    color: #111827;
  }

  .sa-grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .sa-span2 { grid-column: 1 / -1; }

  .sa-error {
    margin-top: 14px;
    padding: 10px 14px;
    border-radius: 9px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    color: #dc2626;
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
  }

  .sa-form-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
    margin-top: 24px;
  }

  .sa-btn-ghost {
    padding: 10px 22px;
    border-radius: 9px;
    border: 1px solid #e5e7eb;
    background: #f9fafb;
    color: #6b7280;
    font-family: 'Syne', sans-serif;
    font-weight: 600;
    font-size: 0.83rem;
    cursor: pointer;
    transition: all 0.2s;
  }

  .sa-btn-ghost:hover {
    border-color: #d1d5db;
    color: #374151;
    background: #f3f4f6;
  }

  .sa-btn-primary {
    padding: 10px 24px;
    border-radius: 9px;
    border: none;
    background: linear-gradient(135deg, #f59e0b, #d97706);
    color: #fff;
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 0.83rem;
    cursor: pointer;
    box-shadow: 0 4px 16px rgba(245,158,11,0.25);
    transition: all 0.2s;
    display: inline-flex;
    align-items: center;
  }

  .sa-btn-primary:hover:not(:disabled) {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(245,158,11,0.35);
  }

  .sa-btn-primary:disabled { opacity: 0.55; cursor: not-allowed; }

  .sa-btn-teal {
    background: linear-gradient(135deg, #00d4b4, #00b09b);
    box-shadow: 0 4px 16px rgba(0,212,180,0.2);
  }

  .sa-btn-teal:hover:not(:disabled) {
    box-shadow: 0 6px 20px rgba(0,212,180,0.3);
  }

  /* ── DETAIL MODAL ── */
  .sa-detail-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid #f3f4f6;
  }

  .sa-detail-row:last-child { border-bottom: none; }

  .sa-detail-key {
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .sa-detail-val {
    font-family: 'DM Mono', monospace;
    font-size: 0.8rem;
    color: #374151;
    font-weight: 500;
  }

  .sa-embed-box {
    margin-top: 16px;
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 14px 16px;
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: #6b7280;
    word-break: break-all;
    line-height: 1.6;
    position: relative;
  }

  .sa-embed-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.62rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 8px;
  }

  /* ── DETAIL HEADER ── */
  .sa-detail-head {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 22px;
    padding-bottom: 20px;
    border-bottom: 1px solid #f3f4f6;
  }

  .sa-detail-logo {
    width: 56px;
    height: 56px;
    border-radius: 14px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
    overflow: hidden;
  }

  /* ── LOADING ── */
  .sa-loading {
    padding: 64px;
    text-align: center;
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: #c4c9d4;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  @keyframes pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 0.8; }
  }

  .sa-loading { animation: pulse 1.5s ease-in-out infinite; }

  /* ── WIZARD ── */
  .sa-wiz {
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 28px;
    margin-top: 8px;
  }
  .sa-wiz-rail {
    border-right: 1px solid #f3f4f6;
    padding-right: 22px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .sa-wiz-step {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 9px;
    cursor: pointer;
    font-family: 'Syne', sans-serif;
    font-size: 0.82rem;
    color: #6b7280;
    border: 1px solid transparent;
    transition: all 0.15s;
  }
  .sa-wiz-step:hover { background: #f9fafb; color: #374151; }
  .sa-wiz-step.is-active {
    background: rgba(245,158,11,0.08);
    border-color: rgba(245,158,11,0.2);
    color: #b45309;
    font-weight: 700;
  }
  .sa-wiz-step.is-done { color: #059669; }
  .sa-wiz-num {
    width: 22px; height: 22px;
    border-radius: 50%;
    display: grid; place-items: center;
    font-size: 0.7rem;
    font-family: 'DM Mono', monospace;
    background: #f3f4f6; color: #9ca3af;
    flex-shrink: 0;
  }
  .sa-wiz-step.is-active .sa-wiz-num { background: #f59e0b; color: #fff; }
  .sa-wiz-step.is-done .sa-wiz-num   { background: #10b981; color: #fff; }
  .sa-wiz-opt {
    margin-left: auto;
    font-size: 0.55rem;
    font-family: 'DM Mono', monospace;
    color: #c4c9d4;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .sa-wiz-step.is-active .sa-wiz-opt { color: #d97706; }

  .sa-wiz-body { min-height: 360px; }
  .sa-wiz-step-title {
    font-family: 'Syne', sans-serif;
    font-size: 1.05rem;
    font-weight: 700;
    color: #1a1d23;
    margin-bottom: 4px;
  }
  .sa-wiz-step-sub {
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: #9ca3af;
    margin-bottom: 18px;
  }

  .sa-accordion {
    border: 1px solid #f3f4f6;
    border-radius: 10px;
    margin-bottom: 8px;
    overflow: hidden;
  }
  .sa-accordion-head {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 14px;
    cursor: pointer;
    background: #fafbfc;
    font-family: 'Syne', sans-serif;
    font-size: 0.82rem;
    font-weight: 600;
    color: #374151;
    user-select: none;
  }
  .sa-accordion-head:hover { background: #f3f4f6; }
  .sa-accordion-body { padding: 14px; border-top: 1px solid #f3f4f6; }
  .sa-accordion-chev { margin-left: auto; transition: transform 0.2s; }
  .sa-accordion.is-open .sa-accordion-chev { transform: rotate(180deg); }

  .sa-wiz-foot {
    margin-top: 20px;
    padding-top: 18px;
    border-top: 1px solid #f3f4f6;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .sa-wiz-foot .sa-spacer { flex: 1; }
  .sa-checkbox-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: 'Syne', sans-serif;
    font-size: 0.82rem;
    color: #374151;
    cursor: pointer;
    user-select: none;
  }

  @media (max-width: 860px) {
    .sa-wiz { grid-template-columns: 1fr; }
    .sa-wiz-rail {
      border-right: none;
      border-bottom: 1px solid #f3f4f6;
      padding: 0 0 14px 0;
      flex-direction: row;
      overflow-x: auto;
      gap: 6px;
    }
    .sa-wiz-step { flex-shrink: 0; }
    .sa-wiz-opt { display: none; }
  }

  /* Draft-restore banner */
  .sa-draft-banner {
    margin: 12px 0 4px;
    padding: 10px 14px;
    background: #fef3c7;
    border: 1px solid #fcd34d;
    border-radius: 10px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-family: 'Syne', sans-serif;
    font-size: 0.82rem;
    color: #78350f;
  }
  .sa-draft-banner > span { flex: 1; }
  .sa-draft-btn {
    padding: 6px 12px;
    border-radius: 7px;
    border: 1px solid #fcd34d;
    background: #fffbeb;
    color: #78350f;
    font-family: 'Syne', sans-serif;
    font-size: 0.75rem;
    font-weight: 600;
    cursor: pointer;
  }
  .sa-draft-btn:hover { background: #fef3c7; }
  .sa-draft-btn-primary {
    background: #f59e0b;
    border-color: #f59e0b;
    color: #fff;
  }
  .sa-draft-btn-primary:hover { background: #d97706; }

  /* Soft inline hint under fields */
  .sa-hint {
    margin-top: 4px;
    font-family: 'DM Mono', monospace;
    font-size: 0.66rem;
    line-height: 1.4;
  }
  .sa-hint-warn { color: #b45309; }

  /* ── OVERVIEW SHEET (per-company table) ── */
  .sa-sheet-wrap {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    overflow: hidden;
    margin-bottom: 32px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }
  .sa-sheet-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 16px 22px;
    border-bottom: 1px solid #f3f4f6;
  }
  .sa-sheet-head-title {
    font-family: 'Syne', sans-serif;
    font-weight: 700;
    font-size: 0.95rem;
    color: #111827;
  }
  .sa-sheet-scroll { overflow-x: auto; }
  .sa-sheet {
    width: 100%;
    border-collapse: collapse;
    font-family: 'Syne', sans-serif;
  }
  .sa-sheet thead th {
    text-align: left;
    padding: 11px 16px;
    font-family: 'DM Mono', monospace;
    font-size: 0.62rem;
    font-weight: 500;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    background: #fafbfc;
    border-bottom: 1px solid #f3f4f6;
    white-space: nowrap;
  }
  .sa-sheet th.sa-sheet-num, .sa-sheet td.sa-sheet-num { text-align: right; }
  .sa-sheet tbody td {
    padding: 12px 16px;
    border-bottom: 1px solid #f6f7f9;
    font-size: 0.85rem;
    color: #374151;
    white-space: nowrap;
  }
  .sa-sheet tbody tr:last-child td { border-bottom: none; }
  .sa-sheet tbody tr:hover { background: #fafbfc; }
  .sa-sheet-co {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .sa-sheet-dot {
    width: 9px; height: 9px;
    border-radius: 3px;
    flex-shrink: 0;
  }
  .sa-sheet-co-name { font-weight: 700; color: #111827; }
  .sa-sheet-co-slug {
    font-family: 'DM Mono', monospace;
    font-size: 0.66rem;
    color: #9ca3af;
  }
  .sa-sheet-num-val { font-weight: 700; color: #1f2937; }
  .sa-sheet tfoot td {
    padding: 12px 16px;
    border-top: 2px solid #f3f4f6;
    background: #fafbfc;
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .sa-sheet tfoot td.sa-sheet-num { font-weight: 800; color: #111827; font-size: 0.9rem; }
  .sa-sheet-pill {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 12px;
    font-family: 'DM Mono', monospace;
    font-size: 0.62rem;
    font-weight: 500;
  }
  .sa-sheet-pill-on  { background: rgba(0,168,107,0.08); color: #059669; }
  .sa-sheet-pill-off { background: rgba(239,68,68,0.08); color: #dc2626; }
`

export default function SuperAdminDashboard() {
  const location = useLocation()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(location.search)
  const deptViewCompanyId = location.pathname.includes('/departments') ? searchParams.get('company') : null
  const deptViewCompanyName = searchParams.get('name') || ''
  const deptViewColor = searchParams.get('color') || '#f59e0b'

  // ── Own company (Innomax) state ──────────────────────────────
  const [ownCompany, setOwnCompany] = useState(null)
  const [ownDepts, setOwnDepts] = useState([])
  const [ownDeptName, setOwnDeptName] = useState('')
  const [ownDeptDesc, setOwnDeptDesc] = useState('')
  const [ownDeptAdding, setOwnDeptAdding] = useState(false)
  const [ownDeptError, setOwnDeptError] = useState('')
  const [ownDeptDeleting, setOwnDeptDeleting] = useState('')

  const fetchOwnCompany = () => {
    api.get('/superadmin/own-company')
      .then(r => { setOwnCompany(r.data); setOwnDepts(r.data.departments || []) })
      .catch(() => {})
  }

  const handleAddOwnDept = async () => {
    if (!ownDeptName.trim()) return
    setOwnDeptAdding(true); setOwnDeptError('')
    try {
      await api.post(`/superadmin/companies/${ownCompany.id}/departments`, { name: ownDeptName.trim(), description: ownDeptDesc.trim() })
      setOwnDeptName(''); setOwnDeptDesc('')
      fetchOwnCompany()
    } catch (err) {
      setOwnDeptError(err.response?.data?.detail || 'Failed to add department')
    } finally { setOwnDeptAdding(false) }
  }

  const handleDeleteOwnDept = async (deptId) => {
    setOwnDeptDeleting(deptId)
    try {
      await api.delete(`/superadmin/companies/${ownCompany.id}/departments/${deptId}`)
      fetchOwnCompany()
    } catch (err) {
      setOwnDeptError(err.response?.data?.detail || 'Failed to delete')
    } finally { setOwnDeptDeleting('') }
  }

  const [companies, setCompanies] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [wizardStep, setWizardStep] = useState(1)
  const [openContact, setOpenContact] = useState('primary')
  const [draftAvailable, setDraftAvailable] = useState(false)
  const [draftRestored, setDraftRestored] = useState(false)
  // Tracks whether the onboarder hand-edited the slug. While false, the slug
  // auto-follows the company name; once true we stop clobbering their value.
  const [slugTouched, setSlugTouched] = useState(false)

  // Read any persisted draft so we can prompt to restore when the modal opens
  useEffect(() => {
    if (showModal) {
      try {
        const raw = localStorage.getItem(DRAFT_KEY)
        setDraftAvailable(!!raw && !draftRestored)
      } catch { setDraftAvailable(false) }
    }
  }, [showModal])

  // Persist every change so a half-filled wizard survives a modal close /
  // accidental reload. Skip when form is the untouched EMPTY_FORM.
  useEffect(() => {
    if (!showModal) return
    const isUntouched = form === EMPTY_FORM
    if (isUntouched) return
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ wizardStep, form })) } catch {}
  }, [form, wizardStep, showModal])

  const restoreDraft = () => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY)
      if (!raw) return
      const { wizardStep: ws, form: f } = JSON.parse(raw)
      if (f && typeof f === 'object') setForm({ ...EMPTY_FORM, ...f })
      if (typeof ws === 'number') setWizardStep(ws)
      // A restored draft may carry a slug the user already tuned — preserve it
      // by treating the slug as touched whenever it's non-empty.
      setSlugTouched(!!(f && f.slug))
      setDraftAvailable(false)
      setDraftRestored(true)
    } catch {}
  }
  const discardDraft = () => {
    try { localStorage.removeItem(DRAFT_KEY) } catch {}
    setDraftAvailable(false)
    setDraftRestored(false)
    setForm(EMPTY_FORM)
    setWizardStep(1)
    setSlugTouched(false)
  }

  const setField = (k, v) => setForm(p => ({ ...p, [k]: v }))

  // Company-name change: also auto-fill the slug until the user edits it by hand.
  const setCompanyName = (name) => setForm(p => ({
    ...p,
    company_name: name,
    slug: slugTouched ? p.slug : slugify(name),
  }))
  // Manual slug edit: mark it touched and lightly normalize as they type (keep
  // trailing hyphen while typing so multi-word slugs are possible; full slugify
  // runs on blur and on the backend).
  const setSlugManual = (raw) => {
    setSlugTouched(true)
    setField('slug', raw.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/-{2,}/g, '-'))
  }
  const closeWizard = () => {
    // Cancel just closes — draft is intentionally preserved so the user can
    // come back. Use the Discard button to clear it.
    setShowModal(false)
    setError('')
    setLogoPreview(null)
    setDeptTargetCompany('__new__')
    setQuickDeptName('')
    setQuickDeptDesc('')
    setAddedDepts([])
  }
  const goNext = () => setWizardStep(s => Math.min(WIZARD_STEPS.length, s + 1))
  const goPrev = () => setWizardStep(s => Math.max(1, s - 1))
  const [copied, setCopied] = useState('')
  const [newlyCreatedCompany, setNewlyCreatedCompany] = useState(null)
  const [selectedCompany, setSelectedCompany] = useState(null)
  const [editCompany, setEditCompany] = useState(null)
  const [editForm, setEditForm] = useState({})
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [editError, setEditError] = useState('')
  const [companyUsers, setCompanyUsers] = useState([])
  const [companyDepts, setCompanyDepts] = useState([])
  const [companyDetail, setCompanyDetail] = useState(null)

  const [logoUploading, setLogoUploading] = useState(false)
  const [logoPreview, setLogoPreview] = useState(null)
  const [editLogoUploading, setEditLogoUploading] = useState(false)
  const [editLogoPreview, setEditLogoPreview] = useState(null)

  // Add admin modal state
  const [addAdminCompany, setAddAdminCompany] = useState(null)
  const [adminForm, setAdminForm] = useState({ first_name: '', last_name: '', email: '', password: '' })
  const [adminShowPw, setAdminShowPw] = useState(false)
  const [adminSubmitting, setAdminSubmitting] = useState(false)
  const [adminError, setAdminError] = useState('')
  const [adminSuccess, setAdminSuccess] = useState('')

  // Quick-add department state (inside create modal)
  const [deptTargetCompany, setDeptTargetCompany] = useState('__new__')
  const [quickDeptName, setQuickDeptName] = useState('')
  const [quickDeptDesc, setQuickDeptDesc] = useState('')
  const [quickDeptAdding, setQuickDeptAdding] = useState(false)
  const [addedDepts, setAddedDepts] = useState([])

  // Department page state (for /superadmin/departments route)
  const [pageDepts, setPageDepts] = useState([])
  const [pageDeptForm, setPageDeptForm] = useState({ name: '', description: '' })
  const [pageDeptSubmitting, setPageDeptSubmitting] = useState(false)
  const [pageDeptError, setPageDeptError] = useState('')

  // Selected company for dept page (dropdown)
  const [deptPageCompany, setDeptPageCompany] = useState(null)
  const [deptPageLoading, setDeptPageLoading] = useState(false)

  const fetchCompanies = () => {
    setLoading(true)
    api.get('/superadmin/companies')
      .then(r => setCompanies(r.data || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  const handleLogoUpload = async (file, isEdit = false) => {
    if (!file) return
    const localUrl = URL.createObjectURL(file)
    if (isEdit) setEditLogoPreview(localUrl)
    else setLogoPreview(localUrl)
    if (isEdit) setEditLogoUploading(true)
    else setLogoUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const token = localStorage.getItem('hireflow_token')
      const res = await fetch('/api/v1/superadmin/upload-logo', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      })
      if (!res.ok) throw new Error('Upload failed')
      const data = await res.json()
      if (isEdit) setEditForm(p => ({ ...p, logo_url: data.logo_url }))
      else setForm(p => ({ ...p, logo_url: data.logo_url }))
    } catch {
      // Preview stays; user can still paste a direct image URL below
    } finally {
      if (isEdit) setEditLogoUploading(false)
      else setLogoUploading(false)
    }
  }

  const handleAddQuickDept = async () => {
    if (!quickDeptName.trim()) return
    if (deptTargetCompany === '__new__') {
      setForm(p => ({ ...p, departments: [...p.departments, { name: quickDeptName.trim(), description: quickDeptDesc.trim() }] }))
      setQuickDeptName('')
      setQuickDeptDesc('')
    } else {
      setQuickDeptAdding(true)
      try {
        await api.post(`/superadmin/companies/${deptTargetCompany}/departments`, {
          name: quickDeptName.trim(),
          description: quickDeptDesc.trim() || ''
        })
        const co = companies.find(c => c.id === deptTargetCompany)
        setAddedDepts(prev => [...prev, { name: quickDeptName.trim(), company: co?.name || '' }])
        setQuickDeptName('')
        setQuickDeptDesc('')
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to add department')
      }
      setQuickDeptAdding(false)
    }
  }

  useEffect(() => { fetchCompanies(); fetchOwnCompany() }, [])

  // When navigating to /superadmin/departments?company=<id>, load that company's depts
  useEffect(() => {
    if (deptViewCompanyId) {
      const found = companies.find(c => c.id === deptViewCompanyId)
      if (found) setDeptPageCompany(found)
      else if (companies.length > 0) {
        // company not in list, still load depts
        setDeptPageCompany({ id: deptViewCompanyId, name: deptViewCompanyName, primary_color: deptViewColor })
      }
      setPageDepts([])
      setDeptPageLoading(true)
      api.get(`/superadmin/companies/${deptViewCompanyId}/departments`)
        .then(r => setPageDepts(r.data || []))
        .catch(() => {})
        .finally(() => setDeptPageLoading(false))
    }
  }, [deptViewCompanyId, companies.length])

  const handleCreate = async (e) => {
    if (e && e.preventDefault) e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      // Strip empty strings → null so backend sees omitted fields rather than ""
      const payload = Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, v === '' ? null : v])
      )
      const res = await api.post('/superadmin/companies', payload)
      const newCompany = res.data
      await fetchCompanies()

      // Auto-select the newly created company if we're on departments page
      if (location.pathname.includes('/departments')) {
        selectDeptPageCompany(newCompany)
        setNewlyCreatedCompany(newCompany)
        setTimeout(() => setNewlyCreatedCompany(null), 3000)
      }

      setShowModal(false)
      setForm(EMPTY_FORM)
      setWizardStep(1)
      setLogoPreview(null)
      setDeptTargetCompany('__new__')
      setQuickDeptName('')
      setQuickDeptDesc('')
      setAddedDepts([])
      try { localStorage.removeItem(DRAFT_KEY) } catch {}
      setDraftAvailable(false)
      setDraftRestored(false)
      setSlugTouched(false)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create company')
    }
    setSubmitting(false)
  }

  const handlePageAddDept = async (e) => {
    e.preventDefault()
    if (!pageDeptForm.name.trim() || !deptPageCompany) return
    setPageDeptSubmitting(true)
    setPageDeptError('')
    try {
      const r = await api.post(`/superadmin/companies/${deptPageCompany.id}/departments`, pageDeptForm)
      setPageDepts(prev => [...prev, r.data].sort((a, b) => a.name.localeCompare(b.name)))
      setPageDeptForm({ name: '', description: '' })
    } catch (err) {
      setPageDeptError(err.response?.data?.detail || 'Failed to add department')
    }
    setPageDeptSubmitting(false)
  }

  const handlePageDeleteDept = async (deptId) => {
    if (!deptPageCompany) return
    await api.delete(`/superadmin/companies/${deptPageCompany.id}/departments/${deptId}`).catch(() => {})
    setPageDepts(prev => prev.filter(d => d.id !== deptId))
  }

  const selectDeptPageCompany = (c) => {
    setDeptPageCompany(c)
    setPageDepts([])
    setPageDeptForm({ name: '', description: '' })
    setPageDeptError('')
    setDeptPageLoading(true)
    navigate(`/superadmin/departments?company=${c.id}&name=${encodeURIComponent(c.name)}&color=${encodeURIComponent(c.primary_color || '#f59e0b')}`)
    api.get(`/superadmin/companies/${c.id}/departments`)
      .then(r => setPageDepts(r.data || []))
      .catch(() => {})
      .finally(() => setDeptPageLoading(false))
  }

  const handleToggle = async (id) => {
    await api.put(`/superadmin/companies/${id}/toggle`).catch(() => {})
    fetchCompanies()
  }

  const handleRegenerateKey = async (id) => {
    const r = await api.post(`/superadmin/companies/${id}/regenerate-key`).catch(() => null)
    if (r) fetchCompanies()
  }

  const openEdit = (c) => {
    setEditCompany(c)
    // Seed with the summary we already have so the form opens instantly…
    setEditForm({
      company_name: c.name, slug: c.slug,
      logo_url: c.logo_url || '', favicon_url: c.favicon_url || '',
      primary_color: c.primary_color || '#f59e0b',
      secondary_color: c.secondary_color || '',
      accent_color: c.accent_color || '',
      font_family: c.font_family || '',
      tagline: c.tagline || '',
      support_email: c.support_email || '',
    })
    setEditError('')
    setEditLogoPreview(null)
    // …then enrich with the full registration profile so every field is editable.
    api.get(`/superadmin/companies/${c.id}`)
      .then(r => {
        const d = r.data || {}
        setEditForm(prev => ({
          ...prev,
          company_name: d.name ?? prev.company_name,
          slug: d.slug ?? prev.slug,
          billing_same_as_registered: !!d.billing_same_as_registered,
          ...Object.fromEntries(EDIT_PROFILE_KEYS.map(k => [k, d[k] ?? ''])),
        }))
      })
      .catch(() => {})
  }

  const handleUpdate = async (e) => {
    e.preventDefault()
    setEditSubmitting(true)
    setEditError('')
    try {
      // Empty strings → null so optional date/number columns aren't sent ""
      const payload = Object.fromEntries(
        Object.entries(editForm).map(([k, v]) => [k, v === '' ? null : v])
      )
      await api.put(`/superadmin/companies/${editCompany.id}`, payload)
      fetchCompanies()
      setEditCompany(null)
    } catch (err) {
      setEditError(err.response?.data?.detail || 'Failed to update company')
    }
    setEditSubmitting(false)
  }

  // Compact bound-input helpers for the Edit modal's profile sections.
  const editField = (key, label, { type = 'text', placeholder = '', span = false } = {}) => (
    <div className={`sa-field${span ? ' sa-span2' : ''}`}>
      <label className="sa-label">{label}</label>
      <input className="sa-input" type={type} placeholder={placeholder}
        value={editForm[key] ?? ''}
        onChange={e => setEditForm(p => ({ ...p, [key]: e.target.value }))} />
    </div>
  )
  const editSelect = (key, label, options) => (
    <div className="sa-field">
      <label className="sa-label">{label}</label>
      <select className="sa-input" value={editForm[key] ?? ''}
        onChange={e => setEditForm(p => ({ ...p, [key]: e.target.value }))}>
        <option value="">—</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  )

  const copyKey = (key, id) => {
    navigator.clipboard.writeText(key)
    setCopied(id)
    setTimeout(() => setCopied(''), 2000)
  }

  const openView = (c) => {
    setSelectedCompany(c)
    setCompanyUsers([])
    setCompanyDepts([])
    setCompanyDetail(null)
    api.get(`/superadmin/companies/${c.id}`)
      .then(r => { setCompanyUsers(r.data?.users || []); setCompanyDetail(r.data || null) })
      .catch(() => {})
    api.get(`/superadmin/companies/${c.id}/departments`)
      .then(r => setCompanyDepts(r.data || []))
      .catch(() => {})
  }

  const openAddAdmin = (c) => {
    setAddAdminCompany(c)
    setAdminForm({ first_name: '', last_name: '', email: '', password: '' })
    setAdminError('')
    setAdminSuccess('')
    setAdminShowPw(false)
  }

  const handleCreateAdmin = async (e) => {
    e.preventDefault()
    setAdminError('')
    setAdminSuccess('')
    setAdminSubmitting(true)
    try {
      await api.post(`/superadmin/companies/${addAdminCompany.id}/admin`, adminForm)
      setAdminSuccess(`Admin account created! They can now log in at /admin/login`)
      fetchCompanies()
      setAdminForm({ first_name: '', last_name: '', email: '', password: '' })
    } catch (err) {
      setAdminError(err.response?.data?.detail || 'Failed to create admin')
    } finally {
      setAdminSubmitting(false)
    }
  }

  const totalUsers = companies.reduce((s, c) => s + (c.total_users || 0), 0)
  const totalJobs = companies.reduce((s, c) => s + (c.total_jobs || 0), 0)
  const totalDepartments = companies.reduce((s, c) => s + (c.total_departments || 0), 0)
  const totalCandidates = companies.reduce((s, c) => s + (c.total_candidates || 0), 0)
  const activeCompanies = companies.filter(c => c.is_active).length

  return (
    <div className="sa-root">
      <style>{STYLES}</style>
      <div className="sa-dotgrid" />
      <div className="sa-glow-amber" />
      <div className="sa-glow-teal" />

      {/* Header */}
      <header className="sa-header">
        <div className="sa-header-left">
          <div className="sa-crown-badge">
            <Crown size={22} color="#fff" />
          </div>
          <div>
            <h1 className="sa-title">Command Center</h1>
            <div className="sa-subtitle">Super Admin · Client Management</div>
          </div>
        </div>
        <button className="sa-add-btn" onClick={() => setShowModal(true)}>
          <Plus size={15} /> Onboard Client
        </button>
      </header>

      <div className="sa-content">

        {/* Stats */}
        <div className="sa-stats">
          {[
            { label: 'Total Clients', value: companies.length, icon: Building2, accent: '#f59e0b' },
            { label: 'Active', value: activeCompanies, icon: Zap, accent: '#00d48c' },
            { label: 'Total Users', value: totalUsers, icon: Users, accent: '#60a5fa' },
            { label: 'Total Jobs', value: totalJobs, icon: Briefcase, accent: '#a78bfa' },
          ].map((s, i) => (
            <div key={i} className="sa-stat-card" style={{ '--accent': s.accent }}>
              <s.icon size={42} className="sa-stat-icon" color={s.accent} />
              <div className="sa-stat-num">{s.value}</div>
              <div className="sa-stat-label">{s.label}</div>
            </div>
          ))}
        </div>

        {/* ── Innomax Own Company Departments ── */}
        {ownCompany && (
          <div style={{
            background: '#fff',
            border: '1.5px solid #e5e7eb',
            borderRadius: 16,
            padding: '24px 28px',
            marginBottom: 32,
            boxShadow: '0 2px 12px rgba(43,124,186,0.06)',
          }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
              <div style={{
                width: 42, height: 42, borderRadius: 12,
                background: 'rgba(43,124,186,0.1)',
                display: 'grid', placeItems: 'center', flexShrink: 0,
              }}>
                <Layers size={20} color="#2b7cba" />
              </div>
              <div>
                <div style={{ fontFamily: 'Syne, sans-serif', fontWeight: 700, fontSize: '1rem', color: '#1a1d23' }}>
                  {ownCompany.name} — Departments
                </div>
                <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '0.65rem', color: '#9ca3af', letterSpacing: '0.06em', textTransform: 'uppercase', marginTop: 2 }}>
                  Platform owner company · /{ownCompany.slug}
                </div>
              </div>
              <div style={{ marginLeft: 'auto', background: 'rgba(43,124,186,0.08)', color: '#2b7cba', borderRadius: 20, padding: '4px 12px', fontSize: '0.75rem', fontFamily: 'DM Mono, monospace', fontWeight: 600 }}>
                {ownDepts.length} dept{ownDepts.length !== 1 ? 's' : ''}
              </div>
            </div>

            {/* Add row */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 14, alignItems: 'center' }}>
              <input
                style={{ flex: 2, padding: '10px 14px', borderRadius: 9, border: '1.5px solid #e5e7eb', fontSize: '0.88rem', fontFamily: 'Syne, sans-serif', outline: 'none', color: '#1a1d23' }}
                placeholder="Department name"
                value={ownDeptName}
                onChange={e => setOwnDeptName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddOwnDept() } }}
                onFocus={e => e.target.style.borderColor = '#2b7cba'}
                onBlur={e => e.target.style.borderColor = '#e5e7eb'}
              />
              <input
                style={{ flex: 3, padding: '10px 14px', borderRadius: 9, border: '1.5px solid #e5e7eb', fontSize: '0.88rem', fontFamily: 'Syne, sans-serif', outline: 'none', color: '#1a1d23' }}
                placeholder="Description (optional)"
                value={ownDeptDesc}
                onChange={e => setOwnDeptDesc(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddOwnDept() } }}
                onFocus={e => e.target.style.borderColor = '#2b7cba'}
                onBlur={e => e.target.style.borderColor = '#e5e7eb'}
              />
              <button
                type="button"
                onClick={handleAddOwnDept}
                disabled={!ownDeptName.trim() || ownDeptAdding}
                style={{
                  flexShrink: 0, padding: '10px 18px', borderRadius: 9, border: 'none',
                  background: ownDeptName.trim() ? 'linear-gradient(135deg,#2b7cba,#1a5f96)' : '#f3f4f6',
                  color: ownDeptName.trim() ? '#fff' : '#9ca3af',
                  cursor: ownDeptName.trim() ? 'pointer' : 'not-allowed',
                  fontFamily: 'Syne, sans-serif', fontWeight: 700, fontSize: '0.82rem',
                  display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap',
                  transition: 'all 0.2s',
                }}
              >
                <Plus size={14} /> {ownDeptAdding ? 'Adding…' : 'Add Department'}
              </button>
            </div>

            {ownDeptError && (
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626', borderRadius: 8, padding: '8px 14px', fontSize: '0.8rem', marginBottom: 10 }}>
                {ownDeptError}
              </div>
            )}

            {/* Department list */}
            {ownDepts.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '24px 0', color: '#9ca3af', fontFamily: 'DM Mono, monospace', fontSize: '0.72rem', letterSpacing: '0.06em' }}>
                No departments yet — add one above
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
                {ownDepts.map(d => (
                  <div key={d.id} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '11px 14px', borderRadius: 10,
                    background: 'rgba(43,124,186,0.04)',
                    border: '1px solid rgba(43,124,186,0.12)',
                    transition: 'border-color 0.15s',
                  }}
                    onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(43,124,186,0.3)'}
                    onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(43,124,186,0.12)'}
                  >
                    <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#2b7cba', flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontFamily: 'Syne, sans-serif', fontWeight: 600, fontSize: '0.85rem', color: '#1a1d23', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {d.name}
                      </div>
                      {d.description && (
                        <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '0.65rem', color: '#9ca3af', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {d.description}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteOwnDept(d.id)}
                      disabled={ownDeptDeleting === d.id}
                      style={{ flexShrink: 0, padding: 5, borderRadius: 7, border: '1px solid rgba(239,68,68,0.2)', background: 'rgba(239,68,68,0.06)', cursor: 'pointer', color: '#f87171', display: 'grid', placeItems: 'center', transition: 'all 0.15s', opacity: ownDeptDeleting === d.id ? 0.5 : 1 }}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Section label */}
        <div className="sa-section-head">
          <div className="sa-section-title">Client Companies ({companies.length})</div>
        </div>

        {/* Cards */}
        {loading ? (
          <div className="sa-loading">Fetching clients…</div>
        ) : companies.length === 0 ? (
          <div className="sa-empty">
            <Crown size={36} style={{ marginBottom: 12, opacity: 0.25, color: '#9ca3af' }} />
            <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '0.75rem', letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9ca3af' }}>No clients onboarded yet</div>
            <div style={{ marginTop: 6, fontSize: '0.72rem', fontFamily: 'DM Mono, monospace', color: '#c4c9d4' }}>Click "Onboard Client" to get started</div>
          </div>
        ) : (
          <div className="sa-cards">
            {companies.map((c, i) => (
              <div key={c.id} className="sa-card" style={{ animationDelay: `${i * 0.06}s` }}>
                <div className="sa-card-bar" style={{ background: c.primary_color || '#f59e0b' }} />

                {/* Logo */}
                <div className="sa-card-logo" style={{ background: `${c.primary_color || '#f59e0b'}10` }}>
                  {c.logo_url ? (
                    <img src={c.logo_url} alt={c.name} style={{ maxHeight: 64, maxWidth: '100%', objectFit: 'contain' }} />
                  ) : (
                    <div className="sa-card-logo-fallback" style={{ background: `${c.primary_color || '#f59e0b'}20` }}>
                      <Building2 size={26} style={{ color: c.primary_color || '#f59e0b' }} />
                    </div>
                  )}
                </div>

                {/* Body */}
                <div className="sa-card-body">
                  <div className="sa-card-name">{c.name}</div>
                  <div className="sa-card-slug">/{c.slug}</div>
                  <div className="sa-card-stats">
                    <div className="sa-card-stat">
                      <div className="sa-card-stat-num">{c.total_users}</div>
                      <div className="sa-card-stat-lbl">Users</div>
                    </div>
                    <div className="sa-card-stat">
                      <div className="sa-card-stat-num">{c.total_jobs}</div>
                      <div className="sa-card-stat-lbl">Jobs</div>
                    </div>
                  </div>
                  <span className={`sa-badge ${c.is_active ? 'sa-badge-active' : 'sa-badge-disabled'}`}>
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'currentColor', display: 'inline-block' }} />
                    {c.is_active ? 'Active' : 'Disabled'}
                  </span>
                </div>

                {/* Footer */}
                <div className="sa-card-footer">
                  <button
                    className="sa-view-btn"
                    style={{ background: c.primary_color || '#f59e0b' }}
                    onClick={() => openView(c)}
                  >
                    <Eye size={13} /> View
                  </button>
                  <button
                    className="sa-icon-btn"
                    title="Edit branding (logo, colors, font)"
                    onClick={() => openEdit(c)}
                    style={{ color: '#00b09b', borderColor: '#a7f3d0', background: '#ecfdf5' }}
                  >
                    <Edit3 size={14} />
                  </button>
                  <button
                    className="sa-icon-btn"
                    title="Add Admin User"
                    onClick={() => openAddAdmin(c)}
                    style={{ color: '#2b7cba', borderColor: '#bfdbfe', background: '#eff6ff' }}
                  >
                    <UserPlus size={14} />
                  </button>
                  <button className="sa-icon-btn" title={c.is_active ? 'Disable' : 'Enable'} onClick={() => handleToggle(c.id)}>
                    {c.is_active
                      ? <ToggleRight size={15} style={{ color: '#00d48c' }} />
                      : <ToggleLeft size={15} />}
                  </button>
                  <button className="sa-icon-btn" title="Regenerate API Key" onClick={() => handleRegenerateKey(c.id)}>
                    <RefreshCw size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── Per-Company Overview Sheet ── */}
        {!loading && companies.length > 0 && (
          <div className="sa-sheet-wrap" style={{ marginTop: 32, marginBottom: 0 }}>
            <div className="sa-sheet-head">
              <Layers size={18} color="#f59e0b" />
              <span className="sa-sheet-head-title">Company Overview</span>
              <span className="sa-card-slug" style={{ marginLeft: 'auto' }}>
                {companies.length} compan{companies.length !== 1 ? 'ies' : 'y'}
              </span>
            </div>
            <div className="sa-sheet-scroll">
              <table className="sa-sheet">
                <thead>
                  <tr>
                    <th>Company</th>
                    <th className="sa-sheet-num">Users</th>
                    <th className="sa-sheet-num">Departments</th>
                    <th className="sa-sheet-num">Jobs</th>
                    <th className="sa-sheet-num">Candidates</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map(c => (
                    <tr key={c.id}>
                      <td>
                        <div className="sa-sheet-co">
                          <span className="sa-sheet-dot" style={{ background: c.primary_color || '#f59e0b' }} />
                          <div>
                            <div className="sa-sheet-co-name">{c.name}</div>
                            <div className="sa-sheet-co-slug">/{c.slug}</div>
                          </div>
                        </div>
                      </td>
                      <td className="sa-sheet-num"><span className="sa-sheet-num-val">{c.total_users || 0}</span></td>
                      <td className="sa-sheet-num"><span className="sa-sheet-num-val">{c.total_departments || 0}</span></td>
                      <td className="sa-sheet-num"><span className="sa-sheet-num-val">{c.total_jobs || 0}</span></td>
                      <td className="sa-sheet-num"><span className="sa-sheet-num-val">{c.total_candidates || 0}</span></td>
                      <td>
                        <span className={`sa-sheet-pill ${c.is_active ? 'sa-sheet-pill-on' : 'sa-sheet-pill-off'}`}>
                          {c.is_active ? 'Active' : 'Disabled'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>Total</td>
                    <td className="sa-sheet-num">{totalUsers}</td>
                    <td className="sa-sheet-num">{totalDepartments}</td>
                    <td className="sa-sheet-num">{totalJobs}</td>
                    <td className="sa-sheet-num">{totalCandidates}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ── ADD COMPANY MODAL (7-step wizard) ── */}
      {showModal && (
        <div className="sa-overlay" onClick={closeWizard}>
          <div className="sa-modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 900 }}>
            <button className="sa-modal-close" onClick={closeWizard}><X size={14} /></button>
            <h2 className="sa-modal-title">Onboard New Client</h2>
            <div className="sa-modal-sub">Step {wizardStep} of {WIZARD_STEPS.length} · Only the first step is required</div>

            {draftAvailable && (
              <div className="sa-draft-banner">
                <span><b>Unsaved draft</b> from a previous session. Pick up where you left off?</span>
                <button type="button" className="sa-draft-btn sa-draft-btn-primary" onClick={restoreDraft}>Restore</button>
                <button type="button" className="sa-draft-btn" onClick={discardDraft}>Discard</button>
              </div>
            )}

            <form onSubmit={e => {
              e.preventDefault()
              if (wizardStep < WIZARD_STEPS.length) { goNext() } else { handleCreate(e) }
            }}>
              <div className="sa-wiz">
                {/* Left rail */}
                <nav className="sa-wiz-rail">
                  {WIZARD_STEPS.map(s => (
                    <div
                      key={s.id}
                      className={
                        'sa-wiz-step' +
                        (s.id === wizardStep ? ' is-active' : '') +
                        (s.id < wizardStep ? ' is-done' : '')
                      }
                      onClick={() => setWizardStep(s.id)}
                    >
                      <span className="sa-wiz-num">{s.id < wizardStep ? <Check size={12}/> : s.id}</span>
                      <span>{s.label}</span>
                      {!s.required && <span className="sa-wiz-opt">opt</span>}
                    </div>
                  ))}
                </nav>

                {/* Step body */}
                <div className="sa-wiz-body">
                  {/* ── STEP 1: Basics + first admin ── */}
                  {wizardStep === 1 && (
                    <>
                      <div className="sa-wiz-step-title">Company Basics</div>
                      <div className="sa-wiz-step-sub">Required to activate the workspace.</div>
                      <div className="sa-grid2">
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Company Name *</label>
                          <input className="sa-input" placeholder="Innomax Solutions" value={form.company_name}
                            onChange={e => setCompanyName(e.target.value)} required />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Slug * (URL ID — auto-filled from the name, editable)</label>
                          <input className="sa-input" placeholder="innomax-solutions" value={form.slug} autoComplete="off"
                            onChange={e => setSlugManual(e.target.value)}
                            onBlur={e => setField('slug', slugify(e.target.value))} required />
                          {form.slug && (
                            <div className="sa-hint">URL: <strong>{form.slug}.hireflow.io</strong></div>
                          )}
                        </div>
                      </div>

                      <hr className="sa-divider" />
                      <div className="sa-section-divider-label">First Admin User</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">First Name *</label>
                          <input className="sa-input" placeholder="John" value={form.admin_first_name}
                            onChange={e => setField('admin_first_name', e.target.value)} required />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Last Name *</label>
                          <input className="sa-input" placeholder="Smith" value={form.admin_last_name}
                            onChange={e => setField('admin_last_name', e.target.value)} required />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Admin Email *</label>
                          <input className="sa-input" type="email" placeholder="admin@acme.com" value={form.admin_email}
                            onChange={e => setField('admin_email', e.target.value)} required />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Temp Password *</label>
                          <input className="sa-input" type="password" placeholder="••••••••" value={form.admin_password}
                            onChange={e => setField('admin_password', e.target.value)} required />
                        </div>
                      </div>
                    </>
                  )}

                  {/* ── STEP 2: Legal & Tax Identity ── */}
                  {wizardStep === 2 && (
                    <>
                      <div className="sa-wiz-step-title">Legal & Tax Identity</div>
                      <div className="sa-wiz-step-sub">Optional — needed for compliant invoicing & KYC.</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">Legal Company Name</label>
                          <input className="sa-input" placeholder="Acme Technologies Pvt Ltd" value={form.legal_name}
                            onChange={e => setField('legal_name', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Trading / Brand Name</label>
                          <input className="sa-input" placeholder="Acme" value={form.brand_name}
                            onChange={e => setField('brand_name', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Company Type</label>
                          <select className="sa-input" value={form.company_type} onChange={e => setField('company_type', e.target.value)}>
                            {COMPANY_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">CIN / Registration No.</label>
                          <input className="sa-input" placeholder="U72200KA2015PTC079876" value={form.cin}
                            onChange={e => setField('cin', e.target.value)} />
                          {validateCIN(form.cin) && (
                            <div className="sa-hint sa-hint-warn">{validateCIN(form.cin)}</div>
                          )}
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Date of Incorporation</label>
                          <input className="sa-input" type="date" value={form.incorporation_date}
                            onChange={e => setField('incorporation_date', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Authorized Signatory</label>
                          <input className="sa-input" placeholder="Jane Doe" value={form.authorized_signatory_name}
                            onChange={e => setField('authorized_signatory_name', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Signatory Designation</label>
                          <input className="sa-input" placeholder="Director" value={form.authorized_signatory_designation}
                            onChange={e => setField('authorized_signatory_designation', e.target.value)} />
                        </div>
                      </div>
                    </>
                  )}

                  {/* ── STEP 3: Addresses & Role Contacts ── */}
                  {wizardStep === 3 && (
                    <>
                      <div className="sa-wiz-step-title">Addresses & Role Contacts</div>
                      <div className="sa-wiz-step-sub">Optional — registered address powers tax compute; contacts route notifications correctly.</div>

                      <div className="sa-section-divider-label">Registered Office</div>
                      <div className="sa-grid2">
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Address Line 1</label>
                          <input className="sa-input" value={form.reg_address_line1} onChange={e => setField('reg_address_line1', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Address Line 2</label>
                          <input className="sa-input" value={form.reg_address_line2} onChange={e => setField('reg_address_line2', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">City</label>
                          <input className="sa-input" value={form.reg_city} onChange={e => setField('reg_city', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">State</label>
                          <input className="sa-input" value={form.reg_state} onChange={e => setField('reg_state', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Country</label>
                          <input className="sa-input" value={form.reg_country} onChange={e => setField('reg_country', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">PIN / ZIP</label>
                          <input className="sa-input" value={form.reg_postal_code} autoComplete="off" maxLength={20} onChange={e => setField('reg_postal_code', e.target.value)} />
                          {validatePostalCode(form.reg_postal_code, form.reg_country) && (
                            <div className="sa-hint sa-hint-warn">{validatePostalCode(form.reg_postal_code, form.reg_country)}</div>
                          )}
                        </div>
                      </div>

                      <hr className="sa-divider" />
                      <label className="sa-checkbox-row" style={{ marginBottom: 10 }}>
                        <input type="checkbox" checked={!!form.billing_same_as_registered}
                          onChange={e => setField('billing_same_as_registered', e.target.checked)} />
                        Billing address is same as registered office
                      </label>

                      {!form.billing_same_as_registered && (
                        <>
                          <div className="sa-section-divider-label">Billing Address</div>
                          <div className="sa-grid2">
                            <div className="sa-field sa-span2">
                              <label className="sa-label">Address Line 1</label>
                              <input className="sa-input" value={form.billing_address_line1} onChange={e => setField('billing_address_line1', e.target.value)} />
                            </div>
                            <div className="sa-field sa-span2">
                              <label className="sa-label">Address Line 2</label>
                              <input className="sa-input" value={form.billing_address_line2} onChange={e => setField('billing_address_line2', e.target.value)} />
                            </div>
                            <div className="sa-field">
                              <label className="sa-label">City</label>
                              <input className="sa-input" value={form.billing_city} onChange={e => setField('billing_city', e.target.value)} />
                            </div>
                            <div className="sa-field">
                              <label className="sa-label">State</label>
                              <input className="sa-input" value={form.billing_state} onChange={e => setField('billing_state', e.target.value)} />
                            </div>
                            <div className="sa-field">
                              <label className="sa-label">Country</label>
                              <input className="sa-input" value={form.billing_country} onChange={e => setField('billing_country', e.target.value)} />
                            </div>
                            <div className="sa-field">
                              <label className="sa-label">PIN / ZIP</label>
                              <input className="sa-input" value={form.billing_postal_code} autoComplete="off" maxLength={20} onChange={e => setField('billing_postal_code', e.target.value)} />
                            </div>
                          </div>
                        </>
                      )}

                      <hr className="sa-divider" />
                      <div className="sa-section-divider-label">Role-based Contacts</div>

                      {[
                        { key: 'primary',   title: 'Primary / Admin Contact' },
                        { key: 'billing',   title: 'Billing Contact' },
                        { key: 'technical', title: 'Technical Contact' },
                        { key: 'dpo',       title: 'DPO / Privacy Contact' },
                        { key: 'escalation',title: 'Escalation Contact' },
                      ].map(({ key, title }) => {
                        const isOpen = openContact === key
                        return (
                          <div key={key} className={'sa-accordion' + (isOpen ? ' is-open' : '')}>
                            <div className="sa-accordion-head" onClick={() => setOpenContact(isOpen ? null : key)}>
                              <span>{title}</span>
                              <ChevronDown size={14} className="sa-accordion-chev" />
                            </div>
                            {isOpen && (
                              <div className="sa-accordion-body">
                                <div className="sa-grid2">
                                  <div className="sa-field">
                                    <label className="sa-label">Name</label>
                                    <input className="sa-input" value={form[`${key}_contact_name`]} onChange={e => setField(`${key}_contact_name`, e.target.value)} />
                                  </div>
                                  <div className="sa-field">
                                    <label className="sa-label">Designation</label>
                                    <input className="sa-input" value={form[`${key}_contact_designation`]} onChange={e => setField(`${key}_contact_designation`, e.target.value)} />
                                  </div>
                                  <div className="sa-field">
                                    <label className="sa-label">Email</label>
                                    <input className="sa-input" type="email" value={form[`${key}_contact_email`]} onChange={e => setField(`${key}_contact_email`, e.target.value)} />
                                  </div>
                                  <div className="sa-field">
                                    <label className="sa-label">Phone</label>
                                    <input className="sa-input" value={form[`${key}_contact_phone`]} onChange={e => setField(`${key}_contact_phone`, e.target.value)} />
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </>
                  )}

                  {/* ── STEP 4: Business Profile ── */}
                  {wizardStep === 4 && (
                    <>
                      <div className="sa-wiz-step-title">Business Profile</div>
                      <div className="sa-wiz-step-sub">Optional — helps us tailor plans and benchmarks.</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">Industry</label>
                          <input className="sa-input" placeholder="SaaS, Manufacturing, …" value={form.industry}
                            onChange={e => setField('industry', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Company Size</label>
                          <select className="sa-input" value={form.company_size_band} onChange={e => setField('company_size_band', e.target.value)}>
                            {COMPANY_SIZE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Years in Business</label>
                          <input className="sa-input" type="number" min="0" value={form.years_in_business}
                            onChange={e => setField('years_in_business', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Website URL</label>
                          <input className="sa-input" type="url" placeholder="https://acme.com" value={form.website_url}
                            onChange={e => setField('website_url', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Description</label>
                          <textarea className="sa-input" rows={4} placeholder="What does the company do?" value={form.description}
                            onChange={e => setField('description', e.target.value)} />
                        </div>
                      </div>
                    </>
                  )}

                  {/* ── STEP 5: Hiring Footprint ── */}
                  {wizardStep === 5 && (
                    <>
                      <div className="sa-wiz-step-title">Hiring Footprint</div>
                      <div className="sa-wiz-step-sub">Optional — sizing inputs for picking the right plan.</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">Expected Hires / Year</label>
                          <input className="sa-input" type="number" min="0" value={form.expected_hires_per_year}
                            onChange={e => setField('expected_hires_per_year', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Recruiter Headcount</label>
                          <input className="sa-input" type="number" min="0" value={form.recruiter_count}
                            onChange={e => setField('recruiter_count', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Hiring Locations (comma-separated)</label>
                          <input className="sa-input" placeholder="Bengaluru, Mumbai, Remote-India" value={form.hiring_locations}
                            onChange={e => setField('hiring_locations', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Existing ATS (if any)</label>
                          <input className="sa-input" placeholder="Greenhouse, Lever, none…" value={form.existing_ats}
                            onChange={e => setField('existing_ats', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Role Categories</label>
                          <input className="sa-input" placeholder="Engineering, Sales, Ops" value={form.role_categories}
                            onChange={e => setField('role_categories', e.target.value)} />
                        </div>
                      </div>
                    </>
                  )}

                  {/* ── STEP 6: Branding ── */}
                  {wizardStep === 6 && (
                    <>
                      <div className="sa-wiz-step-title">Branding</div>
                      <div className="sa-wiz-step-sub">Optional — controls how the candidate-facing portal looks.</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">Primary Color</label>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <input type="color" value={form.primary_color}
                              onChange={e => setField('primary_color', e.target.value)}
                              style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                            <input className="sa-input" style={{ flex: 1 }} value={form.primary_color}
                              onChange={e => setField('primary_color', e.target.value)} />
                          </div>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Secondary Color</label>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <input type="color" value={form.secondary_color || '#6b7280'}
                              onChange={e => setField('secondary_color', e.target.value)}
                              style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                            <input className="sa-input" style={{ flex: 1 }} placeholder="#6b7280 (optional)"
                              value={form.secondary_color} onChange={e => setField('secondary_color', e.target.value)} />
                          </div>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Accent Color</label>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <input type="color" value={form.accent_color || '#10b981'}
                              onChange={e => setField('accent_color', e.target.value)}
                              style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                            <input className="sa-input" style={{ flex: 1 }} placeholder="#10b981 (optional)"
                              value={form.accent_color} onChange={e => setField('accent_color', e.target.value)} />
                          </div>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Font Family</label>
                          <select className="sa-input" value={form.font_family} onChange={e => setField('font_family', e.target.value)}>
                            {FONT_OPTIONS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                          </select>
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Tagline</label>
                          <input className="sa-input" placeholder="Your hiring partner for the future" value={form.tagline}
                            onChange={e => setField('tagline', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Support Email</label>
                          <input className="sa-input" type="email" placeholder="hr@company.com" value={form.support_email}
                            onChange={e => setField('support_email', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Favicon URL</label>
                          <input className="sa-input" placeholder="https://… /favicon.ico" value={form.favicon_url}
                            onChange={e => setField('favicon_url', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Company Logo (Light)</label>
                          <div
                            style={{ border: '1px dashed rgba(245,158,11,0.4)', borderRadius: 10, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 16, background: 'rgba(245,158,11,0.03)', cursor: 'pointer' }}
                            onClick={() => document.getElementById('create-logo-input').click()}
                          >
                            {(logoPreview || form.logo_url) ? (
                              <img src={logoPreview || form.logo_url} alt="Logo" style={{ width: 52, height: 52, objectFit: 'contain', borderRadius: 8, background: '#f3f4f6' }} onError={e => { e.target.style.display='none' }} />
                            ) : (
                              <div style={{ width: 52, height: 52, borderRadius: 10, background: '#f3f4f6', border: '1px solid #e5e7eb', display: 'grid', placeItems: 'center' }}>
                                <Upload size={20} style={{ color: '#c4c9d4' }} />
                              </div>
                            )}
                            <div style={{ flex: 1 }}>
                              <div style={{ color: logoUploading ? '#f59e0b' : '#374151', fontSize: '0.82rem', fontFamily: 'Syne, sans-serif', fontWeight: 600 }}>
                                {logoUploading ? 'Uploading…' : (logoPreview || form.logo_url) ? 'Click to change logo' : 'Click to upload logo'}
                              </div>
                              <div style={{ color: '#9ca3af', fontSize: '0.67rem', fontFamily: 'DM Mono, monospace', marginTop: 3 }}>PNG, JPG, SVG, WebP</div>
                            </div>
                            <input id="create-logo-input" type="file" accept="image/*" style={{ display: 'none' }}
                              onChange={e => { if (e.target.files[0]) handleLogoUpload(e.target.files[0]); e.target.value = '' }} />
                          </div>
                          <input className="sa-input" style={{ marginTop: 8 }} placeholder="Or paste light-logo URL" value={form.logo_url}
                            onChange={e => { setField('logo_url', e.target.value); setLogoPreview(null) }} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-label">Logo (Dark variant) URL</label>
                          <input className="sa-input" placeholder="https://… /logo-dark.png" value={form.logo_dark_url}
                            onChange={e => setField('logo_dark_url', e.target.value)} />
                        </div>
                      </div>
                    </>
                  )}

                  {/* ── STEP 7: Commercial & Departments ── */}
                  {wizardStep === 7 && (
                    <>
                      <div className="sa-wiz-step-title">Commercial & Departments</div>
                      <div className="sa-wiz-step-sub">Optional — plan terms and starting departments.</div>

                      <div className="sa-section-divider-label">Plan</div>
                      <div className="sa-grid2">
                        <div className="sa-field">
                          <label className="sa-label">Plan</label>
                          <select className="sa-input" value={form.plan_name} onChange={e => setField('plan_name', e.target.value)}>
                            {PLAN_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Billing Cycle</label>
                          <select className="sa-input" value={form.billing_cycle} onChange={e => setField('billing_cycle', e.target.value)}>
                            <option value="monthly">Monthly</option>
                            <option value="annual">Annual</option>
                          </select>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Price</label>
                          <input className="sa-input" type="number" min="0" step="0.01" value={form.price}
                            onChange={e => setField('price', e.target.value)} />
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Currency</label>
                          <input className="sa-input" placeholder="INR" maxLength={3} value={form.currency}
                            onChange={e => setField('currency', e.target.value.toUpperCase())} />
                          {validateCurrency(form.currency) && (
                            <div className="sa-hint sa-hint-warn">{validateCurrency(form.currency)}</div>
                          )}
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">Payment Method</label>
                          <select className="sa-input" value={form.payment_method} onChange={e => setField('payment_method', e.target.value)}>
                            {PAYMENT_METHOD_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                          </select>
                        </div>
                        <div className="sa-field">
                          <label className="sa-label">PO Number</label>
                          <input className="sa-input" value={form.po_number} disabled={!form.po_required}
                            onChange={e => setField('po_number', e.target.value)} />
                        </div>
                        <div className="sa-field sa-span2">
                          <label className="sa-checkbox-row">
                            <input type="checkbox" checked={!!form.po_required}
                              onChange={e => setField('po_required', e.target.checked)} />
                            Purchase order required for invoices
                          </label>
                        </div>
                      </div>

                      <hr className="sa-divider" />
                      <div className="sa-section-divider-label">Starting Departments</div>

                      <div className="sa-field">
                        <label className="sa-label">Add Department To</label>
                        <select
                          className="sa-input"
                          value={deptTargetCompany}
                          onChange={e => setDeptTargetCompany(e.target.value)}
                          style={{ cursor: 'pointer' }}
                        >
                          <option value="__new__">⬡ This new company (being created)</option>
                          {companies.map(c => (
                            <option key={c.id} value={c.id}>{c.name}</option>
                          ))}
                        </select>
                      </div>

                      <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
                        <input
                          className="sa-input"
                          style={{ flex: 2 }}
                          placeholder="Department name"
                          value={quickDeptName}
                          onChange={e => setQuickDeptName(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddQuickDept() } }}
                        />
                        <input
                          className="sa-input"
                          style={{ flex: 2 }}
                          placeholder="Description (optional)"
                          value={quickDeptDesc}
                          onChange={e => setQuickDeptDesc(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddQuickDept() } }}
                        />
                        <button
                          type="button"
                          onClick={handleAddQuickDept}
                          disabled={!quickDeptName.trim() || quickDeptAdding}
                          style={{ flexShrink: 0, padding: '10px 18px', borderRadius: 8, border: 'none', background: 'rgba(245,158,11,0.15)', color: '#f59e0b', cursor: 'pointer', fontFamily: 'Syne, sans-serif', fontWeight: 700, fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap', opacity: quickDeptName.trim() ? 1 : 0.5 }}
                        >
                          <Plus size={13} /> {quickDeptAdding ? 'Adding…' : 'Add'}
                        </button>
                      </div>

                      {form.departments.length > 0 && (
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ fontSize: '0.63rem', fontFamily: 'DM Mono, monospace', color: '#d97706', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 7 }}>
                            Queued for new company
                          </div>
                          {form.departments.map((dept, idx) => (
                            <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, background: '#fffbeb', border: '1px solid #fde68a', marginBottom: 5 }}>
                              <span style={{ flex: 1, color: '#374151', fontSize: '0.82rem', fontFamily: 'Syne, sans-serif', fontWeight: 500 }}>{dept.name}</span>
                              {dept.description && <span style={{ color: '#9ca3af', fontSize: '0.7rem', fontFamily: 'DM Mono, monospace' }}>{dept.description}</span>}
                              <button
                                type="button"
                                onClick={() => setForm(p => ({ ...p, departments: p.departments.filter((_, i) => i !== idx) }))}
                                style={{ padding: 4, borderRadius: 6, border: '1px solid rgba(239,68,68,0.2)', background: 'rgba(239,68,68,0.06)', cursor: 'pointer', color: '#f87171', display: 'grid', placeItems: 'center', flexShrink: 0 }}
                              >
                                <X size={11} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}

                      {addedDepts.length > 0 && (
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ fontSize: '0.63rem', fontFamily: 'DM Mono, monospace', color: '#059669', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 7 }}>
                            Saved to existing companies
                          </div>
                          {addedDepts.map((d, i) => (
                            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, background: '#f0fdf4', border: '1px solid #bbf7d0', marginBottom: 5 }}>
                              <Check size={13} style={{ color: '#059669', flexShrink: 0 }} />
                              <span style={{ flex: 1, color: '#374151', fontSize: '0.82rem', fontFamily: 'Syne, sans-serif' }}>{d.name}</span>
                              <span style={{ color: '#6b7280', fontSize: '0.68rem', fontFamily: 'DM Mono, monospace' }}>→ {d.company}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  {error && <div className="sa-error" style={{ marginTop: 14 }}>{error}</div>}
                </div>
              </div>

              {/* Footer */}
              <div className="sa-wiz-foot">
                <button type="button" className="sa-btn-ghost" onClick={closeWizard}>Cancel</button>
                {wizardStep > 1 && (
                  <button type="button" className="sa-btn-ghost" onClick={goPrev}>← Back</button>
                )}
                {(draftRestored || draftAvailable) && (
                  <button type="button" className="sa-btn-ghost" onClick={discardDraft} title="Clear saved draft">Discard draft</button>
                )}
                <div className="sa-spacer" />
                {wizardStep > 1 && wizardStep < WIZARD_STEPS.length && (
                  <button type="button" className="sa-btn-ghost" onClick={goNext}>Skip</button>
                )}
                <button type="submit" className="sa-btn-primary" disabled={submitting || logoUploading}>
                  {wizardStep < WIZARD_STEPS.length
                    ? 'Next →'
                    : (logoUploading ? 'Uploading logo…' : submitting ? 'Creating…' : 'Create Company')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── EDIT MODAL ── */}
      {editCompany && (
        <div className="sa-overlay" onClick={() => { setEditCompany(null); setEditLogoPreview(null) }}>
          <div className="sa-modal" onClick={e => e.stopPropagation()}>
            <button className="sa-modal-close" onClick={() => { setEditCompany(null); setEditLogoPreview(null) }}><X size={14} /></button>
            <h2 className="sa-modal-title">Edit Company</h2>
            <div className="sa-modal-sub">{editCompany.name}</div>

            <form onSubmit={handleUpdate}>
              <div className="sa-field">
                <label className="sa-label">Company Name *</label>
                <input className="sa-input" value={editForm.company_name}
                  onChange={e => setEditForm(p => ({ ...p, company_name: e.target.value }))} required />
              </div>
              <div className="sa-field">
                <label className="sa-label">Slug * (changing this changes the company URL)</label>
                <input className="sa-input" value={editForm.slug}
                  onChange={e => setEditForm(p => ({ ...p, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/-{2,}/g, '-') }))}
                  onBlur={e => setEditForm(p => ({ ...p, slug: slugify(e.target.value) }))} required />
                {editForm.slug && (
                  <div className="sa-hint">URL: <strong>{editForm.slug}.hireflow.io</strong></div>
                )}
              </div>
              <div className="sa-field">
                <label className="sa-label">Company Logo</label>
                <div
                  style={{ border: '1px dashed rgba(0,180,160,0.4)', borderRadius: 10, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 16, background: 'rgba(0,212,180,0.03)', cursor: 'pointer', transition: 'border-color 0.2s' }}
                  onClick={() => document.getElementById('edit-logo-input').click()}
                  onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(0,180,160,0.7)'}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(0,180,160,0.4)'}
                >
                  {(editLogoPreview || editForm.logo_url) ? (
                    <img src={editLogoPreview || editForm.logo_url} alt="Logo preview" style={{ width: 52, height: 52, objectFit: 'contain', borderRadius: 8, background: '#f3f4f6', flexShrink: 0 }} onError={e => { e.target.style.display = 'none' }} />
                  ) : (
                    <div style={{ width: 52, height: 52, borderRadius: 10, background: '#f3f4f6', border: '1px solid #e5e7eb', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
                      <Upload size={20} style={{ color: '#c4c9d4' }} />
                    </div>
                  )}
                  <div style={{ flex: 1 }}>
                    <div style={{ color: editLogoUploading ? '#00b09b' : '#374151', fontSize: '0.82rem', fontFamily: 'Syne, sans-serif', fontWeight: 600 }}>
                      {editLogoUploading ? 'Uploading…' : (editLogoPreview || editForm.logo_url) ? 'Click to change logo' : 'Click to upload logo'}
                    </div>
                    <div style={{ color: '#9ca3af', fontSize: '0.67rem', fontFamily: 'DM Mono, monospace', marginTop: 3 }}>PNG, JPG, SVG, WebP supported</div>
                  </div>
                  <input id="edit-logo-input" type="file" accept="image/*" style={{ display: 'none' }}
                    onChange={e => { if (e.target.files[0]) handleLogoUpload(e.target.files[0], true); e.target.value = '' }} />
                </div>
                <input className="sa-input" style={{ marginTop: 8 }} placeholder="Or paste image URL directly" value={editForm.logo_url}
                  onChange={e => { setEditForm(p => ({ ...p, logo_url: e.target.value })); setEditLogoPreview(null) }} />
              </div>
              <div className="sa-field">
                <label className="sa-label">Primary Color</label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input type="color" value={editForm.primary_color}
                    onChange={e => setEditForm(p => ({ ...p, primary_color: e.target.value }))}
                    style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                  <input className="sa-input" style={{ flex: 1 }} value={editForm.primary_color}
                    onChange={e => setEditForm(p => ({ ...p, primary_color: e.target.value }))} />
                </div>
              </div>
              <div className="sa-field">
                <label className="sa-label">Secondary Color</label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input type="color" value={editForm.secondary_color || '#6b7280'}
                    onChange={e => setEditForm(p => ({ ...p, secondary_color: e.target.value }))}
                    style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                  <input className="sa-input" style={{ flex: 1 }} placeholder="#6b7280 (optional)"
                    value={editForm.secondary_color} onChange={e => setEditForm(p => ({ ...p, secondary_color: e.target.value }))} />
                </div>
              </div>
              <div className="sa-field">
                <label className="sa-label">Accent Color</label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input type="color" value={editForm.accent_color || '#10b981'}
                    onChange={e => setEditForm(p => ({ ...p, accent_color: e.target.value }))}
                    style={{ width: 42, height: 40, borderRadius: 8, border: '1px solid #e5e7eb', cursor: 'pointer', padding: 2, background: '#fff' }} />
                  <input className="sa-input" style={{ flex: 1 }} placeholder="#10b981 (optional)"
                    value={editForm.accent_color} onChange={e => setEditForm(p => ({ ...p, accent_color: e.target.value }))} />
                </div>
              </div>
              <div className="sa-field">
                <label className="sa-label">Font Family</label>
                <select className="sa-input" value={editForm.font_family}
                  onChange={e => setEditForm(p => ({ ...p, font_family: e.target.value }))}>
                  {FONT_OPTIONS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                </select>
              </div>
              <div className="sa-field sa-span2">
                <label className="sa-label">Tagline</label>
                <input className="sa-input" placeholder="Your hiring partner for the future" value={editForm.tagline}
                  onChange={e => setEditForm(p => ({ ...p, tagline: e.target.value }))} />
              </div>
              <div className="sa-field">
                <label className="sa-label">Support Email</label>
                <input className="sa-input" type="email" placeholder="hr@company.com" value={editForm.support_email}
                  onChange={e => setEditForm(p => ({ ...p, support_email: e.target.value }))} />
              </div>
              <div className="sa-field">
                <label className="sa-label">Favicon URL</label>
                <input className="sa-input" placeholder="https://… /favicon.ico" value={editForm.favicon_url}
                  onChange={e => setEditForm(p => ({ ...p, favicon_url: e.target.value }))} />
              </div>

              {/* ── Legal & Tax Identity ── */}
              <hr className="sa-divider" />
              <div className="sa-section-divider-label">Legal &amp; Tax Identity</div>
              <div className="sa-grid2">
                {editField('legal_name', 'Legal Name')}
                {editField('brand_name', 'Brand Name')}
                {editSelect('company_type', 'Company Type', COMPANY_TYPE_OPTIONS)}
                {editField('cin', 'CIN')}
                {editField('incorporation_date', 'Incorporation Date', { type: 'date' })}
                {editField('authorized_signatory_name', 'Authorized Signatory')}
                {editField('authorized_signatory_designation', 'Signatory Designation', { span: true })}
              </div>

              {/* ── Contacts ── */}
              <hr className="sa-divider" />
              <div className="sa-section-divider-label">Primary Contact</div>
              <div className="sa-grid2">
                {editField('primary_contact_name', 'Name')}
                {editField('primary_contact_email', 'Email', { type: 'email' })}
                {editField('primary_contact_phone', 'Phone')}
                {editField('primary_contact_designation', 'Designation')}
              </div>
              <div className="sa-section-divider-label" style={{ marginTop: 16 }}>Billing Contact</div>
              <div className="sa-grid2">
                {editField('billing_contact_name', 'Name')}
                {editField('billing_contact_email', 'Email', { type: 'email' })}
                {editField('billing_contact_phone', 'Phone')}
                {editField('billing_contact_designation', 'Designation')}
              </div>
              <div className="sa-section-divider-label" style={{ marginTop: 16 }}>Technical Contact</div>
              <div className="sa-grid2">
                {editField('technical_contact_name', 'Name')}
                {editField('technical_contact_email', 'Email', { type: 'email' })}
                {editField('technical_contact_phone', 'Phone')}
                {editField('technical_contact_designation', 'Designation')}
              </div>
              <div className="sa-section-divider-label" style={{ marginTop: 16 }}>Data Protection Officer</div>
              <div className="sa-grid2">
                {editField('dpo_contact_name', 'Name')}
                {editField('dpo_contact_email', 'Email', { type: 'email' })}
                {editField('dpo_contact_phone', 'Phone')}
                {editField('dpo_contact_designation', 'Designation')}
              </div>
              <div className="sa-section-divider-label" style={{ marginTop: 16 }}>Escalation Contact</div>
              <div className="sa-grid2">
                {editField('escalation_contact_name', 'Name')}
                {editField('escalation_contact_email', 'Email', { type: 'email' })}
                {editField('escalation_contact_phone', 'Phone')}
                {editField('escalation_contact_designation', 'Designation')}
              </div>

              {/* ── Addresses ── */}
              <hr className="sa-divider" />
              <div className="sa-section-divider-label">Registered Address</div>
              <div className="sa-grid2">
                {editField('reg_address_line1', 'Address Line 1', { span: true })}
                {editField('reg_address_line2', 'Address Line 2', { span: true })}
                {editField('reg_city', 'City')}
                {editField('reg_state', 'State')}
                {editField('reg_country', 'Country')}
                {editField('reg_postal_code', 'Postal Code', { placeholder: 'e.g. 500001' })}
              </div>
              <label className="sa-checkbox-row" style={{ marginTop: 12 }}>
                <input type="checkbox" checked={!!editForm.billing_same_as_registered}
                  onChange={e => setEditForm(p => ({ ...p, billing_same_as_registered: e.target.checked }))} />
                Billing address same as registered
              </label>
              {!editForm.billing_same_as_registered && (
                <>
                  <div className="sa-section-divider-label" style={{ marginTop: 16 }}>Billing Address</div>
                  <div className="sa-grid2">
                    {editField('billing_address_line1', 'Address Line 1', { span: true })}
                    {editField('billing_address_line2', 'Address Line 2', { span: true })}
                    {editField('billing_city', 'City')}
                    {editField('billing_state', 'State')}
                    {editField('billing_country', 'Country')}
                    {editField('billing_postal_code', 'Postal Code', { placeholder: 'e.g. 500001' })}
                  </div>
                </>
              )}

              {/* ── Business Profile ── */}
              <hr className="sa-divider" />
              <div className="sa-section-divider-label">Business Profile</div>
              <div className="sa-grid2">
                {editField('industry', 'Industry')}
                {editSelect('company_size_band', 'Company Size', COMPANY_SIZE_OPTIONS)}
                {editField('years_in_business', 'Years in Business', { type: 'number' })}
                {editField('website_url', 'Website')}
                {editField('description', 'Description', { span: true })}
              </div>

              {/* ── Hiring Footprint ── */}
              <hr className="sa-divider" />
              <div className="sa-section-divider-label">Hiring Footprint</div>
              <div className="sa-grid2">
                {editField('expected_hires_per_year', 'Expected Hires / Year', { type: 'number' })}
                {editField('recruiter_count', 'Recruiters', { type: 'number' })}
                {editField('hiring_locations', 'Hiring Locations', { span: true, placeholder: 'comma-separated' })}
                {editField('existing_ats', 'Existing ATS')}
                {editField('role_categories', 'Role Categories')}
              </div>

              {editError && <div className="sa-error">{editError}</div>}

              <div className="sa-form-actions">
                <button type="button" className="sa-btn-ghost" onClick={() => { setEditCompany(null); setEditLogoPreview(null) }}>Cancel</button>
                <button type="submit" className="sa-btn-primary sa-btn-teal" disabled={editSubmitting || editLogoUploading}>
                  {editLogoUploading ? 'Uploading logo…' : editSubmitting ? 'Saving…' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── ADD ADMIN MODAL ── */}
      {addAdminCompany && (
        <div className="sa-overlay" onClick={() => setAddAdminCompany(null)}>
          <div className="sa-modal sa-modal-sm" onClick={e => e.stopPropagation()}>
            <button className="sa-modal-close" onClick={() => setAddAdminCompany(null)}><X size={14} /></button>

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
              <div style={{ width: 42, height: 42, borderRadius: 12, background: '#eff6ff', display: 'grid', placeItems: 'center', flexShrink: 0 }}>
                <UserPlus size={20} color="#2b7cba" />
              </div>
              <div>
                <h2 className="sa-modal-title" style={{ marginBottom: 2 }}>Add Admin User</h2>
                <div className="sa-modal-sub" style={{ marginBottom: 0 }}>{addAdminCompany.name}</div>
              </div>
            </div>

            {adminSuccess ? (
              <div style={{ padding: '16px', borderRadius: 12, background: '#f0fdf4', border: '1px solid #bbf7d0', marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <Check size={16} color="#16a34a" />
                  <span style={{ fontFamily: 'Syne, sans-serif', fontWeight: 700, color: '#15803d', fontSize: '0.88rem' }}>Admin created!</span>
                </div>
                <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '0.72rem', color: '#166534' }}>
                  Email: <strong>{adminForm.email || '—'}</strong><br />
                  Login URL: <strong>/admin/login</strong>
                </div>
              </div>
            ) : null}

            <form onSubmit={handleCreateAdmin}>
              <div className="sa-grid2">
                <div className="sa-field">
                  <label className="sa-label">First Name *</label>
                  <input className="sa-input" required placeholder="John"
                    value={adminForm.first_name}
                    onChange={e => setAdminForm(p => ({ ...p, first_name: e.target.value }))} />
                </div>
                <div className="sa-field">
                  <label className="sa-label">Last Name *</label>
                  <input className="sa-input" required placeholder="Smith"
                    value={adminForm.last_name}
                    onChange={e => setAdminForm(p => ({ ...p, last_name: e.target.value }))} />
                </div>
                <div className="sa-field sa-span2">
                  <label className="sa-label">Admin Email *</label>
                  <input className="sa-input" required type="email" placeholder="admin@company.com"
                    value={adminForm.email}
                    onChange={e => setAdminForm(p => ({ ...p, email: e.target.value }))} />
                </div>
                <div className="sa-field sa-span2">
                  <label className="sa-label">Password *</label>
                  <div style={{ position: 'relative' }}>
                    <input
                      className="sa-input"
                      required
                      type={adminShowPw ? 'text' : 'password'}
                      placeholder="••••••••"
                      style={{ paddingRight: 40 }}
                      value={adminForm.password}
                      onChange={e => setAdminForm(p => ({ ...p, password: e.target.value }))}
                    />
                    <button
                      type="button"
                      onClick={() => setAdminShowPw(p => !p)}
                      style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 2 }}
                    >
                      {adminShowPw ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>
              </div>

              {adminError && <div className="sa-error">{adminError}</div>}

              <div className="sa-form-actions">
                <button type="button" className="sa-btn-ghost" onClick={() => setAddAdminCompany(null)}>Cancel</button>
                <button
                  type="submit"
                  className="sa-btn-primary"
                  disabled={adminSubmitting}
                  style={{ background: 'linear-gradient(135deg,#2b7cba,#1a5f96)', boxShadow: '0 4px 16px rgba(43,124,186,0.25)' }}
                >
                  {adminSubmitting ? 'Creating…' : 'Create Admin'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── DETAIL MODAL ── */}
      {selectedCompany && (
        <div className="sa-overlay" onClick={() => setSelectedCompany(null)}>
          <div className="sa-modal" onClick={e => e.stopPropagation()}>
            <button className="sa-modal-close" onClick={() => setSelectedCompany(null)}><X size={14} /></button>

            <div className="sa-detail-head">
              <div className="sa-detail-logo" style={{ background: `${selectedCompany.primary_color}18` }}>
                {selectedCompany.logo_url
                  ? <img src={selectedCompany.logo_url} alt={selectedCompany.name} style={{ maxWidth: 56, maxHeight: 56, objectFit: 'contain' }} />
                  : <Building2 size={24} style={{ color: selectedCompany.primary_color }} />}
              </div>
              <div>
                <h2 className="sa-modal-title" style={{ marginBottom: 0 }}>{selectedCompany.name}</h2>
                <div className="sa-modal-sub" style={{ marginBottom: 0 }}>/{selectedCompany.slug}</div>
              </div>
            </div>

            {(() => {
              const adminUser = companyUsers.find(u => u.role === 'admin')
              return [
                { k: 'Status', v: selectedCompany.is_active ? 'Active' : 'Disabled' },
                { k: 'Admin Email', v: adminUser ? adminUser.email : companyUsers.length === 0 ? 'Loading…' : '—' },
                { k: 'Users', v: selectedCompany.total_users },
                { k: 'Jobs', v: selectedCompany.total_jobs },
                { k: 'Candidates', v: selectedCompany.total_candidates ?? '—' },
                { k: 'Created', v: new Date(selectedCompany.created_at).toLocaleDateString() },
              ].map(row => (
                <div key={row.k} className="sa-detail-row">
                  <span className="sa-detail-key">{row.k}</span>
                  <span className="sa-detail-val" style={row.k === 'Admin Email' ? { fontFamily: 'DM Mono, monospace', fontSize: '0.72rem', color: '#a0c4ff' } : {}}>{row.v}</span>
                </div>
              ))
            })()}

            {/* Full onboarding / registration profile */}
            {!companyDetail ? (
              <div style={{ marginTop: 16, fontSize: '0.78rem', color: '#9ca3af', fontFamily: 'DM Mono, monospace' }}>Loading registration details…</div>
            ) : (() => {
              const d = companyDetail
              const sub = d.subscription
              const fmtDate = v => v ? new Date(v).toLocaleDateString() : null
              const contact = (label, p) => ({ title: label, rows: [
                ['Name', d[`${p}_name`]], ['Email', d[`${p}_email`]],
                ['Phone', d[`${p}_phone`]], ['Designation', d[`${p}_designation`]],
              ]})
              const sections = [
                { title: 'Legal & Tax Identity', rows: [
                  ['Legal Name', d.legal_name], ['Brand Name', d.brand_name],
                  ['Company Type', d.company_type], ['CIN', d.cin],
                  ['Incorporation Date', fmtDate(d.incorporation_date)],
                  ['Authorized Signatory', d.authorized_signatory_name],
                  ['Signatory Designation', d.authorized_signatory_designation],
                ]},
                contact('Primary Contact', 'primary_contact'),
                contact('Billing Contact', 'billing_contact'),
                contact('Technical Contact', 'technical_contact'),
                contact('Data Protection Officer', 'dpo_contact'),
                contact('Escalation Contact', 'escalation_contact'),
                { title: 'Registered Address', rows: [
                  ['Address Line 1', d.reg_address_line1], ['Address Line 2', d.reg_address_line2],
                  ['City', d.reg_city], ['State', d.reg_state],
                  ['Country', d.reg_country], ['Postal Code', d.reg_postal_code],
                ]},
                { title: 'Billing Address', rows: d.billing_same_as_registered ? [
                  ['Billing Address', 'Same as registered address'],
                ] : [
                  ['Address Line 1', d.billing_address_line1], ['Address Line 2', d.billing_address_line2],
                  ['City', d.billing_city], ['State', d.billing_state],
                  ['Country', d.billing_country], ['Postal Code', d.billing_postal_code],
                ]},
                { title: 'Business Profile', rows: [
                  ['Industry', d.industry], ['Company Size', d.company_size_band],
                  ['Years in Business', d.years_in_business], ['Website', d.website_url],
                  ['Description', d.description],
                ]},
                { title: 'Hiring Footprint', rows: [
                  ['Expected Hires / Year', d.expected_hires_per_year], ['Recruiters', d.recruiter_count],
                  ['Hiring Locations', d.hiring_locations], ['Existing ATS', d.existing_ats],
                  ['Role Categories', d.role_categories],
                ]},
                { title: 'Branding', rows: [
                  ['Primary Color', d.primary_color], ['Secondary Color', d.secondary_color],
                  ['Accent Color', d.accent_color], ['Font', d.font_family],
                  ['Tagline', d.tagline], ['Support Email', d.support_email],
                ]},
                { title: 'Commercial Plan', rows: sub ? [
                  ['Plan', sub.plan_name], ['Billing Cycle', sub.billing_cycle],
                  ['Price', sub.price != null ? `${sub.currency || ''} ${sub.price}`.trim() : null],
                  ['Payment Method', sub.payment_method],
                  ['PO Required', sub.po_required ? 'Yes' : null], ['PO Number', sub.po_number],
                  ['Status', sub.status],
                ] : [] },
              ]
              const visible = sections
                .map(s => ({ ...s, rows: s.rows.filter(([, v]) => v !== null && v !== undefined && v !== '') }))
                .filter(s => s.rows.length)
              if (!visible.length) {
                return <div style={{ marginTop: 16, fontSize: '0.78rem', color: '#9ca3af', fontFamily: 'DM Mono, monospace' }}>No additional registration details were provided.</div>
              }
              const isColor = v => typeof v === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v)
              return (
                <div style={{ marginTop: 8 }}>
                  {visible.map(s => (
                    <div key={s.title} style={{ marginTop: 16 }}>
                      <div className="sa-section-divider-label">{s.title}</div>
                      {s.rows.map(([k, v]) => (
                        <div key={k} className="sa-detail-row">
                          <span className="sa-detail-key">{k}</span>
                          <span className="sa-detail-val" style={{ maxWidth: '62%', textAlign: 'right', wordBreak: 'break-word', display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end' }}>
                            {isColor(v) && <span style={{ width: 12, height: 12, borderRadius: 3, background: v, border: '1px solid #e5e7eb', flexShrink: 0 }} />}
                            {String(v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )
            })()}

            <div style={{ marginTop: 18 }}>
              <div className="sa-embed-label">Embed Code</div>
              <div className="sa-embed-box">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                  <span>{`<iframe src="${window.location.origin}/jobs?key=${selectedCompany.api_key}" width="100%" height="700" frameborder="0"></iframe>`}</span>
                  <button
                    onClick={() => copyKey(selectedCompany.api_key, selectedCompany.id + '_detail')}
                    style={{ flexShrink: 0, background: 'none', border: 'none', cursor: 'pointer', color: copied === selectedCompany.id + '_detail' ? '#059669' : '#9ca3af', padding: 0 }}>
                    {copied === selectedCompany.id + '_detail' ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                </div>
              </div>
            </div>

            <div style={{ marginTop: 18 }}>
              <div className="sa-embed-label" style={{ marginBottom: 8 }}>Departments</div>
              {companyDepts.length === 0 ? (
                <div style={{ fontSize: '0.78rem', color: '#9ca3af', fontFamily: 'DM Mono, monospace' }}>No departments yet</div>
              ) : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {companyDepts.map(d => (
                    <span key={d.id} style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      background: `${selectedCompany.primary_color}18`,
                      color: selectedCompany.primary_color,
                      border: `1px solid ${selectedCompany.primary_color}40`,
                      borderRadius: 6, padding: '3px 10px',
                      fontSize: '0.75rem', fontFamily: 'DM Mono, monospace',
                    }}>
                      {d.name}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <div className="sa-form-actions" style={{ marginTop: 20 }}>
              <button className="sa-btn-ghost" onClick={() => setSelectedCompany(null)}>Close</button>
              <button className="sa-btn-primary" style={{ background: selectedCompany.primary_color }} onClick={() => { setSelectedCompany(null); openEdit(selectedCompany) }}>
                <Edit3 size={13} style={{ marginRight: 5 }} /> Edit
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
