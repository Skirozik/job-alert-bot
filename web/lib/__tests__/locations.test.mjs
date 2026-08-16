/**
 * splitLocations regression suite.
 *
 * Two separate bugs shipped from this function: concatenated locations
 * rendering as a single entry, and camelCase cities (McLean, DeKalb,
 * LaGrange) being split in half. Neither is visible unless you inspect a
 * specific row, so both are pinned here.
 *
 * The suite executes the REAL function out of lib/jobView.ts. A test against a
 * copied implementation would pass while the shipped one was broken, which is
 * precisely the failure being guarded against.
 *
 * Run:  node lib/__tests__/locations.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '../jobView.ts'), 'utf8')
const lines = src.split(String.fromCharCode(10)).map(l => l.charCodeAt(l.length - 1) === 13 ? l.slice(0, -1) : l)   // source is CRLF

const start = lines.findIndex(l => l.startsWith('export function splitLocations'))
const end = lines.findIndex((l, i) => i > start && l === '}')
if (start < 0 || end < 0) { console.error('  could not locate splitLocations in jobView.ts'); process.exit(1) }

const fnSrc = lines.slice(start, end + 1).join('\n')
  .replace('export function', 'function')
  .replace(/: string \| null/g, '')
  .replace(/: string\[\]/g, '')
const prefixSrc = lines.find(l => l.startsWith('const CAMEL_CITY_PREFIX')) || ''
const splitLocations = new Function(prefixSrc + '\n' + fnSrc + '\nreturn splitLocations')()

let pass = 0, fail = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  ok ? pass++ : fail++
  console.log(ok ? `  PASS  ${name}`
                 : `  FAIL  ${name}\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`)
}

console.log('-- concatenated multi-location strings --')
eq('state-code seam', splitLocations('Milwaukee, WIGreen Bay, WI'), ['Milwaukee, WI', 'Green Bay, WI'])
eq('three-way seam', splitLocations('Austin, TXFort Mill, SCCharlotte, NC'), ['Austin, TX', 'Fort Mill, SC', 'Charlotte, NC'])
eq('two-way seam', splitLocations('Pensacola, FLVienna, VA'), ['Pensacola, FL', 'Vienna, VA'])
eq('country seam', splitLocations('London, UKParis, France'), ['London, UK', 'Paris, France'])
eq('lowercase seam', splitLocations('Remote in IndiaRemote in USA'), ['Remote in India', 'Remote in USA'])
eq('state then bare state', splitLocations('St. Louis, MOIllinois'), ['St. Louis, MO', 'Illinois'])
eq('count prefix + list', splitLocations('4 locationsSparks, MDHartford, CTAtlanta, GASt Paul, MN'),
   ['Sparks, MD', 'Hartford, CT', 'Atlanta, GA', 'St Paul, MN'])
eq('long count prefix + list',
   splitLocations('5 locationsWaukegan, ILMilwaukee, WIGlenview, ILPleasant Prairie, WIKenosha, WI'),
   ['Waukegan, IL', 'Milwaukee, WI', 'Glenview, IL', 'Pleasant Prairie, WI', 'Kenosha, WI'])

console.log('-- camelCase cities must NOT be split --')
eq('McLean', splitLocations('McLean, VA'), ['McLean, VA'])
eq('DeKalb', splitLocations('DeKalb, IL'), ['DeKalb, IL'])
eq('LaGrange', splitLocations('LaGrange, GA'), ['LaGrange, GA'])
eq('camelCase after a seam', splitLocations('San Jose, CAMcLean, VA'), ['San Jose, CA', 'McLean, VA'])
eq('camelCase mid-list', splitLocations('Plano, TXMcLean, VARichmond, VA'), ['Plano, TX', 'McLean, VA', 'Richmond, VA'])

console.log('-- must remain a single entry --')
eq('plain city', splitLocations('Atlanta, GA'), ['Atlanta, GA'])
eq('city + country', splitLocations('New Jersey, United States'), ['New Jersey, United States'])
eq('bare count, no list stored', splitLocations('21 Locations'), ['21 Locations'])
eq('lone capital mid-token', splitLocations('Flexible - Any SpaceX Site'), ['Flexible - Any SpaceX Site'])
eq('trailing capitals', splitLocations('JPN TOKY 1-3-1 FLR12 BldgJA'), ['JPN TOKY 1-3-1 FLR12 BldgJA'])
eq('literal pipes are hierarchy, not a list', splitLocations('US | California | San Francisco'),
   ['US | California | San Francisco'])
eq('empty', splitLocations(''), [])
eq('null', splitLocations(null), [])

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
