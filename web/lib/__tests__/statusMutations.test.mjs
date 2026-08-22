/**
 * Status mutation race regressions. TypeScript is transpiled in memory so the
 * test runs the shipped helpers without adding a test framework.
 *
 * Run: node lib/__tests__/statusMutations.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import ts from 'typescript'

const here = dirname(fileURLToPath(import.meta.url))
const loadTs = async (path) => {
  const src = readFileSync(join(here, path), 'utf8')
  const compiled = ts.transpileModule(src, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
}

const {
  applyGroupStatus,
  groupMemberIds,
  hasPendingMutation,
  overlayStatusMutations,
  reconcileServerJobs,
} = await loadTs('../statusMutations.ts')
const { matchesView } = await loadTs('../jobView.ts')

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

const job = (id, status = 'new', overrides = {}) => ({
  id,
  title: `Software Engineer Intern ${id}`,
  company: 'Example',
  location: 'New York, NY',
  url: `https://example.com/${id}`,
  tier: 'APPLY',
  status,
  found_at: '2026-08-22T00:00:00Z',
  ...overrides,
})

const grouped = (status = 'new') => ({
  ...job('leader', status),
  duplicates: [job('duplicate', status)],
})

const mutation = (phase = 'pending', desiredStatus = 'applied', mutationId = 1) => ({
  mutationId,
  ids: ['leader', 'duplicate'],
  desiredStatus,
  previousStatus: 'new',
  phase,
})

console.log('-- refresh reconciliation --')
const pending = mutation('pending')
const pendingLedger = new Map(pending.ids.map((id) => [id, pending]))
const staleWhilePending = reconcileServerJobs([grouped('new')], pendingLedger)
check('a stale full refresh cannot undo a pending Applied click',
  staleWhilePending.jobs[0].status === 'applied')
check('pending mutations are never acknowledged early',
  staleWhilePending.acknowledgedMutationIds.size === 0)

const confirmed = mutation('confirmed')
const confirmedLedger = new Map(confirmed.ids.map((id) => [id, confirmed]))
const staleAfterSuccess = reconcileServerJobs([grouped('new')], confirmedLedger)
check('a stale full refresh cannot undo a confirmed Applied click',
  staleAfterSuccess.jobs[0].status === 'applied')
check('stale server data keeps the confirmed mutation in the ledger',
  staleAfterSuccess.acknowledgedMutationIds.size === 0)

const freshAfterSuccess = reconcileServerJobs([grouped('applied')], confirmedLedger)
check('a fresh matching snapshot acknowledges the mutation',
  freshAfterSuccess.acknowledgedMutationIds.has(confirmed.mutationId))

console.log('-- duplicate groups and sequencing --')
const optimistic = applyGroupStatus([grouped('new')], confirmed.ids, 'dismissed')
check('an optimistic action updates the leader and every duplicate',
  optimistic[0].status === 'dismissed' && optimistic[0].duplicates[0].status === 'dismissed')

const lateDuplicate = {
  ...job('leader', 'new'),
  duplicates: [job('duplicate', 'new'), job('arrived-later', 'new')],
}
const withLateDuplicate = overlayStatusMutations([lateDuplicate], confirmedLedger)
check('a duplicate arriving after the click inherits the local status',
  groupMemberIds(withLateDuplicate[0]).length === 3 &&
  withLateDuplicate[0].duplicates.every((row) => row.status === 'applied'))

const oldMutation = mutation('confirmed', 'saved', 2)
const newMutation = { ...mutation('pending', 'dismissed', 3), ids: ['duplicate'] }
const newestWins = overlayStatusMutations(
  [grouped('new')],
  new Map([['leader', oldMutation], ['duplicate', newMutation]]),
)
check('the newest mutation wins when responses are out of order',
  newestWins[0].status === 'dismissed')

const twoGroups = [grouped('applied'), job('unrelated', 'saved')]
const targetedRollback = applyGroupStatus(twoGroups, ['leader', 'duplicate'], 'new')
check('rollback touches only the failed group',
  targetedRollback[0].status === 'new' && targetedRollback[1].status === 'saved')
check('pending detection covers duplicate member ids',
  hasPendingMutation(grouped('new'), new Set(['duplicate'])))

console.log('-- mutually exclusive views --')
const saved = job('saved', 'saved')
check('Saved leaves To Apply', !matchesView(saved, 'to-apply'))
check('Saved leaves My list', !matchesView(saved, 'my-list'))
check('Saved appears in Saved', matchesView(saved, 'saved'))
check('Applied cannot appear in To Apply', !matchesView(job('applied', 'applied'), 'to-apply'))
check('Dismissed cannot appear in To Apply', !matchesView(job('dismissed', 'dismissed'), 'to-apply'))

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
