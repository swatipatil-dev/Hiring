/**
 * Curated college list for the Talent Pool college filter.
 *
 * Plain module, no fetch — same shape as `nasscom_courses.js`. Groups are ordered
 * the way HR thinks about campus hiring: intent first, then the IIIT network, then
 * regions.
 *
 * Each college carries `terms`, the substrings actually sent to the API. The pool
 * stores whatever the résumé parser scraped (`resume_profile_parser.py:200`), which
 * is rarely the full formal name — a résumé says "IIT Indore" as often as "Indian
 * Institute of Technology Indore" — so a college may carry several spellings and
 * the server ORs them.
 *
 * Two rules when adding a college:
 *
 *  1. NO COMMAS in a term. The filter is comma-joined into a single query param, so
 *     a comma inside a term would split it into two broken terms. This is also why
 *     terms drop the comma from names like "Institute of Engineering and Technology,
 *     Lucknow" rather than reproducing it.
 *  2. NO SHORT OR GENERIC TERMS. Matching is a plain `%term%`, so "nit" would hit
 *     "Uni**nit**ed" and "iit" would hit every IIT. Keep a term distinctive enough
 *     that it can only mean this college — acronym plus city, usually.
 *
 * Matching is substring-only and does not normalise punctuation, so where a
 * hyphenated form is common ("IIIT-Delhi") it is listed as its own term.
 */

