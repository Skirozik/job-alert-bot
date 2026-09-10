/**
 * Cross-source sibling identity for status writes. TypeScript is transpiled in
 * memory so the test runs the shipped helpers without adding a test framework.
 *
 * Every fixture below is a real pair from the jobs table. The NULL target_key
 * case is the one that matters most: 13% of rows have none, and treating NULL
 * as a joinable value would make one click resolve thousands of rows.
 *
 * Run: node lib/__tests__/siblings.test.mjs
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

const { SIBLING_CAP, mergeSiblingIds, pgList, siblingIdentities } =
  await loadTs('../siblings.ts')

let pass = 0, fail = 0
const check = (name, condition, detail = '') => {
  if (condition) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}

// ---- siblingIdentities ---------------------------------------------------
{
  const rows = [
    { id: 'gh:bb368df55346c4b0', target_key: null, norm_key: 'stripe|software engineer summer or winter' },
    { id: 'ats:bb368df55346c4b0', target_key: null, norm_key: 'stripe|software engineer summer or winter' },
  ]
  const { targetKeys, normKeys } = siblingIdentities(rows)
  check('a NULL target_key is never collected', targetKeys.length === 0,
        JSON.stringify(targetKeys))
  check('norm_key dedupes across the pair', normKeys.length === 1, JSON.stringify(normKeys))
}

{
  // The Cloudflare case: two titles that read as different jobs, one proven
  // requisition. dupes.ts does not cluster these; target_key does.
  const rows = [
    { id: 'gh:04c21c4f85670c04', target_key: 'greenhouse:cloudflare:5678',
      norm_key: 'cloudflare|software engineer fall 2026 austin tx' },
    { id: 'ats:04c21c4f85670c04', target_key: 'greenhouse:cloudflare:5678',
      norm_key: 'cloudflare|software engineer austin tx' },
  ]
  const { targetKeys, normKeys } = siblingIdentities(rows)
  check('one target_key joins two differing norm_keys', targetKeys.length === 1)
  check('both norm_keys are still offered as join values', normKeys.length === 2)
}

{
  const { targetKeys, normKeys } = siblingIdentities([
    { id: 'a', target_key: '   ', norm_key: '  ' },
    { id: 'b', target_key: undefined, norm_key: 'acme|widget' },
    null,
    undefined,
  ])
  check('whitespace-only keys are dropped', targetKeys.length === 0 && normKeys.length === 1)
  check('a null row does not throw', normKeys[0] === 'acme|widget')
}

check('empty input yields empty identities',
      siblingIdentities([]).targetKeys.length === 0
      && siblingIdentities([]).normKeys.length === 0)

// ---- pgList --------------------------------------------------------------
check('norm_key values are quoted (they hold spaces and a pipe)',
      pgList(['stripe|software engineer summer or winter'])
      === '("stripe|software engineer summer or winter")')
check('multiple values are comma-joined',
      pgList(['a', 'b']) === '("a","b")')
check('an embedded double quote is escaped, not left to end the literal',
      pgList(['say "hi"']) === '("say \\"hi\\"")')
check('a backslash is escaped before the quote pass',
      pgList(['back\\slash']) === '("back\\\\slash")')
check('an empty list is still syntactically valid', pgList([]) === '()')

// ---- mergeSiblingIds -----------------------------------------------------
{
  const r = mergeSiblingIds(['gh:1'], [{ id: 'gh:1' }, { id: 'ats:1' }, { id: '4460442880' }])
  check('seeds come first and are never duplicated', r.ids[0] === 'gh:1')
  check('siblings are appended', r.ids.length === 3, JSON.stringify(r.ids))
  check('added counts only the new rows', r.added === 2)
  check('nothing truncated under the cap', r.truncated === 0)
}

{
  const r = mergeSiblingIds(['a', 'a', 'b'], [{ id: 'b' }, { id: 'c' }])
  check('duplicate seeds collapse', r.ids.join(',') === 'a,b,c', r.ids.join(','))
  check('a sibling already among the seeds is not re-added', r.added === 1)
}

{
  // The clicked row must survive even if its family is somehow enormous.
  const found = Array.from({ length: 10 }, (_, i) => ({ id: `x${i}` }))
  const r = mergeSiblingIds(['seed'], found, 3)
  check('the cap bounds the result', r.ids.length === 3, JSON.stringify(r.ids))
  check('the seed is kept ahead of the cap', r.ids[0] === 'seed')
  check('truncation is counted, not silent', r.truncated === 8, String(r.truncated))
}

{
  const r = mergeSiblingIds(['seed'], [{ id: null }, {}, { id: '' }, { id: 'ok' }])
  check('rows without a usable id are skipped', r.ids.join(',') === 'seed,ok', r.ids.join(','))
}

check('the default cap is well above any real group', SIBLING_CAP >= 50)

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
