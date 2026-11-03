// One-time setup, Windows and Linux: Python venv + requirements, npm installs, server/.env.
//   node scripts/setup.mjs            (or: npm run setup)
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const win = process.platform === 'win32';
const venvPython = path.join(root, '.venv', win ? 'Scripts' : 'bin', win ? 'python.exe' : 'python');

function run(cmd, args, cwd = root) {
  console.log(`\n> ${cmd} ${args.join(' ')}   (in ${path.relative(root, cwd) || '.'})`);
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit', shell: win && cmd === 'npm' });
  if (r.status !== 0) {
    console.error(`\nSetup failed at: ${cmd} ${args.join(' ')}`);
    process.exit(r.status || 1);
  }
}

// 1. Python virtual environment
if (!fs.existsSync(venvPython)) {
  const sysPython = process.env.PYTHON || (win ? 'python' : 'python3');
  run(sysPython, ['-m', 'venv', '.venv']);
}
run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
run(venvPython, ['-m', 'pip', 'install', '-r', path.join('sim', 'requirements.txt')]);

// 2. Node packages
run('npm', ['install'], root);
run('npm', ['install'], path.join(root, 'server'));
run('npm', ['install'], path.join(root, 'client'));

// 3. server/.env (kept if it already exists)
const envPath = path.join(root, 'server', '.env');
if (!fs.existsSync(envPath)) {
  let env = fs.readFileSync(path.join(root, 'server', '.env.example'), 'utf8');
  const rel = win ? '../.venv/Scripts/python.exe' : '../.venv/bin/python';
  env = env.replace(/^PYTHON_BIN=.*$/m, `PYTHON_BIN=${rel}`);
  env = env.replace(/^ADMIN_TOKEN=.*$/m, `ADMIN_TOKEN=${crypto.randomBytes(12).toString('hex')}`);
  fs.writeFileSync(envPath, env);
  console.log(`\nWrote server/.env (PYTHON_BIN=${rel}, random ADMIN_TOKEN). Edit it to change settings.`);
} else {
  console.log('\nserver/.env exists, left unchanged.');
}

// 4. Checks
if (!process.env.SUMO_HOME) {
  console.warn('\nWARNING: SUMO_HOME is not set. Install SUMO 1.18+ and set SUMO_HOME (see README).');
}
console.log('\nSetup complete. Next: npm run dev:mock (no SUMO needed) or npm run dev (real simulation).');
