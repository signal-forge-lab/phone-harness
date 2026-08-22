(function () {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const desktopMedia = window.matchMedia('(min-width: 1181px)');
  const isDesktopViewport = () => window.innerWidth >= 1181;
  const layoutKey = 'phoneHarnessDockLayoutV2';
  const languageKey = 'phoneHarnessMonitorLanguageV1';
  const collapsedKey = 'phoneHarnessCollapsedPanelsV1';
  const pinnedKey = 'phoneHarnessPinnedPanelsV1';
  let language = localStorage.getItem(languageKey) === 'en' ? 'en' : 'ja';
  let dockApi = null;
  let dockLayoutDisposable = null;
  let dockPanelDragDisposable = null;
  let dockGroupDragDisposable = null;
  let viewportObserver = null;
  let imageSignature = '';
  const panelById = new Map();
  let pinnedPanels = loadPinned();

  const copy = {
    ja: {
      language: '説明: 日本語', reset: 'Layout Reset', collapse: '折りたたむ', expand: '展開する',
      pin: 'Pin', maximize: '最大化', restore: '元に戻す', float: 'Float',
      noImages: '保存された画像ログはまだありません。', imageLogMeta: 'trace preview cache',
      answerPlaceholder: '回答を入力（Ctrl+Enterで送信）', teachPlaceholder: 'AIへ伝えたいルール、訂正、補足、指示など（Ctrl+Enterで送信）',
      monitor: 'Monitor', harness: 'Phone Harness', running: 'RUNNING', stopped: 'STOPPED', external: 'EXTERNAL',
      start: 'Start', stop: 'Stop', restart: 'Restart', reload: 'Reload UI', developer: 'Developer', logs: 'Logs', build: 'Build', buildRestart: 'Build & Restart', buildExe: 'Build EXE', devtools: 'DevTools',
      decision: 'AI/Workflowが明示的に記録した判断の要約です。内部の非公開推論ではなく、実行方針・根拠・代替案などの観測用メタデータを表示します。',
      pipeline: '直近イベントから計測した各処理段階の時間です。段階は重なる場合があるため、各行を単純合計して全体時間とはみなしません。',
      runtime: '直近のCapture、Analysis、画像サイズ、Backend、注文数、空きセルなど実行時の主要値です。',
      failure: '最新の失敗判定です。reason / expected / actual / next action を表示します。phase が preflight の場合は実操作前に停止しています。',
      teaching: 'AIから人への確認と、人からAIへの回答・補足を扱います。Human Teaching向けのAI文面は日本語を原則とします。',
      inbox: '作業を止めずにAIへルール・訂正・画像を送る入力欄です。',
      timeline: 'Phone Harnessが記録した観測・判断・操作・エラー等のイベント時系列です。選択すると詳細や関連画像を表示します。',
      detail: 'Timelineで選択したイベントの構造化データとAccessibility/OCRの補助情報です。',
      preview: '現在または選択中イベントのキャプチャ画像です。Fitと100%表示を切り替えられます。',
      images: 'Traceに紐付いて保持されている最近のキャプチャ画像です。選択するとCurrent Frameで確認できます。',
      stale: '操作の基準にした画面情報が最新ではなくなったため、安全のため実操作前に停止しました。最新画面を再観測して再計画すれば再試行できます。',
      pauseReplan: 'Human Pause中に画面が変わった可能性があるため、古い計画を実行せず再観測を要求しました。',
      preflight: 'preflightで停止したため、この失敗によるタップやドラッグは実行されていません。',
    },
    en: {
      language: 'Help: English', reset: 'Layout Reset', collapse: 'Collapse', expand: 'Expand',
      pin: 'Pin', maximize: 'Maximize', restore: 'Restore', float: 'Float',
      noImages: 'No cached image-log previews yet.', imageLogMeta: 'trace preview cache',
      answerPlaceholder: 'Type an answer (Ctrl+Enter to send)', teachPlaceholder: 'Send a rule, correction, note, or instruction (Ctrl+Enter to send)',
      monitor: 'Monitor', harness: 'Phone Harness', running: 'RUNNING', stopped: 'STOPPED', external: 'EXTERNAL',
      start: 'Start', stop: 'Stop', restart: 'Restart', reload: 'Reload UI', developer: 'Developer', logs: 'Logs', build: 'Build', buildRestart: 'Build & Restart', buildExe: 'Build EXE', devtools: 'DevTools',
      decision: 'An explicit AI/workflow decision summary for observability. It shows recorded execution intent, evidence, and alternatives; it is not hidden chain-of-thought.',
      pipeline: 'Recent measured timing by pipeline stage. Stages can overlap, so the rows should not be added together as total wall-clock time.',
      runtime: 'Latest runtime values such as capture, analysis, image size, backend, visible orders, and free cells.',
      failure: 'The latest failure judgment with reason, expected, actual, and next action. A preflight failure stopped before a phone action ran.',
      teaching: 'Questions from AI to the operator and answers or corrections from the operator. Human-facing AI prompts are expected to be Japanese by default.',
      inbox: 'Send rules, corrections, notes, or an image to the AI without blocking the current work.',
      timeline: 'Chronological observation, decision, action, and error events recorded by Phone Harness. Select an event to inspect its details or image.',
      detail: 'Structured data and Accessibility/OCR support information for the selected Timeline event.',
      preview: 'The current or selected capture. Switch between Fit and 100% display.',
      images: 'Recent capture images retained by trace preview caching. Select one to inspect it in Current Frame.',
      stale: 'The screen observation used to plan the action is no longer current, so Phone Harness stopped before executing the action. Re-observe and re-plan before retrying.',
      pauseReplan: 'The screen may have changed during Human Pause, so the previous plan was rejected and a fresh observation is required.',
      preflight: 'Because this stopped in preflight, no tap or drag was executed by this failed request.',
    },
  };

  const tr = (key) => copy[language][key] || key;

  const panelDefs = [
    { id: 'preview', selector: '.preview-region', title: 'Current Frame / Capture', topic: 'preview' },
    { id: 'decision', selector: '.decision-region', title: 'Decision', topic: 'decision' },
    { id: 'failure', selector: '.failure-region', title: 'Failure Judgment', topic: 'failure' },
    { id: 'teaching', selector: '.teaching-region', title: 'Human Teaching', topic: 'teaching' },
    { id: 'inbox', selector: '.inbox-region', title: 'Human Teaching Inbox', topic: 'inbox' },
    { id: 'timeline', selector: '.timeline-region', title: 'Timeline', topic: 'timeline' },
    { id: 'runtime', selector: '.runtime-region', title: 'Runtime', topic: 'runtime' },
    { id: 'performance', selector: '.performance-region', title: 'Pipeline Timing', topic: 'pipeline' },
    { id: 'detail', selector: '.detail-region', title: 'Analysis / Event Detail', topic: 'detail' },
    { id: 'images', selector: '.image-log-region', title: 'Image Log', topic: 'images' },
  ];

  function createImageLogRegion() {
    if (document.querySelector('.image-log-region')) return;
    const section = document.createElement('section');
    section.className = 'region image-log-region';
    section.setAttribute('aria-labelledby', 'imageLogTitle');
    section.innerHTML = '<div class="region-heading"><h2 id="imageLogTitle">Image Log</h2><div class="region-meta">trace preview cache</div></div><div class="image-log-body"><div id="imageLog" class="image-log-grid"></div></div>';
    const monitor = byId('monitor');
    const firstSplitter = monitor.querySelector('.grid-splitter');
    monitor.insertBefore(section, firstSplitter || null);
  }

  function createToolbar() {
    if (byId('monitorUtilityBar')) return;
    const toolbar = document.createElement('div');
    toolbar.id = 'monitorUtilityBar';
    toolbar.className = 'monitor-toolbar';
    toolbar.innerHTML = '<button id="languageToggle" class="toolbar-button" type="button"></button><button id="layoutReset" class="toolbar-button desktop-only" type="button">Layout Reset</button><span class="toolbar-spacer"></span>';
    document.querySelector('.topbar').after(toolbar);
    byId('languageToggle').addEventListener('click', () => {
      language = language === 'ja' ? 'en' : 'ja';
      localStorage.setItem(languageKey, language);
      applyLanguage();
      renderImageLog(true);
      decorateFailure();
    });
    byId('layoutReset').addEventListener('click', resetDockLayout);
    if (window.phoneHarnessDesktop) addDesktopControls(toolbar);
  }

  function addDesktopControls(toolbar) {
    const host = document.createElement('div');
    host.className = 'control-group desktop-only';
    host.innerHTML = '<span id="desktopMonitorStatus" class="control-status">Monitor …</span><button class="control-button" data-control="monitor-start" type="button">Start</button><button class="control-button" data-control="monitor-stop" type="button">Stop</button><button class="control-button" data-control="monitor-restart" type="button">Restart</button><button class="control-button" data-control="monitor-reload" type="button">Reload UI</button>';
    toolbar.append(host);
    const harness = document.createElement('div');
    harness.className = 'control-group desktop-only';
    harness.innerHTML = '<span id="desktopHarnessStatus" class="control-status">Phone Harness …</span><button class="control-button" data-control="harness-start" type="button">Start</button><button class="control-button" data-control="harness-stop" type="button">Stop</button><button class="control-button" data-control="harness-restart" type="button">Restart</button>';
    toolbar.append(harness);
    const developer = document.createElement('details');
    developer.className = 'developer-controls desktop-only';
    developer.innerHTML = '<summary class="toolbar-button">Developer</summary><div class="developer-panel"><button class="control-button" data-control="build" type="button">Build</button><button class="control-button" data-control="build-restart" type="button">Build & Restart</button><button class="control-button" data-control="build-exe" type="button">Build EXE</button><button class="control-button" data-control="devtools" type="button">DevTools</button><button class="control-button" data-control="logs" type="button">Logs</button><pre id="desktopLog" class="desktop-log" hidden></pre></div>';
    toolbar.append(developer);
    toolbar.addEventListener('click', desktopControlClick);
    refreshDesktopStatus();
    window.setInterval(refreshDesktopStatus, 2000);
  }

  async function desktopControlClick(event) {
    const button = event.target.closest('[data-control]');
    if (!button || !window.phoneHarnessDesktop) return;
    const action = button.dataset.control;
    const api = window.phoneHarnessDesktop;
    button.disabled = true;
    try {
      if (action === 'monitor-start') await api.monitor.start();
      else if (action === 'monitor-stop') await api.monitor.stop();
      else if (action === 'monitor-restart') await api.monitor.restart();
      else if (action === 'monitor-reload') await api.monitor.reload();
      else if (action === 'harness-start') await api.harness.start();
      else if (action === 'harness-stop') await api.harness.stop();
      else if (action === 'harness-restart') await api.harness.restart();
      else if (action === 'build') await api.developer.build();
      else if (action === 'build-restart') await api.developer.buildAndRestart();
      else if (action === 'build-exe') await api.developer.buildExe();
      else if (action === 'devtools') await api.developer.toggleDevTools();
      else if (action === 'logs') await showDesktopLogs();
    } catch (error) {
      showDesktopLog(String(error?.message || error));
    } finally {
      button.disabled = false;
      refreshDesktopStatus();
    }
  }

  function showDesktopLog(text) {
    const log = byId('desktopLog');
    if (!log) return;
    log.hidden = false;
    log.textContent = text;
  }

  async function showDesktopLogs() {
    const value = await window.phoneHarnessDesktop.getLogs();
    const lines = [...(value.monitor || []), ...(value.desktop || []).map((item) => `${item.at} ${item.label}\n${item.output}`)];
    showDesktopLog(lines.join('\n\n') || 'No desktop logs yet.');
  }

  async function refreshDesktopStatus() {
    if (!window.phoneHarnessDesktop) return;
    try {
      const value = await window.phoneHarnessDesktop.getStatus();
      const monitor = value.monitor || {};
      const harness = value.harness || {};
      const monitorNode = byId('desktopMonitorStatus');
      const harnessNode = byId('desktopHarnessStatus');
      if (monitorNode) monitorNode.innerHTML = `<strong>${tr('monitor')}</strong> ${monitor.external ? tr('external') : (monitor.running ? tr('running') : tr('stopped'))}${monitor.pid ? ` · PID ${monitor.pid}` : ''}`;
      if (harnessNode) harnessNode.innerHTML = `<strong>${tr('harness')}</strong> ${harness.running ? tr('running') : tr('stopped')}${harness.pid ? ` · PID ${harness.pid}` : ''}${Number.isFinite(harness.uptimeSeconds) ? ` · ${formatDuration(harness.uptimeSeconds)}` : ''}`;
    } catch (error) {
      showDesktopLog(String(error?.message || error));
    }
  }

  function formatDuration(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    return `${h}h ${m}m`;
  }

  function installInfoAndPanelActions() {
    const collapsed = loadCollapsed();
    for (const def of panelDefs) {
      const region = document.querySelector(def.selector);
      if (!region) continue;
      region.dataset.dockPanel = def.id;
      if (collapsed.has(def.id)) region.classList.add('is-collapsed');
      const heading = region.querySelector('.region-heading');
      if (!heading || heading.querySelector('.info-button')) continue;
      const info = document.createElement('button');
      info.type = 'button';
      info.className = 'info-button';
      info.dataset.topic = def.topic;
      info.setAttribute('aria-label', `${def.title} information`);
      info.setAttribute('aria-expanded', 'false');
      info.textContent = 'i';
      const collapse = document.createElement('button');
      collapse.type = 'button';
      collapse.className = 'collapse-button';
      collapse.dataset.collapse = def.id;
      collapse.textContent = region.classList.contains('is-collapsed') ? '+' : '−';
      collapse.addEventListener('click', () => toggleCollapse(def.id));
      const pin = dockActionButton('⌖', 'pin', def.id);
      const maximize = dockActionButton('□', 'maximize', def.id);
      const float = dockActionButton('↗', 'float', def.id);
      heading.append(info, collapse, pin, maximize, float);
    }
    installTooltipEvents();
  }

  function dockActionButton(text, action, panelId) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'collapse-button dock-only';
    button.dataset.dockAction = action;
    button.dataset.panelId = panelId;
    button.textContent = text;
    button.addEventListener('click', handleDockAction);
    return button;
  }

  function handleDockAction(event) {
    if (!dockApi) return;
    const panel = dockApi.getPanel(event.currentTarget.dataset.panelId);
    if (!panel) return;
    const action = event.currentTarget.dataset.dockAction;
    if (action === 'pin') {
      togglePinned(panel.id);
      return;
    }
    if (pinnedPanels.has(panel.id) && action === 'float') return;
    if (action === 'maximize') {
      if (dockApi.hasMaximizedGroup()) dockApi.exitMaximizedGroup(); else dockApi.maximizeGroup(panel);
    } else if (action === 'float' && panel.api?.location?.type !== 'floating') {
      dockApi.addFloatingGroup(panel, { width: Math.min(680, innerWidth - 80), height: Math.min(520, innerHeight - 100) });
    }
    persistDockLayout();
  }

  function loadPinned() {
    try { return new Set(JSON.parse(localStorage.getItem(pinnedKey) || '[]')); } catch { return new Set(); }
  }

  function togglePinned(id) {
    if (pinnedPanels.has(id)) pinnedPanels.delete(id); else pinnedPanels.add(id);
    localStorage.setItem(pinnedKey, JSON.stringify([...pinnedPanels]));
    applyPinnedState();
  }

  function applyPinnedState() {
    for (const def of panelDefs) {
      const pinned = pinnedPanels.has(def.id);
      const region = document.querySelector(def.selector);
      region?.classList.toggle('is-pinned', pinned);
      const button = region?.querySelector(`[data-dock-action="pin"][data-panel-id="${def.id}"]`);
      if (button) {
        button.classList.toggle('is-active', pinned);
        button.setAttribute('aria-pressed', pinned ? 'true' : 'false');
      }
      const panel = dockApi?.getPanel(def.id);
      if (panel?.group?.api) panel.group.api.locked = pinned ? 'no-drop-target' : false;
    }
  }

  function loadCollapsed() {
    try { return new Set(JSON.parse(localStorage.getItem(collapsedKey) || '[]')); } catch { return new Set(); }
  }

  function toggleCollapse(id) {
    if (pinnedPanels.has(id)) return;
    const region = document.querySelector(`[data-dock-panel="${id}"]`);
    if (!region) return;
    region.classList.toggle('is-collapsed');
    const values = new Set(panelDefs.filter((item) => document.querySelector(item.selector)?.classList.contains('is-collapsed')).map((item) => item.id));
    localStorage.setItem(collapsedKey, JSON.stringify([...values]));
    const button = region.querySelector(`[data-collapse="${id}"]`);
    if (button) button.textContent = region.classList.contains('is-collapsed') ? '+' : '−';
    applyLanguage();
  }

  let tooltip = null;
  let tooltipButton = null;
  function installTooltipEvents() {
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'info-tooltip';
      tooltip.id = 'monitorInfoTooltip';
      tooltip.setAttribute('role', 'tooltip');
      tooltip.hidden = true;
      document.body.append(tooltip);
      document.addEventListener('keydown', (event) => { if (event.key === 'Escape') hideTooltip(); });
      document.addEventListener('pointerdown', (event) => { if (!event.target.closest('.info-button') && !event.target.closest('.info-tooltip')) hideTooltip(); });
    }
    document.querySelectorAll('.info-button').forEach((button) => {
      if (button.dataset.tooltipReady) return;
      button.dataset.tooltipReady = '1';
      button.setAttribute('aria-describedby', tooltip.id);
      button.addEventListener('mouseenter', () => showTooltip(button));
      button.addEventListener('mouseleave', () => { if (document.activeElement !== button) hideTooltip(); });
      button.addEventListener('focus', () => showTooltip(button));
      button.addEventListener('blur', () => hideTooltip());
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (tooltipButton === button && !tooltip.hidden) hideTooltip(); else showTooltip(button);
      });
    });
  }

  function showTooltip(button) {
    tooltipButton = button;
    tooltip.textContent = tr(button.dataset.topic);
    tooltip.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    const rect = button.getBoundingClientRect();
    const width = Math.min(360, innerWidth - 24);
    tooltip.style.width = `${width}px`;
    const left = Math.max(12, Math.min(rect.left, innerWidth - width - 12));
    const top = rect.bottom + 8 + Math.min(0, innerHeight - (rect.bottom + 8 + tooltip.offsetHeight) - 12);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(12, top)}px`;
  }

  function hideTooltip() {
    if (!tooltip) return;
    tooltip.hidden = true;
    if (tooltipButton) tooltipButton.setAttribute('aria-expanded', 'false');
    tooltipButton = null;
  }

  function applyLanguage() {
    document.documentElement.lang = language;
    const toggle = byId('languageToggle');
    if (toggle) toggle.textContent = tr('language');
    const reset = byId('layoutReset');
    if (reset) reset.textContent = tr('reset');
    const answer = byId('answer');
    const teach = byId('teachMessage');
    if (answer) answer.placeholder = tr('answerPlaceholder');
    if (teach) teach.placeholder = tr('teachPlaceholder');
    document.querySelectorAll('[data-collapse]').forEach((button) => {
      const region = document.querySelector(`[data-dock-panel="${button.dataset.collapse}"]`);
      button.title = region?.classList.contains('is-collapsed') ? tr('expand') : tr('collapse');
    });
    document.querySelectorAll('[data-dock-action="pin"]').forEach((button) => { button.title = tr('pin'); });
    document.querySelectorAll('[data-dock-action="maximize"]').forEach((button) => { button.title = tr('maximize'); });
    document.querySelectorAll('[data-dock-action="float"]').forEach((button) => { button.title = tr('float'); });
    if (tooltipButton) showTooltip(tooltipButton);
    const imageMeta = document.querySelector('.image-log-region .region-meta');
    if (imageMeta) imageMeta.textContent = tr('imageLogMeta');
    refreshDesktopControlLabels();
  }

  function refreshDesktopControlLabels() {
    const labels = {
      'monitor-start': 'start', 'monitor-stop': 'stop', 'monitor-restart': 'restart', 'monitor-reload': 'reload',
      'harness-start': 'start', 'harness-stop': 'stop', 'harness-restart': 'restart',
      build: 'build', 'build-restart': 'buildRestart', 'build-exe': 'buildExe', devtools: 'devtools', logs: 'logs',
    };
    Object.entries(labels).forEach(([action, key]) => {
      const node = document.querySelector(`[data-control="${action}"]`);
      if (node) node.textContent = tr(key);
    });
    const summary = document.querySelector('.developer-controls > summary');
    if (summary) summary.textContent = tr('developer');
  }

  function installKeyboardSend() {
    document.addEventListener('keydown', (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return;
      const target = event.target;
      if (target === byId('answer')) {
        event.preventDefault();
        if (!target.disabled && target.value.trim()) respond(target.value.trim());
      } else if (target === byId('teachMessage')) {
        event.preventDefault();
        sendTeaching();
      } else if (target.matches?.('.history-answer')) {
        event.preventDefault();
        const qid = target.dataset.qid;
        document.querySelector(`.history-send[data-qid="${CSS.escape(qid)}"]`)?.click();
      }
    });
  }

  function renderImageLog(force = false) {
    const root = byId('imageLog');
    if (!root || typeof state === 'undefined') return;
    const items = [...(state?.preview_events || [])].reverse();
    const signature = `${language}:${items.map((item) => item.event_id).join(',')}`;
    if (!force && signature === imageSignature) return;
    imageSignature = signature;
    if (!items.length) {
      root.innerHTML = `<div class="empty">${escapeHtml(tr('noImages'))}</div>`;
      return;
    }
    root.innerHTML = items.map((item) => `<button type="button" class="image-log-item" data-event-id="${escapeHtml(item.event_id)}"><img loading="lazy" src="/api/preview/${encodeURIComponent(item.event_id)}" alt="${escapeHtml(item.summary || item.type || 'capture')}"><span class="image-log-copy"><strong>${escapeHtml(item.type || 'capture')}</strong><small>${escapeHtml(`${formatTime(item.at)} · ${item.summary || ''}`)}</small></span></button>`).join('');
    root.querySelectorAll('.image-log-item').forEach((button) => button.addEventListener('click', () => {
      const item = items.find((candidate) => candidate.event_id === button.dataset.eventId);
      if (!item) return;
      const full = typeof events !== 'undefined' ? events.find((event) => event.event_id === item.event_id) : null;
      setPreview(item.event_id, full || { data: { image_profile: item.image_profile, image_bytes: item.image_bytes } });
      document.querySelector('.preview-region')?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }));
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function formatTime(value) {
    try { return new Date(value).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return '--:--:--'; }
  }

  function decorateFailure() {
    if (typeof state === 'undefined') return;
    const root = byId('failure');
    const failure = state?.latest_failure;
    if (!root || !failure) return;
    const text = JSON.stringify(failure);
    let message = '';
    if (text.includes('STALE_OBSERVATION')) message = tr('stale');
    else if (text.includes('PAUSE_REPLAN_REQUIRED')) message = tr('pauseReplan');
    if (!message) return;
    if ((failure.phase || failure.data?.phase) === 'preflight') message += ` ${tr('preflight')}`;
    let help = root.querySelector('.failure-help');
    if (!help) {
      help = document.createElement('div');
      help.className = 'failure-help';
      root.append(help);
    }
    help.innerHTML = `<strong>${escapeHtml(text.includes('STALE_OBSERVATION') ? 'STALE_OBSERVATION' : 'PAUSE_REPLAN_REQUIRED')}</strong><br>${escapeHtml(message)}`;
  }

  function ensureDockRoot() {
    let root = byId('desktopDock');
    if (root) return root;
    root = document.createElement('div');
    root.id = 'desktopDock';
    root.className = 'dockview-theme-dark';
    byId('monitor').after(root);
    return root;
  }

  function createRenderer(options) {
    const def = panelDefs.find((item) => item.id === options.name);
    const element = document.createElement('div');
    element.className = 'dock-panel-host';
    element.style.width = '100%';
    element.style.height = '100%';
    return {
      element,
      init() {
        const section = def && document.querySelector(def.selector);
        if (section) element.append(section);
      },
    };
  }

  function addPanel(api, def, position) {
    const panel = api.addPanel({
      id: def.id,
      component: def.id,
      title: def.title,
      renderer: 'always',
      minimumWidth: 220,
      minimumHeight: 100,
      ...(position ? { position } : {}),
    });
    panelById.set(def.id, panel);
    return panel;
  }

  function buildDefaultDock(api) {
    panelById.clear();
    addPanel(api, panelDefs.find((item) => item.id === 'preview'));
    addPanel(api, panelDefs.find((item) => item.id === 'decision'), { referencePanel: 'preview', direction: 'left' });
    addPanel(api, panelDefs.find((item) => item.id === 'teaching'), { referencePanel: 'preview', direction: 'right' });
    addPanel(api, panelDefs.find((item) => item.id === 'timeline'), { referencePanel: 'preview', direction: 'below' });
    addPanel(api, panelDefs.find((item) => item.id === 'images'), { referencePanel: 'preview' });
    addPanel(api, panelDefs.find((item) => item.id === 'runtime'), { referencePanel: 'decision', direction: 'below' });
    addPanel(api, panelDefs.find((item) => item.id === 'performance'), { referencePanel: 'runtime' });
    addPanel(api, panelDefs.find((item) => item.id === 'failure'), { referencePanel: 'runtime', direction: 'below' });
    addPanel(api, panelDefs.find((item) => item.id === 'detail'), { referencePanel: 'failure' });
    addPanel(api, panelDefs.find((item) => item.id === 'inbox'), { referencePanel: 'teaching' });
  }

  function initDock() {
    if (dockApi || !isDesktopViewport() || !window.dockview?.createDockview) return;
    const root = ensureDockRoot();
    document.body.classList.add('desktop-dock');
    dockApi = window.dockview.createDockview(root, {
      createComponent: createRenderer,
      defaultRenderer: 'always',
      floatingGroupBounds: { minimumHeightWithinViewport: 80, minimumWidthWithinViewport: 120 },
    });
    let restored = false;
    try {
      const saved = localStorage.getItem(layoutKey);
      if (saved) {
        dockApi.fromJSON(JSON.parse(saved));
        restored = panelDefs.every((def) => dockApi.getPanel(def.id));
      }
    } catch {
      restored = false;
    }
    if (!restored) {
      restoreSections();
      dockApi.clear();
      buildDefaultDock(dockApi);
    }
    panelDefs.forEach((def) => panelById.set(def.id, dockApi.getPanel(def.id)));
    dockLayoutDisposable = dockApi.onDidLayoutChange(() => persistDockLayout());
    dockPanelDragDisposable = dockApi.onWillDragPanel((event) => {
      if (pinnedPanels.has(event.panel.id)) event.nativeEvent.preventDefault();
    });
    dockGroupDragDisposable = dockApi.onWillDragGroup((event) => {
      if (event.group.panels?.some((panel) => pinnedPanels.has(panel.id))) event.nativeEvent.preventDefault();
    });
    applyPinnedState();
    persistDockLayout();
  }

  function persistDockLayout() {
    if (!dockApi) return;
    try { localStorage.setItem(layoutKey, JSON.stringify(dockApi.toJSON())); } catch {}
  }

  function restoreSections() {
    const monitor = byId('monitor');
    const firstSplitter = monitor.querySelector('.grid-splitter');
    for (const def of panelDefs) {
      const section = document.querySelector(def.selector);
      if (section) monitor.insertBefore(section, firstSplitter || null);
    }
  }

  function disposeDock() {
    if (!dockApi) return;
    restoreSections();
    dockLayoutDisposable?.dispose?.();
    dockLayoutDisposable = null;
    dockPanelDragDisposable?.dispose?.();
    dockPanelDragDisposable = null;
    dockGroupDragDisposable?.dispose?.();
    dockGroupDragDisposable = null;
    dockApi.dispose();
    dockApi = null;
    panelById.clear();
    ensureDockRoot().replaceChildren();
    document.body.classList.remove('desktop-dock');
    requestAnimationFrame(() => { if (typeof positionSplitters === 'function') positionSplitters(); });
  }

  function resetDockLayout() {
    localStorage.removeItem(layoutKey);
    if (!isDesktopViewport()) return;
    if (dockApi) {
      restoreSections();
      dockApi.clear();
      buildDefaultDock(dockApi);
      persistDockLayout();
    } else initDock();
  }

  function handleViewportChange() {
    if (isDesktopViewport()) initDock(); else disposeDock();
  }

  function wrapRenderers() {
    if (typeof failure === 'function') {
      const baseFailure = failure;
      failure = function () { baseFailure(); decorateFailure(); };
    }
    if (typeof teaching === 'function') {
      const baseTeaching = teaching;
      teaching = function () {
        baseTeaching();
        if (!state?.pending && language === 'ja') byId('question').textContent = '未回答の質問はありません';
      };
    }
  }

  function initialize() {
    createImageLogRegion();
    createToolbar();
    installInfoAndPanelActions();
    installKeyboardSend();
    wrapRenderers();
    applyLanguage();
    desktopMedia.addEventListener('change', handleViewportChange);
    window.addEventListener('resize', handleViewportChange);
    if (typeof ResizeObserver === 'function') {
      viewportObserver = new ResizeObserver(handleViewportChange);
      viewportObserver.observe(document.documentElement);
    }
    handleViewportChange();
    window.setInterval(handleViewportChange, 250);
    window.setInterval(() => {
      renderImageLog();
      decorateFailure();
    }, 700);
  }

  initialize();
})();
