// Unit tests for SimProcess: the gateway's handle on one simulation child process.
// A small Node script stands in for the Python simulation, so no Python or SUMO is needed.
import { describe, expect, it } from 'vitest';
import { SimProcess } from '../src/simProcess.js';

// Starts a fake simulation running `script` with Node.
function fake(script) {
  return new SimProcess({ pythonBin: process.execPath, args: ['-e', script], cwd: process.cwd(), env: {} });
}

// Collects events until the process exits.
function collect(proc) {
  const seen = { messages: [], bad: [], stderr: [] };
  proc.on('message', (m) => seen.messages.push(m));
  proc.on('bad_line', (l) => seen.bad.push(l));
  proc.on('stderr', (l) => seen.stderr.push(l));
  const exit = new Promise((resolve) => proc.on('exit', resolve));
  return { seen, exit };
}

describe('SimProcess', () => {
  it('turns protocol lines into messages and reports other output separately', async () => {
    const proc = fake(`
      console.log(JSON.stringify({ type: 'status', state: 'running' }));
      console.log('this is not json');
      console.log(JSON.stringify({ type: 'unknown_kind' }));
      console.log('');
      console.error('a warning on stderr');
      console.log(JSON.stringify({ type: 'event', event: 'sim_started' }));`);
    const { seen, exit } = collect(proc);
    const info = await exit;
    expect(info).toMatchObject({ code: 0, stopped: false });
    expect(seen.messages.map((m) => m.type)).toEqual(['status', 'event']);
    expect(seen.messages[1].kind).toBe('sim_started'); // events get a kind
    expect(seen.bad).toEqual(['this is not json', '{"type":"unknown_kind"}']); // empty lines are ignored
    expect(seen.stderr).toEqual(['a warning on stderr']);
  });

  it('reports a failing simulation with its exit code', async () => {
    const { exit } = collect(fake('process.exit(3)'));
    expect(await exit).toMatchObject({ code: 3, stopped: false });
  });

  it('send() writes one JSON line to the simulation (driver commands)', async () => {
    const proc = fake(`
      process.stdin.once('data', (d) => {
        const cmd = JSON.parse(String(d).trim());
        console.log(JSON.stringify({ type: 'driver', echo: cmd.cmd }));
        process.exit(0);
      });`);
    const { seen, exit } = collect(proc);
    expect(proc.send({ cmd: 'faster' })).toBe(true);
    await exit;
    expect(seen.messages[0]).toMatchObject({ type: 'driver', echo: 'faster' });
    expect(proc.send({ cmd: 'slower' })).toBe(false); // nothing is sent after the process ended
  });

  it('stop() ends a running simulation and marks it as stopped', async () => {
    const proc = fake('setInterval(() => {}, 1000)');
    const { exit } = collect(proc);
    await proc.stop(1000);
    expect(await exit).toMatchObject({ stopped: true });
    expect(proc.send({ cmd: 'x' })).toBe(false);
    await proc.stop(); // a second stop is harmless
  });

  it('a program that cannot start reports an exit instead of crashing the gateway', async () => {
    const proc = new SimProcess({ pythonBin: 'no-such-python-binary-xyz', args: [], cwd: process.cwd(), env: {} });
    const { exit } = collect(proc);
    expect((await exit).code).toBe(-1);
  });
});
