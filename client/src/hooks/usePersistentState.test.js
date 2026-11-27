// Unit tests for page state that survives moving between pages (sessionStorage, prefix itsc-ui:).
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearPersistentState, usePersistentState } from './usePersistentState.js';

// The tests run in Node, which has no sessionStorage: a small in-memory one stands in for it.
function memoryStorage() {
  const data = new Map();
  const store = {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
  return new Proxy(store, { ownKeys: () => [...data.keys()], getOwnPropertyDescriptor: () => ({ enumerable: true, configurable: true }) });
}

// Renders a component that shows the hook's first value.
function firstValue(key, initial) {
  const Probe = () => createElement('span', null, JSON.stringify(usePersistentState(key, initial)[0]));
  return renderToString(createElement(Probe)).replace(/<\/?span>/g, '').replace(/&quot;/g, '"');
}

describe('usePersistentState', () => {
  beforeEach(() => {
    globalThis.sessionStorage = memoryStorage();
  });
  afterEach(() => {
    delete globalThis.sessionStorage;
  });

  it('starts from the initial value when nothing is saved', () => {
    expect(firstValue('tab', 'Overview')).toBe('"Overview"');
    expect(firstValue('form', () => ({ seed: 1 }))).toBe('{"seed":1}');
  });

  it('restores a saved value under the itsc-ui: prefix', () => {
    sessionStorage.setItem('itsc-ui:tab', JSON.stringify('Vehicle'));
    expect(firstValue('tab', 'Overview')).toBe('"Vehicle"');
  });

  it('falls back to the initial value when the saved text is broken', () => {
    sessionStorage.setItem('itsc-ui:tab', '{not json');
    expect(firstValue('tab', 'Overview')).toBe('"Overview"');
  });

  it('works like plain state when storage is unavailable', () => {
    delete globalThis.sessionStorage;
    expect(firstValue('tab', 'Overview')).toBe('"Overview"');
  });

  it('clearPersistentState removes only this app’s keys (used at sign-out)', () => {
    sessionStorage.setItem('itsc-ui:tab', '"Vehicle"');
    sessionStorage.setItem('itsc-ui:admin.form', '{}');
    sessionStorage.setItem('other-app', 'keep');
    clearPersistentState();
    expect(sessionStorage.getItem('itsc-ui:tab')).toBeNull();
    expect(sessionStorage.getItem('itsc-ui:admin.form')).toBeNull();
    expect(sessionStorage.getItem('other-app')).toBe('keep');
  });

  it('clearPersistentState does not throw without storage', () => {
    delete globalThis.sessionStorage;
    expect(() => clearPersistentState()).not.toThrow();
  });
});
