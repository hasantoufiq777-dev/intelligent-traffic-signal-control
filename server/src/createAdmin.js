// Create (or promote) an administrator account from the command line:
//   npm run create-admin -- admin@example.org "Full Name"
// The password is read from the ADMIN_PASSWORD environment variable or asked for on the terminal
// (it is never passed as an argument, so it does not end up in the shell history).
import readline from 'node:readline';
import bcrypt from 'bcryptjs';
import { z } from 'zod';
import { loadConfig } from './config.js';
import { openDatabase } from './db.js';

const [email, name = 'Administrator'] = process.argv.slice(2);
const parsedEmail = z.string().trim().toLowerCase().email().safeParse(email ?? '');
if (!parsedEmail.success) {
  console.error('Usage: npm run create-admin -- <email> ["Full Name"]');
  process.exit(2);
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer);
    });
    rl._writeToOutput = (text) => { if (text.includes(question)) rl.output.write(question); }; // do not echo the password
  });
}

const cfg = loadConfig();
const password = process.env.ADMIN_PASSWORD || await ask('Password (at least 10 characters): ');
if (password.length < 10 || Buffer.byteLength(password, 'utf8') > 72) {
  console.error('\nThe password must be 10 to 72 characters long.');
  process.exit(2);
}

const db = openDatabase(cfg.DB_PATH);
try {
  const hash = await bcrypt.hash(password, cfg.BCRYPT_ROUNDS);
  const existing = db.getUserByEmail(parsedEmail.data);
  if (existing) {
    db.updateUser(existing.id, { role: 'ADMIN', status: 'active', password_hash: hash, failed_logins: 0, locked_until: null });
    db.deleteUserSessions(existing.id);
    db.audit({ action: 'user_update', target: parsedEmail.data, detail: 'made administrator from the command line' });
    console.log(`\n${parsedEmail.data} is now an active administrator (password replaced).`);
  } else {
    db.createUser({ email: parsedEmail.data, name: name.slice(0, 80), password_hash: hash, role: 'ADMIN' });
    db.audit({ action: 'user_create', target: parsedEmail.data, detail: 'administrator created from the command line' });
    console.log(`\nAdministrator ${parsedEmail.data} created.`);
  }
} finally {
  db.close();
}
