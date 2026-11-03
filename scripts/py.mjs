// Run the project's Python (the .venv interpreter when it exists) with the given arguments.
//   node scripts/py.mjs -m pytest -q
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const win = process.platform === 'win32';
const venv = path.join(root, '.venv', win ? 'Scripts' : 'bin', win ? 'python.exe' : 'python');
const python = fs.existsSync(venv) ? venv : (process.env.PYTHON || (win ? 'python' : 'python3'));
const r = spawnSync(python, process.argv.slice(2), { cwd: root, stdio: 'inherit' });
process.exit(r.status ?? 1);
