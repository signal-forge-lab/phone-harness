const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_WINDOW_STATE = Object.freeze({ width: 1500, height: 980, maximized: false });

function normalizeWindowState(value = {}) {
  const source = value && typeof value === 'object' ? value : {};
  const width = Number.isFinite(source.width) ? Math.max(760, Math.round(source.width)) : DEFAULT_WINDOW_STATE.width;
  const height = Number.isFinite(source.height) ? Math.max(560, Math.round(source.height)) : DEFAULT_WINDOW_STATE.height;
  const result = { width, height, maximized: source.maximized === true };
  if (Number.isFinite(source.x)) result.x = Math.round(source.x);
  if (Number.isFinite(source.y)) result.y = Math.round(source.y);
  return result;
}

function isValidWorkArea(area) {
  return area
    && Number.isFinite(area.x)
    && Number.isFinite(area.y)
    && Number.isFinite(area.width)
    && Number.isFinite(area.height)
    && area.width > 0
    && area.height > 0;
}

function intersectsWorkArea(state, area) {
  return area.x < state.x + state.width
    && state.x < area.x + area.width
    && area.y < state.y + state.height
    && state.y < area.y + area.height;
}

function resolveWindowState(value = {}, workAreas = []) {
  const state = normalizeWindowState(value);
  // A lone coordinate cannot be validated against displays and would hand
  // Electron invalid/invisible geometry; keep both or neither.
  if (!Number.isFinite(state.x) || !Number.isFinite(state.y)) {
    const { x, y, ...noPosition } = state;
    return noPosition;
  }
  const validWorkAreas = Array.isArray(workAreas) ? workAreas.filter(isValidWorkArea) : [];
  if (validWorkAreas.length === 0) return state;
  if (validWorkAreas.some((area) => intersectsWorkArea(state, area))) return state;
  const { x, y, ...recenter } = state;
  return recenter;
}

function loadWindowState(filePath) {
  try {
    return normalizeWindowState(JSON.parse(fs.readFileSync(filePath, 'utf8')));
  } catch {
    return normalizeWindowState();
  }
}

function saveWindowState(filePath, browserWindow) {
  if (!browserWindow || browserWindow.isDestroyed()) return;
  const bounds = browserWindow.isMaximized() ? browserWindow.getNormalBounds() : browserWindow.getBounds();
  const value = normalizeWindowState({ ...bounds, maximized: browserWindow.isMaximized() });
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

module.exports = {
  DEFAULT_WINDOW_STATE,
  loadWindowState,
  normalizeWindowState,
  resolveWindowState,
  saveWindowState,
};
