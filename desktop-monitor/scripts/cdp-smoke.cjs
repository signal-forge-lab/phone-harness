const endpoint = process.argv[2] || 'http://127.0.0.1:9223';

async function main() {
  const pages = await fetch(`${endpoint}/json/list`).then((response) => response.json());
  const page = pages.find((item) => item.type === 'page' && item.url.includes(':17678/'));
  if (!page?.webSocketDebuggerUrl) throw new Error('Phone Harness Monitor debug page not found');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('DevTools WebSocket failed')), { once: true });
  });

  let nextId = 1;
  const pending = new Map();
  const failures = [];
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
      return;
    }
    if (message.method === 'Runtime.exceptionThrown') {
      failures.push(message.params?.exceptionDetails?.text || 'Runtime exception');
    }
    if (message.method === 'Log.entryAdded' && message.params?.entry?.level === 'error') {
      failures.push(message.params.entry.text || 'Browser log error');
    }
  });

  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });

  await call('Runtime.enable');
  await call('Log.enable');
  await call('Page.enable');
  await call('Page.reload', { ignoreCache: true });
  await new Promise((resolve) => setTimeout(resolve, 2500));
  const evaluated = await call('Runtime.evaluate', {
    expression: `JSON.stringify({
      title: document.title,
      desktopDock: document.body.classList.contains('desktop-dock'),
      dockRegions: document.querySelectorAll('#desktopDock .region').length,
      dockTabs: document.querySelectorAll('#desktopDock .dv-tab').length,
      infoButtons: document.querySelectorAll('.info-button').length,
      imageLog: Boolean(document.getElementById('imageLog')),
      languageToggle: Boolean(document.getElementById('languageToggle')),
      desktopApi: typeof window.phoneHarnessDesktop === 'object',
      dockviewApi: typeof window.dockview?.createDockview === 'function',
      savedLayout: Boolean(localStorage.getItem('phoneHarnessDockLayoutV2')),
    })`,
    returnByValue: true,
  });
  const state = JSON.parse(evaluated.result.value);

  const expected = {
    desktopDock: true,
    dockRegions: 10,
    infoButtons: 10,
    imageLog: true,
    languageToggle: true,
    desktopApi: true,
    dockviewApi: true,
    savedLayout: true,
  };
  for (const [key, value] of Object.entries(expected)) {
    if (state[key] !== value) failures.push(`${key}: expected ${value}, got ${state[key]}`);
  }
  if (state.dockTabs < 6) failures.push(`dockTabs: expected at least 6, got ${state.dockTabs}`);

  const desktopStatus = await call('Runtime.evaluate', {
    expression: 'window.phoneHarnessDesktop.getStatus()',
    awaitPromise: true,
    returnByValue: true,
  });
  const ipcStatus = desktopStatus.result.value;
  if (!ipcStatus?.monitor?.owned || !ipcStatus?.monitor?.pid) failures.push('Electron does not report ownership of the monitor server');

  const interaction = await call('Runtime.evaluate', {
    expression: `(() => {
      const language = document.getElementById('languageToggle');
      language.click();
      const english = language.textContent;
      const info = document.querySelector('.info-button');
      info.click();
      const tooltip = document.getElementById('monitorInfoTooltip');
      const tooltipVisible = !tooltip.hidden && tooltip.textContent.length > 20;
      info.click();
      language.click();
      return { english, tooltipVisible, japanese: language.textContent };
    })()`,
    returnByValue: true,
  });
  const interactionState = interaction.result.value;
  if (!String(interactionState.english).includes('English')) failures.push('Language toggle did not switch to English');
  if (!interactionState.tooltipVisible) failures.push('Info tooltip did not open');
  if (!String(interactionState.japanese).includes('日本語')) failures.push('Language toggle did not switch back to Japanese');

  const panelActionResult = await call('Runtime.evaluate', {
    expression: `(async () => {
      const decision = document.querySelector('.decision-region');
      const collapse = decision.querySelector('[data-collapse="decision"]');
      collapse.click();
      const collapsed = decision.classList.contains('is-collapsed');
      collapse.click();
      const expanded = !decision.classList.contains('is-collapsed');
      const pin = decision.querySelector('[data-dock-action="pin"]');
      pin.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      const pinned = decision.classList.contains('is-pinned') && pin.getAttribute('aria-pressed') === 'true';
      pin.click();
      const maximize = decision.querySelector('[data-dock-action="maximize"]');
      const before = decision.getBoundingClientRect();
      maximize.click();
      await new Promise((resolve) => setTimeout(resolve, 160));
      const after = decision.getBoundingClientRect();
      const maximized = after.width > before.width * 1.3 || after.height > before.height * 1.3;
      const maximizeLayout = JSON.parse(localStorage.getItem('phoneHarnessDockLayoutV2') || '{}');
      maximize.click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      const floatButton = decision.querySelector('[data-dock-action="float"]');
      floatButton.click();
      await new Promise((resolve) => setTimeout(resolve, 160));
      const floating = decision.closest('.dv-floating-container, .dv-floating-group, .dv-resize-container, .dv-overlay') !== null;
      const floatLayout = JSON.parse(localStorage.getItem('phoneHarnessDockLayoutV2') || '{}');
      document.getElementById('layoutReset').click();
      await new Promise((resolve) => setTimeout(resolve, 120));
      return {
        collapsed,
        expanded,
        pinned,
        maximized,
        maximizedSerialized: Boolean(maximizeLayout.grid?.maximizedNode || maximizeLayout.maximizedNode),
        floating,
        floatingSerialized: Boolean(floatLayout.floatingGroups?.length || floatLayout.grid?.floatingGroups?.length),
        tabsAfterReset: document.querySelectorAll('#desktopDock .dv-tab').length,
      };
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  const panelActions = panelActionResult.result.value;
  if (!panelActions.collapsed || !panelActions.expanded) failures.push('Panel collapse/expand control failed');
  if (!panelActions.pinned) failures.push('Panel pin control did not enter pinned state');
  if (!panelActions.maximizedSerialized) failures.push('Panel maximize control did not update Dockview state');
  if (!panelActions.floatingSerialized) failures.push('Panel float control did not update Dockview state');
  if (panelActions.tabsAfterReset !== 10) failures.push(`Layout reset did not restore 10 tabs: ${panelActions.tabsAfterReset}`);

  await call('Emulation.setDeviceMetricsOverride', {
    width: 1100,
    height: 700,
    deviceScaleFactor: 1,
    mobile: false,
    screenOrientation: { type: 'landscapePrimary', angle: 90 },
  });
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const tabletResult = await call('Runtime.evaluate', {
    expression: `JSON.stringify({
      desktopDock: document.body.classList.contains('desktop-dock'),
      innerWidth: window.innerWidth,
      desktopMedia: window.matchMedia('(min-width: 1181px)').matches,
      areas: getComputedStyle(document.getElementById('monitor')).gridTemplateAreas,
      columns: getComputedStyle(document.getElementById('monitor')).gridTemplateColumns,
    })`,
    returnByValue: true,
  });
  const tablet = JSON.parse(tabletResult.result.value);
  if (tablet.desktopDock) failures.push('Tablet landscape incorrectly kept desktop docking active');
  if (!tablet.areas.includes('preview decision') || !tablet.areas.includes('imagelog detail')) failures.push(`Tablet landscape grid is unexpected: ${tablet.areas}`);

  await call('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: false,
    screenOrientation: { type: 'portraitPrimary', angle: 0 },
  });
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const phoneResult = await call('Runtime.evaluate', {
    expression: `JSON.stringify({
      desktopDock: document.body.classList.contains('desktop-dock'),
      innerWidth: window.innerWidth,
      desktopMedia: window.matchMedia('(min-width: 1181px)').matches,
      areas: getComputedStyle(document.getElementById('monitor')).gridTemplateAreas,
    })`,
    returnByValue: true,
  });
  const phone = JSON.parse(phoneResult.result.value);
  if (phone.desktopDock) failures.push('Phone portrait incorrectly kept desktop docking active');
  if (!phone.areas.includes('"preview"') || !phone.areas.includes('"imagelog"') || !phone.areas.includes('"teaching"')) failures.push(`Phone portrait grid is unexpected: ${phone.areas}`);

  await call('Emulation.setDeviceMetricsOverride', {
    width: 1500,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
    screenOrientation: { type: 'landscapePrimary', angle: 90 },
  });
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const restoredResult = await call('Runtime.evaluate', {
    expression: `JSON.stringify({
      desktopDock: document.body.classList.contains('desktop-dock'),
      innerWidth: window.innerWidth,
      dockTabs: document.querySelectorAll('#desktopDock .dv-tab').length,
    })`,
    returnByValue: true,
  });
  const restored = JSON.parse(restoredResult.result.value);
  if (!restored.desktopDock || restored.dockTabs !== 10) failures.push(`Desktop dock did not restore after viewport changes: ${JSON.stringify(restored)}`);

  socket.close();
  console.log(JSON.stringify({ state, ipcStatus, interaction: interactionState, panelActions, tablet, phone, restored, failures }, null, 2));
  if (failures.length) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
