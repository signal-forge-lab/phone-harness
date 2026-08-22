const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadWindowState, normalizeWindowState, resolveWindowState, saveWindowState } = require('../window-state.cjs');

test('window state enforces usable minimum size and preserves coordinates', () => {
  assert.deepEqual(normalizeWindowState({ width: 100, height: 100, x: 20.4, y: 30.6, maximized: true }), {
    width: 760,
    height: 560,
    x: 20,
    y: 31,
    maximized: true,
  });
});

test('resolveWindowState preserves a fully visible saved window', () => {
  const state = { x: 120, y: 80, width: 900, height: 700 };
  assert.deepEqual(resolveWindowState(state, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    ...state,
    maximized: false,
  });
});

test('resolveWindowState preserves a partially visible saved window', () => {
  const state = { x: -850, y: 80, width: 900, height: 700 };
  assert.deepEqual(resolveWindowState(state, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    ...state,
    maximized: false,
  });
});

test('resolveWindowState removes coordinates only when the window is fully off-screen', () => {
  assert.deepEqual(resolveWindowState({ x: -1000, y: 80, width: 900, height: 700 }, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('resolveWindowState normalizes invalid saved geometry safely', () => {
  assert.deepEqual(resolveWindowState({ x: 'bad', y: NaN, width: 'bad', height: 20 }, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    width: 1500,
    height: 560,
    maximized: false,
  });
});

test('resolveWindowState leaves coordinates unchanged when no displays are available', () => {
  const state = { x: -2400, y: 120, width: 900, height: 700 };
  assert.deepEqual(resolveWindowState(state, []), { ...state, maximized: false });
});

test('resolveWindowState leaves coordinates unchanged when display data is invalid', () => {
  const state = { x: -2400, y: 120, width: 900, height: 700 };
  assert.deepEqual(resolveWindowState(state, [{ x: 0, y: 0, width: 0, height: 1080 }, null]), {
    ...state,
    maximized: false,
  });
});

test('normalizeWindowState defaults a null persisted value', () => {
  assert.deepEqual(normalizeWindowState(null), { width: 1500, height: 980, maximized: false });
});

test('resolveWindowState recovers a window stranded on a disconnected secondary display', () => {
  // Saved on a left-of-primary monitor (negative x) that is no longer connected.
  assert.deepEqual(resolveWindowState({ x: -1900, y: 100, width: 900, height: 700 }, [{ x: 0, y: 0, width: 1920, height: 1040 }]), {
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('resolveWindowState keeps a saved window on a connected secondary display', () => {
  const displays = [
    { x: -1920, y: 0, width: 1920, height: 1040 },
    { x: 0, y: 0, width: 1920, height: 1040 },
  ];
  assert.deepEqual(resolveWindowState({ x: -1900, y: 100, width: 900, height: 700 }, displays), {
    x: -1900,
    y: 100,
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('resolveWindowState recovers a saved window fully below the primary work area', () => {
  assert.deepEqual(resolveWindowState({ x: 300, y: 2000, width: 900, height: 700 }, [{ x: 0, y: 0, width: 1920, height: 1040 }]), {
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('resolveWindowState drops both coordinates when only x is persisted', () => {
  assert.deepEqual(resolveWindowState({ x: -9999, width: 900, height: 700 }, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('resolveWindowState drops both coordinates when only y is persisted', () => {
  assert.deepEqual(resolveWindowState({ y: 5000, width: 900, height: 700 }, [{ x: 0, y: 0, width: 1920, height: 1080 }]), {
    width: 900,
    height: 700,
    maximized: false,
  });
});

test('persistence round-trip keeps valid bounds and recovers an off-screen position', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ph-window-state-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, 'window-state.json');
  const browserWindow = {
    isDestroyed: () => false,
    isMaximized: () => true,
    getNormalBounds: () => ({ x: -2600, y: 140, width: 980, height: 760 }),
  };
  saveWindowState(file, browserWindow);
  const loaded = loadWindowState(file);
  assert.deepEqual(loaded, { x: -2600, y: 140, width: 980, height: 760, maximized: true });
  assert.deepEqual(resolveWindowState(loaded, [{ x: 0, y: 0, width: 1920, height: 1040 }]), {
    width: 980,
    height: 760,
    maximized: true,
  });
});