export const COLLEGE_GROUPS = [
  {
    id: 'ai-it-hiring',
    label: 'Best for AI/IT campus hiring',
    colleges: [
      { name: 'IIT Indore', terms: ['iit indore', 'indian institute of technology indore'] },
      { name: 'MNIT Jaipur', terms: ['mnit', 'malaviya national institute'] },
      { name: 'NIT Patna', terms: ['nit patna', 'national institute of technology patna'] },
      { name: 'VNIT Nagpur', terms: ['vnit', 'visvesvaraya national institute'] },
      { name: 'PSG College of Technology', terms: ['psg college'] },
      { name: 'SRM Institute of Science and Technology', terms: ['srm institute', 'srm university'] },
      { name: 'Institute of Engineering and Technology (IET), Lucknow', terms: ['iet lucknow', 'institute of engineering and technology'] },
    ],
  },
  {
    id: 'iiit',
    label: 'IIIT colleges across India',
    colleges: [
      { name: 'ABV-IIITM Gwalior', terms: ['iiitm gwalior', 'abv-iiitm', 'atal bihari vajpayee indian institute'] },
      { name: 'IIIT Allahabad', terms: ['iiit allahabad', 'iiit-allahabad'] },
      { name: 'IIITDM Jabalpur', terms: ['iiitdm jabalpur', 'iiitdm-jabalpur'] },
      { name: 'IIITDM Kancheepuram', terms: ['iiitdm kancheepuram', 'iiitdm-kancheepuram'] },
      { name: 'IIITDM Kurnool', terms: ['iiitdm kurnool', 'iiitdm-kurnool'] },
      { name: 'IIIT Sri City', terms: ['iiit sri city', 'iiit-sri city'] },
      { name: 'IIIT Guwahati', terms: ['iiit guwahati', 'iiit-guwahati'] },
      { name: 'IIIT Vadodara', terms: ['iiit vadodara', 'iiit-vadodara'] },
      { name: 'IIIT Kota', terms: ['iiit kota', 'iiit-kota'] },
      { name: 'IIIT Tiruchirappalli', terms: ['iiit tiruchirappalli', 'iiit-tiruchirappalli', 'iiit trichy'] },
      { name: 'IIIT Dharwad', terms: ['iiit dharwad', 'iiit-dharwad'] },
      { name: 'IIIT Una', terms: ['iiit una', 'iiit-una'] },
      { name: 'IIIT Sonipat', terms: ['iiit sonipat', 'iiit-sonipat'] },
      { name: 'IIIT Kalyani', terms: ['iiit kalyani', 'iiit-kalyani'] },
      { name: 'IIIT Lucknow', terms: ['iiit lucknow', 'iiit-lucknow'] },
      { name: 'IIIT Kottayam', terms: ['iiit kottayam', 'iiit-kottayam'] },
      { name: 'IIIT Manipur', terms: ['iiit manipur', 'iiit-manipur'] },
      { name: 'IIIT Nagpur', terms: ['iiit nagpur', 'iiit-nagpur'] },
      { name: 'IIIT Pune', terms: ['iiit pune', 'iiit-pune'] },
      { name: 'IIIT Ranchi', terms: ['iiit ranchi', 'iiit-ranchi'] },
      { name: 'IIIT Surat', terms: ['iiit surat', 'iiit-surat'] },
      { name: 'IIIT Bhopal', terms: ['iiit bhopal', 'iiit-bhopal'] },
      { name: 'IIIT Bhagalpur', terms: ['iiit bhagalpur', 'iiit-bhagalpur'] },
      { name: 'IIIT Agartala', terms: ['iiit agartala', 'iiit-agartala'] },
      { name: 'IIIT Raichur', terms: ['iiit raichur', 'iiit-raichur'] },
      { name: 'IIIT Hyderabad', terms: ['iiit hyderabad', 'iiit-hyderabad'] },
      { name: 'IIIT Bangalore', terms: ['iiit bangalore', 'iiit-bangalore', 'iiit bengaluru'] },
      { name: 'IIIT Delhi', terms: ['iiit delhi', 'iiit-delhi'] },
      { name: 'IIIT Bhubaneswar', terms: ['iiit bhubaneswar', 'iiit-bhubaneswar'] },
      { name: 'IIIT Naya Raipur', terms: ['iiit naya raipur', 'iiit-naya raipur'] },
    ],
  },
  {
    id: 'lucknow',
    label: 'Lucknow region',
    colleges: [
      { name: 'Institute of Engineering and Technology (IET), Lucknow', terms: ['iet lucknow', 'institute of engineering and technology'] },
      { name: 'Faculty of Engineering & Technology, University of Lucknow', terms: ['university of lucknow', 'lucknow university'] },
      { name: 'Babu Banarasi Das University', terms: ['babu banarasi das'] },
      { name: 'GCRG Group of Institutions', terms: ['gcrg'] },
      { name: 'R.R. Group of Institutions', terms: ['r.r. group of institutions', 'rr group of institutions'] },
      { name: 'Institute of Technology and Management – ITM Lucknow', terms: ['itm lucknow', 'institute of technology and management'] },
      { name: 'Rameshwaram Institute of Technology & Management', terms: ['rameshwaram institute'] },
    ],
  },
  {
    id: 'patna',
    label: 'Patna region (~100 km)',
    colleges: [
      { name: 'NIT Patna', terms: ['nit patna', 'national institute of technology patna'] },
      { name: 'Netaji Subhas Institute of Technology (NSIT)', terms: ['netaji subhas institute'] },
      { name: 'Bihar Engineering University', terms: ['bihar engineering university'] },
      { name: 'Bakhtiyarpur College of Engineering', terms: ['bakhtiyarpur'] },
      { name: 'Government Engineering College Vaishali', terms: ['engineering college vaishali'] },
    ],
  },
  {
    id: 'jaipur',
    label: 'Jaipur region (~100 km)',
    colleges: [
      { name: 'Malaviya National Institute of Technology Jaipur', terms: ['mnit', 'malaviya national institute'] },
      { name: 'JECRC University', terms: ['jecrc university'] },
      { name: 'JECRC Foundation', terms: ['jecrc foundation'] },
      { name: 'Poornima College of Engineering', terms: ['poornima college'] },
      { name: 'Manipal University Jaipur', terms: ['manipal university jaipur'] },
      { name: 'Arya College of Engineering & IT', terms: ['arya college'] },
    ],
  },
  {
    id: 'indore',
    label: 'Indore region (~100 km)',
    colleges: [
      { name: 'Indian Institute of Technology Indore', terms: ['iit indore', 'indian institute of technology indore'] },
      { name: 'Institute of Engineering and Technology DAVV', terms: ['iet davv', 'institute of engineering and technology davv'] },
      { name: 'Medi-Caps University', terms: ['medi-caps', 'medicaps'] },
      { name: 'Acropolis Institute of Technology and Research', terms: ['acropolis institute'] },
      { name: 'Indore Institute of Science and Technology', terms: ['indore institute of science'] },
    ],
  },
  {
    id: 'nagpur',
    label: 'Nagpur region (~100 km)',
    colleges: [
      { name: 'Visvesvaraya National Institute of Technology', terms: ['vnit', 'visvesvaraya national institute'] },
      { name: 'Yashwantrao Chavan College of Engineering', terms: ['yashwantrao chavan college'] },
      { name: 'G. H. Raisoni College of Engineering', terms: ['raisoni college'] },
      { name: 'Priyadarshini College of Engineering', terms: ['priyadarshini college'] },
      { name: 'Laxminarayan Innovation Technological University', terms: ['laxminarayan'] },
    ],
  },
  {
    id: 'chengalpattu',
    label: 'Chengalpattu region (~50 km)',
    colleges: [
      { name: 'SRM Valliammai Engineering College', terms: ['srm valliammai', 'valliammai engineering'] },
      { name: 'SRM Institute of Science and Technology', terms: ['srm institute', 'srm university'] },
      { name: 'Mahindra University School of Engineering', terms: ['mahindra university'] },
      { name: 'ARM College of Engineering and Technology', terms: ['arm college of engineering'] },
      { name: 'MIT Campus, Anna University', terms: ['madras institute of technology', 'mit campus anna university'] },
    ],
  },
  {
    id: 'coimbatore',
    label: 'Coimbatore region (~50 km)',
    colleges: [
      { name: 'PSG College of Technology', terms: ['psg college'] },
      { name: 'Coimbatore Institute of Technology', terms: ['coimbatore institute of technology'] },
      { name: 'Government College of Technology', terms: ['government college of technology'] },
      { name: 'Kumaraguru College of Technology', terms: ['kumaraguru'] },
      { name: 'Sri Krishna College of Engineering and Technology', terms: ['sri krishna college'] },
      { name: 'Sri Eshwar College of Engineering', terms: ['sri eshwar'] },
      { name: 'KPR Institute of Engineering and Technology', terms: ['kpr institute'] },
    ],
  },
]

/**
 * Every college once, keyed by its term signature.
 *
 * Seven colleges deliberately appear in two groups (PSG and SRM, NIT Patna, MNIT,
 * VNIT, IIT Indore, IET Lucknow) — a college belongs to its region AND to the
 * hiring shortlist. The groups keep their duplicates so each reads as a complete
 * list; selection is keyed on the term signature instead, so ticking PSG under
 * "Coimbatore region" also ticks it under "Best for AI/IT campus hiring".
 */
export const collegeKey = (c) => c.terms.join('|')

export const ALL_COLLEGES = (() => {
  const seen = new Map()
  for (const g of COLLEGE_GROUPS) {
    for (const c of g.colleges) {
      if (!seen.has(collegeKey(c))) seen.set(collegeKey(c), c)
    }
  }
  return [...seen.values()]
})()
