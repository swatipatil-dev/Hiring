import { useMemo } from 'react'
import NASSCOM_COURSES from '../../../data/nasscom_courses'

export default function useCourseRecommendations({ profile, jobs, atsMissingSkills }) {
  return useMemo(() => {
    const profileSkills = profile?.skills || []

    // Candidate's known skills (lowercase)
    const candidateSkills = new Set(
      profileSkills.map(s => (s?.skill_name || s || '').toLowerCase()).filter(Boolean)
    )

    // Weak skills — candidate has them but at Beginner level
    const weakSkills = new Set(
      profileSkills
        .filter(s => (s?.proficiency_level || '').toLowerCase() === 'beginner')
        .map(s => (s?.skill_name || '').toLowerCase())
        .filter(Boolean)
    )

    // Gap skills — skills required by browsable jobs that candidate doesn't have
    const allJobSkills = (jobs || []).flatMap(j => (j.skills || []).map(s => s.toLowerCase()))
    const gapSkills = new Set(allJobSkills.filter(s => !candidateSkills.has(s)))

    // ATS missing skills — Map<skill_lowercase, rejection_count>
    const atsMap = atsMissingSkills || new Map()

    const scored = NASSCOM_COURSES.map(course => {
      let score = 0
      const matchedGapSkills = []
      const matchedAtsSkills = []

      course.skills.forEach(skill => {
        const s = skill.toLowerCase()
        const atsCount = atsMap.get(s) || 0

        if (atsCount > 0) {
          // +5 per ATS-flagged skill, +1 bonus for each additional rejection beyond the first
          score += 5 + (atsCount - 1)
          matchedAtsSkills.push({ skill, count: atsCount })
        }

        if (gapSkills.has(s)) {
          score += 3
          matchedGapSkills.push(skill)
        } else if (weakSkills.has(s)) {
          score += 1
        } else if (candidateSkills.has(s)) {
          score += 0.5
        }
      })

      return { ...course, score, matchedGapSkills, matchedAtsSkills }
    })

    const recommended = [...scored]
      .sort((a, b) => b.score - a.score || a.title.localeCompare(b.title))

    return { recommended, allCourses: NASSCOM_COURSES, gapSkills, atsMap }
  }, [profile, jobs, atsMissingSkills])
}
