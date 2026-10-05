import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import TimelineTab from '../components/tabs/TimelineTab.jsx'
import investigationFixture from './fixtures/investigation.json'

// Regression test for a real bug found via a live browser run of the demo
// investigation: `probable_origin` shares its `t_offset_hours` with the
// *earliest* point in `backward_track` (both represent the same -24h
// moment, just with a different label) - see the fixture data itself,
// which reproduces this exactly. The node list used to be built as
// `[...backward_track, origin, observed, ...forward_track]` and only
// deduped the repeated backward_track entry without ever reordering, so
// "PROBABLE ORIGIN" stayed in its original array position - right after
// the *later* remaining backward-track points - even though its own
// timestamp is chronologically the earliest of the whole timeline. A
// left-to-right timeline that prints a timestamp on every node has to
// actually be sorted by that timestamp.
describe('TimelineTab chronological ordering', () => {
  it('renders "PROBABLE ORIGIN" before every T-Nh backward-track stage, not after it', () => {
    render(<TimelineTab investigation={investigationFixture} onSelectVessel={() => {}} />)

    const labels = screen.getAllByText(/PROBABLE ORIGIN|^T-|^T\+|SATELLITE DETECTION/)
      .map(el => el.textContent)

    const originIdx = labels.findIndex(l => l === 'PROBABLE ORIGIN')
    const detectionIdx = labels.findIndex(l => l === 'SATELLITE DETECTION')

    expect(originIdx).toBeGreaterThanOrEqual(0)
    expect(detectionIdx).toBeGreaterThan(originIdx)
    // Every backward-track ("T-Nh") stage must come strictly after the
    // origin and strictly before the satellite detection node.
    labels.forEach((label, i) => {
      if (/^T-\d/.test(label)) {
        expect(i).toBeGreaterThan(originIdx)
        expect(i).toBeLessThan(detectionIdx)
      }
    })

    // And the node list as a whole must be non-decreasing in time offset -
    // the direct, data-level statement of "the timeline is chronological".
    const timeNodes = screen.getAllByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/).map(el => el.textContent)
    const parsed = timeNodes.map(t => new Date(t.replace(' ', 'T')).getTime())
    for (let i = 1; i < parsed.length; i++) {
      expect(parsed[i]).toBeGreaterThanOrEqual(parsed[i - 1])
    }
  })
})
