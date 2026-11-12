// Back up the gateway database while it is in use:
//   npm run backup -- backups/gateway-2026-11-12.db
// Restore: stop the gateway, copy the backup file over DB_PATH (remove the old -wal and -shm files), start it again.
import fs from 'node:fs';
import path from 'node:path';
import { loadConfig } from './config.js';
import { openDatabase } from './db.js';

const dest = process.argv[2];
if (!dest) {
  console.error('Usage: npm run backup -- <destination file>');
  process.exit(2);
}
const target = path.resolve(dest);
if (fs.existsSync(target)) {
  console.error(`${target} already exists; choose a new file name.`);
  process.exit(2);
}
fs.mkdirSync(path.dirname(target), { recursive: true });
const db = openDatabase(loadConfig().DB_PATH);
try {
  await db.backup(target);
  console.log(`Backup written to ${target} (${(fs.statSync(target).size / 1e6).toFixed(1)} MB)`);
} finally {
  db.close();
}
