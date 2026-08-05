# Candidate Dashboard – Profile UI Redesign Instructions

Use this file as a complete design spec to update the Candidate Profile section of the hiring platform UI. Follow every section carefully. Do not deviate from the tokens, layout, or component patterns described here.

---

## 1. Design Philosophy

- **Simple, modern, minimal** — clean whitespace, subtle borders, no heavy shadows or gradients
- **Teal brand accent** — use the brand's teal color (`#0ba889`) consistently for active states, icons, and CTAs
- **Warm neutral surfaces** — off-white backgrounds, soft gray borders
- **Typography-first** — hierarchy through font weight and size, not color overload

---

## 2. Design Tokens

Apply these as CSS variables (or your framework's equivalent — Tailwind config, theme tokens, etc.):

```css
:root {
  /* Brand */
  --teal:        #0ba889;
  --teal-light:  #e6f7f4;
  --teal-mid:    #c3ede6;

  /* Sidebar */
  --sidebar-bg:      #111827;
  --sidebar-hover:   #1f2937;
  --sidebar-active:  #1f2937;

  /* Text */
  --text-primary:   #111827;
  --text-secondary: #6b7280;
  --text-muted:     #9ca3af;

  /* Surfaces */
  --bg:     #f5f6f8;
  --card:   #ffffff;
  --border: #e5e7eb;

  /* Status */
  --amber: #f59e0b;
}
```

**Font:** Use `DM Sans` (Google Fonts) — weights 300, 400, 500, 600, 700. Fall back to `Inter` or `system-ui`.

---

## 3. Layout Structure

```
┌─────────────────────────────────────────────────────────┐
│  SIDEBAR (220px fixed)  │  MAIN AREA (flex: 1)          │
│                         │  ┌──────────────────────────┐ │
│  Logo                   │  │ TOPBAR (tabs + search)   │ │
│  Role chip              │  └──────────────────────────┘ │
│  Nav items              │  ┌───────────┬──────────────┐ │
│                         │  │ SECTION   │ PROFILE      │ │
│  ─────────────          │  │ NAV       │ CONTENT      │ │
│  User row + Sign out    │  │ (200px)   │ (flex: 1)    │ │
│                         │  └───────────┴──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

- Full viewport height, no page scroll — internal panels scroll independently
- Sidebar: `width: 220px`, never collapses on desktop
- Section nav: `width: 200px`, scrollable
- Profile content: `flex: 1`, scrollable, `padding: 24px 28px`

---

## 4. Sidebar

### Structure
```
[Logo box] innomax.ai
            Hiring Platform

[ ● CANDIDATE ]   ← role chip

Nav items:
  Dashboard
  Browse Jobs
  My Applications
  My Interviews
  Stage Tracker
  ► Profile        ← active

─────────────────
[S] Sydney Sweeney
    sweeney@gmail.com
    Sign out
```

### Styling Rules
- Background: `#111827`
- Logo box: `40×40px`, white background, `border-radius: 8px`
- **Role chip**: small pill below logo — `background: #1f2937`, teal dot + teal uppercase text `CANDIDATE`
- **Nav items**: `padding: 9px 12px`, `border-radius: 8px`, icon + label side by side
  - Default: color `#9ca3af`
  - Hover: `background: #1f2937`, color `#e5e7eb`
  - Active: `background: #1f2937`, color `white`, icon color `var(--teal)`
- Nav icons: 16×16px stroke icons (Lucide or Heroicons), `stroke-width: 1.8`
- **User row** at bottom: avatar circle with initials (gradient: teal→emerald), name + email in two lines, sign out link below in muted red on hover

---

## 5. Top Navigation Bar

- Height: `52px`, white background, `border-bottom: 1px solid var(--border)`
- **Left**: horizontal tab list — Jobs, Applications, My Interviews, Stage Tracker, Profile
  - Tab padding: `16px 14px`, `font-size: 13.5px`
  - Default: `color: var(--text-secondary)`
  - Active: `color: var(--teal)`, `border-bottom: 2px solid var(--teal)`, `font-weight: 600`
- **Right**: search box + notification bell icon
  - Search box: `background: var(--bg)`, `border: 1px solid var(--border)`, `border-radius: 8px`, `width: 180px`
  - Bell: `34×34px` icon button, with a small teal dot badge (top-right)

---

## 6. Section Navigation Panel

Left of profile content, `width: 200px`, white bg, `border-right: 1px solid var(--border)`.

Sections list (in order):
1. Personal Info
2. Location
3. Professional Summary
4. Work Experience
5. Education
6. Skills
7. Certifications
8. Projects
9. Job Preferences
10. Documents & Links

### Each Section Item
- `padding: 8px 10px`, `border-radius: 8px`
- Left: a **dot indicator** — `18×18px` circle
  - **Incomplete**: `border: 1.5px solid var(--border)`, empty center
  - **Active**: filled teal circle + white inner dot
  - **Completed**: teal border, teal inner dot
- Label: `font-size: 13px`
- States:
  - Default: `color: var(--text-secondary)`
  - Hover: `background: var(--bg)`, `color: var(--text-primary)`
  - Active: `background: var(--teal-light)`, `color: var(--teal)`, `font-weight: 600`

---

## 7. Profile Strength Banner

Replaces the old thin red bar. Use a prominent banner card at the top of the profile content area.

```
┌─────────────────────────────────────────────────────────┐
│  [Ring]  Profile Strength   [Beginner badge]            │
│   5%     ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  5 / 100  │
│          Complete your profile to get better matches.   │
└─────────────────────────────────────────────────────────┘
```

- **Ring indicator** (left): `52×52px` SVG circle ring
  - Track: `stroke: #f0fdf9`, `stroke-width: 5`
  - Fill: `stroke: var(--teal)`, `stroke-linecap: round`, animated via `stroke-dashoffset`
  - Center label: percentage in teal, `font-size: 11px`, `font-weight: 700`
- **Level badge**: pill next to label — "Beginner" in amber (`#f59e0b`), `background: #fff7ed`, `border: 1px solid #fed7aa`
  - Change to green "Intermediate" at 40%, blue "Advanced" at 75%, purple "Expert" at 95%
- **Progress bar**: `height: 6px`, `border-radius: 99px`, teal→emerald gradient fill
- **Score**: right-aligned `5 / 100` in teal, `font-weight: 700`
- Card styling: `background: white`, `border: 1px solid var(--border)`, `border-radius: 14px`, `padding: 16px 20px`

---

## 8. Personal Information Card

### Card Shell
- `background: white`, `border: 1px solid var(--border)`, `border-radius: 14px`
- No drop shadow — border only

### Card Header
- Left: icon (person/user outline, 16px, teal) + bold title "Personal Information"
- Right: **Edit button**
  - `background: var(--bg)`, `border: 1px solid var(--border)`, `border-radius: 8px`
  - `padding: 6px 13px`, `font-size: 12.5px`
  - Hover: `background: var(--teal-light)`, `border-color: var(--teal-mid)`, `color: var(--teal)`
  - Includes a small pencil/edit icon

### Profile Photo Row
Sits below the card header, `border-bottom: 1px solid var(--border)`.

```
[Avatar]  Sydney Sweeney
          sweeney@gmail.com
          [Upload Photo ↑]  JPG or PNG, max 2MB
```

- **Avatar**: `72×72px` circle, teal gradient background, initials in white
  - Ring: `box-shadow: 0 0 0 2px var(--teal-mid)`, `border: 3px solid white`
- **Name**: `font-size: 17px`, `font-weight: 700`
- **Email**: `font-size: 13px`, `color: var(--text-secondary)`
- **Upload button**: teal filled button with upload icon + "Upload Photo"
  - Hint text beside it: "JPG or PNG, max 2MB" in muted color

### Info Fields Grid
Below the photo row. `display: grid; grid-template-columns: 1fr 1fr`.

Fields (in order, left to right, top to bottom):
| Field | Value |
|---|---|
| First Name | Sydney |
| Last Name | Sweeney |
| Email Address | sweeney@gmail.com |
| Gender | Female |
| Date of Birth | September 12, 1997 |
| Phone Number | *(empty state)* |

- Each field: `padding: 16px 20px`
- Separated by: `border-right: 1px solid var(--border)` (left column), `border-bottom: 1px solid var(--border)` (all except last row)
- **Label**: `font-size: 11px`, `font-weight: 500`, `color: var(--text-muted)`, uppercase, `letter-spacing: 0.4px`
- **Value**: `font-size: 14px`, `font-weight: 500`, `color: var(--text-primary)`
- **Empty state**: italic, `color: var(--text-muted)`, text: "Not provided"

---

## 9. Interaction States to Implement

| Element | Interaction | Result |
|---|---|---|
| Section nav items | Click | Highlight active, scroll to that section's card |
| Edit button | Click | Toggle fields into editable inputs |
| Upload Photo | Click | Open file picker |
| Nav items (sidebar) | Click | Navigate to that page |
| Profile strength | On mount | Animate ring + bar from 0 to actual value |
| Tab bar | Click | Switch active tab, load relevant content |

---

## 10. Spacing Reference

| Token | Value |
|---|---|
| Card border radius | `14px` |
| Button border radius | `8px` |
| Pill/badge border radius | `20px` (full) |
| Card padding | `16px 20px` |
| Content area padding | `24px 28px` |
| Gap between cards | `18px` |
| Nav item padding | `9px 12px` |
| Section nav item padding | `8px 10px` |

---

## 11. Icon Library

Use **Lucide Icons** or **Heroicons** (outline style, `stroke-width: 1.8`). Map:

| Section | Icon |
|---|---|
| Dashboard | `grid-2x2` / `layout-grid` |
| Browse Jobs | `briefcase` |
| My Applications | `file-text` |
| My Interviews | `calendar` |
| Stage Tracker | `clock` |
| Profile | `user` |
| Personal Info | `user` |
| Location | `map-pin` |
| Work Experience | `building-2` |
| Education | `graduation-cap` |
| Skills | `star` |
| Certifications | `award` |
| Projects | `folder` |
| Job Preferences | `settings-2` |
| Documents | `paperclip` |
| Edit | `pencil` |
| Upload | `upload` |
| Search | `search` |
| Notifications | `bell` |
| Sign out | `log-out` |

---

## 12. What NOT to Do

- ❌ Do not use heavy box shadows (no `box-shadow: 0 10px 40px rgba(...)`)
- ❌ Do not use gradient backgrounds on cards or the sidebar
- ❌ Do not use rounded corners above `14px` for cards
- ❌ Do not use more than 2 accent colors (teal + amber for badges only)
- ❌ Do not add emoji anywhere in the UI
- ❌ Do not use `Arial`, `Roboto`, or `Inter` — use `DM Sans`
- ❌ Do not use colored left-border accent cards
- ❌ Do not show placeholder text on fields that have real data

---

## 13. Reference Preview File

A static HTML preview of this design is available at `preview.html` in the project root. Open it in a browser to see the exact visual target before implementing.

---

*Design spec authored for innomax.ai Hiring Platform — Candidate Dashboard v2*
