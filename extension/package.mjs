// Zips dist/ as chat-scraper-<version>.zip for handing to the team.
import { execFileSync } from 'node:child_process'
import { readFileSync, rmSync } from 'node:fs'

const version = JSON.parse(readFileSync('manifest.json', 'utf8')).version
const zip = `chat-scraper-${version}.zip`
rmSync(zip, { force: true })
execFileSync('zip', ['-qr', zip, '.'], { cwd: 'dist' , stdio: 'inherit' })
execFileSync('mv', [`dist/${zip}`, zip])
console.log(`wrote ${zip}`)
