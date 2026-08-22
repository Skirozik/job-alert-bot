import type { Status } from '@/types/job'
import type { Grouped } from '@/lib/dupes'

/**
 * A status mutation stays in this ledger after the PATCH succeeds. That is
 * deliberate: a router.refresh() which started before the click can still
 * arrive afterwards with the old status. The mutation is cleared only when a
 * later full server snapshot reports the desired status for every group row.
 */
export type StatusMutation = {
  mutationId: number
  ids: string[]
  desiredStatus: Status
  previousStatus: Status
  phase: 'pending' | 'confirmed'
}

export type StatusLedger = ReadonlyMap<string, StatusMutation>

export function groupMemberIds(job: Grouped): string[] {
  return [job.id, ...(job.duplicates ?? []).map((duplicate) => duplicate.id)]
}

function setWholeGroupStatus(job: Grouped, status: Status): Grouped {
  return {
    ...job,
    status,
    duplicates: job.duplicates?.map((duplicate) => ({ ...duplicate, status })),
  }
}

/** Apply an optimistic change or a targeted rollback to one grouped row. */
export function applyGroupStatus(
  jobs: Grouped[],
  ids: Iterable<string>,
  status: Status,
): Grouped[] {
  const targets = new Set(ids)
  return jobs.map((job) =>
    groupMemberIds(job).some((id) => targets.has(id))
      ? setWholeGroupStatus(job, status)
      : job
  )
}

/**
 * Overlay the newest local mutation on every member of its current duplicate
 * group. Applying it to the whole current group also covers a duplicate which
 * arrived in a LinkedIn delta after the original click.
 */
export function overlayStatusMutations(jobs: Grouped[], ledger: StatusLedger): Grouped[] {
  if (!ledger.size) return jobs

  return jobs.map((job) => {
    let newest: StatusMutation | undefined
    for (const id of groupMemberIds(job)) {
      const mutation = ledger.get(id)
      if (mutation && (!newest || mutation.mutationId > newest.mutationId)) newest = mutation
    }
    return newest ? setWholeGroupStatus(job, newest.desiredStatus) : job
  })
}

function serverStatuses(jobs: Grouped[]): Map<string, Status> {
  const statuses = new Map<string, Status>()
  for (const job of jobs) {
    statuses.set(job.id, job.status ?? 'new')
    for (const duplicate of job.duplicates ?? []) {
      statuses.set(duplicate.id, duplicate.status ?? 'new')
    }
  }
  return statuses
}

/**
 * Reconcile a complete server snapshot with local mutations. Confirmed
 * mutations are acknowledged only after every row reports the desired status;
 * pending mutations and stale snapshots remain overlaid.
 */
export function reconcileServerJobs(
  serverJobs: Grouped[],
  ledger: StatusLedger,
): { jobs: Grouped[], acknowledgedMutationIds: Set<number> } {
  if (!ledger.size) return { jobs: serverJobs, acknowledgedMutationIds: new Set() }

  const statuses = serverStatuses(serverJobs)
  const mutations = new Map<number, StatusMutation>()
  ledger.forEach((mutation) => mutations.set(mutation.mutationId, mutation))

  const acknowledgedMutationIds = new Set<number>()
  mutations.forEach((mutation) => {
    if (
      mutation.phase === 'confirmed' &&
      mutation.ids.every((id) => statuses.get(id) === mutation.desiredStatus)
    ) {
      acknowledgedMutationIds.add(mutation.mutationId)
    }
  })

  const active = new Map<string, StatusMutation>()
  ledger.forEach((mutation, id) => {
    if (!acknowledgedMutationIds.has(mutation.mutationId)) active.set(id, mutation)
  })

  return {
    jobs: overlayStatusMutations(serverJobs, active),
    acknowledgedMutationIds,
  }
}

export function hasPendingMutation(job: Grouped, pendingIds: ReadonlySet<string>): boolean {
  return groupMemberIds(job).some((id) => pendingIds.has(id))
}
