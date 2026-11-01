// One Python simulation child process: spawn (no shell), read stdout line by line, stop cleanly.
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import readline from 'node:readline';
import { parseLine } from './protocol.js';

/**
 * Events: 'message' (parsed protocol object), 'bad_line' (raw text), 'stderr' (line),
 * 'exit' ({ code, signal, stopped }).
 */
export class SimProcess extends EventEmitter {
  constructor({ pythonBin, args, cwd, env, logger }) {
    super();
    this.logger = logger;
    this.stopped = false;
    this.exited = false;
    this.child = spawn(pythonBin, args, {
      cwd,
      env: { ...process.env, ...env, PYTHONUNBUFFERED: '1' }, // SUMO_HOME passes through
      shell: false,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.pid = this.child.pid;

    readline.createInterface({ input: this.child.stdout }).on('line', (line) => {
      const msg = parseLine(line);
      if (msg) this.emit('message', msg);
      else if (line.trim()) this.emit('bad_line', line);
    });
    readline.createInterface({ input: this.child.stderr }).on('line', (line) => this.emit('stderr', line));

    this.child.on('error', (err) => {
      this.logger?.error('Simulation process error:', err.message);
      this._exit(-1, null);
    });
    this.child.on('close', (code, signal) => this._exit(code, signal));
  }

  _exit(code, signal) {
    if (this.exited) return;
    this.exited = true;
    clearTimeout(this._killTimer);
    this.emit('exit', { code, signal, stopped: this.stopped });
  }

  /** Ask the simulation to stop: SIGTERM (Python closes SUMO and reports), SIGKILL after a grace period.
   *  On Windows there is no SIGTERM, so the whole process tree (python + sumo) is terminated. */
  stop(graceMs = 5000) {
    if (this.exited) return Promise.resolve();
    this.stopped = true;
    const done = new Promise((resolve) => this.once('exit', resolve));
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(this.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' }).on('error', () =>
        this.child.kill(),
      );
    } else {
      this.child.kill('SIGTERM');
      this._killTimer = setTimeout(() => !this.exited && this.child.kill('SIGKILL'), graceMs);
    }
    return done;
  }
}
