// ==UserScript==
// @name         Yuki Desktop Agent - Eye
// @namespace    yuki-agent
// @version      0.3
// @description  扫描页面交互元素，通过 HTTP+SSE 与 Yuki 后端通信
// @match        *://*/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const BACKEND = 'http://127.0.0.1:8766';
  const SCAN_INTERVAL = 3000;
  const MAX_ELEMENTS = 80;

  let lastScanHash = '';
  let scanThrottle = null;
  let statusBadge = null;
  let sseSource = null;

  // ==================== 状态指示器 ====================

  function createStatusBadge() {
    if (statusBadge) return;
    const badge = document.createElement('div');
    badge.style.cssText = `
      position: fixed; top: 8px; right: 8px; z-index: 999999;
      padding: 4px 10px; border-radius: 12px;
      font: bold 11px/1.4 'Consolas','Monaco',monospace;
      color: #fff; background: #666;
      pointer-events: none; opacity: 0.7;
      transition: background 0.3s;
    `;
    badge.textContent = '● YUKI:OFFLINE';
    document.body.appendChild(badge);
    statusBadge = badge;
  }

  function setStatus(color, text) {
    createStatusBadge();
    const colors = { green: '#2d8', red: '#d44', gray: '#666', blue: '#48d' };
    statusBadge.style.background = colors[color] || '#666';
    statusBadge.textContent = '● ' + text;
  }

  // ==================== SSE 连接（接收指令） ====================

  function connectSSE() {
    if (sseSource) {
      sseSource.close();
    }

    console.log('[Yuki-Eye] Connecting SSE to', BACKEND + '/events');
    sseSource = new EventSource(BACKEND + '/events');

    sseSource.onopen = () => {
      console.log('[Yuki-Eye] ✅ SSE connected');
      setStatus('green', 'YUKI:ONLINE');
    };

    sseSource.onmessage = (event) => {
      console.log('[Yuki-Eye] SSE ←', event.data.slice(0, 200));
      try {
        const msg = JSON.parse(event.data);
        handleCommand(msg);
      } catch (e) {
        console.warn('[Yuki-Eye] Bad SSE message:', e);
      }
    };

    sseSource.onerror = () => {
      console.warn('[Yuki-Eye] SSE disconnected, will retry...');
      setStatus('red', 'YUKI:OFFLINE');
      // EventSource 自动重连
    };
  }

  // ==================== HTTP POST（发送数据） ====================

  function postJSON(path, data) {
    const url = BACKEND + path;
    const body = JSON.stringify(data);
    console.log('[Yuki-Eye] POST', path, body.slice(0, 150));

    // 用 sendBeacon + fetch 双保险
    try {
      // sendBeacon 在页面卸载时也能发送
      const blob = new Blob([body], { type: 'application/json' });
      navigator.sendBeacon(url, blob);
    } catch {
      // sendBeacon 不支持时 fallback
    }

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
    }).then(res => {
      console.log('[Yuki-Eye] POST response:', res.status);
    }).catch(err => {
      console.warn('[Yuki-Eye] POST failed:', err.message);
    });
  }

  // ==================== DOM 扫描 ====================

  function scanElements() {
    const selectors = [
      'a[href]', 'button', 'input', 'select', 'textarea',
      '[role="button"]', '[role="link"]', '[role="tab"]',
      '[role="menuitem"]', '[role="combobox"]', '[role="listbox"]',
      '[onclick]', '[tabindex]:not([tabindex="-1"])',
      '[data-testid]', '[data-cy]', '[data-qa]',
      'summary',
    ].join(', ');

    const candidates = document.querySelectorAll(selectors);
    const elements = [];
    const seen = new Set();

    for (const el of candidates) {
      if (!isVisible(el)) continue;

      const rect = el.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) continue;

      const fp = `${el.tagName}|${(el.innerText||'').slice(0,40).trim()}|${Math.round(rect.x)}|${Math.round(rect.y)}`;
      if (seen.has(fp)) continue;
      seen.add(fp);

      const selector = generateSelector(el);
      if (!selector) continue;

      elements.push({
        id: elements.length,
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        text: cleanText(el).slice(0, 60),
        placeholder: el.placeholder || null,
        href: el.tagName === 'A' ? (el.href || '').slice(0, 120) : null,
        selector: selector,
        rect: {
          x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
        },
        state: getElementState(el),
      });

      if (elements.length >= MAX_ELEMENTS) break;
    }

    return elements;
  }

  function isVisible(el) {
    if (!el.offsetParent && el.tagName !== 'BODY' && el.tagName !== 'HTML') {
      const st = getComputedStyle(el);
      if (st.position !== 'fixed' && st.position !== 'sticky') return false;
    }
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    if (parseFloat(st.opacity) < 0.05) return false;
    return true;
  }

  function generateSelector(el) {
    // id
    if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) {
      if (document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
        return '#' + CSS.escape(el.id);
      }
    }
    // data-testid
    for (const attr of ['data-testid', 'data-cy', 'data-qa']) {
      const val = el.getAttribute(attr);
      if (val) return `[${attr}="${val}"]`;
    }
    // name in form
    if (el.name) {
      const scope = el.closest('form') || document;
      if (scope.querySelectorAll('[name="' + el.name + '"]').length === 1) {
        return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
      }
    }
    // aria-label
    if (el.getAttribute('aria-label')) {
      const sel = el.tagName.toLowerCase() + '[aria-label="' + el.getAttribute('aria-label') + '"]';
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    // nth-child path
    const path = [];
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      let seg = cur.tagName.toLowerCase();
      if (cur.className && typeof cur.className === 'string') {
        const cls = cur.className.trim().split(/\s+/)
          .filter(c => /^[a-zA-Z_-]/.test(c) && c.length < 30 && !/^sc-/.test(c))
          .slice(0, 2);
        if (cls.length) seg += '.' + cls.join('.');
      }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) seg += ':nth-child(' + (sibs.indexOf(cur) + 1) + ')';
      }
      path.unshift(seg);
      if (document.querySelectorAll(path.join(' > ')).length === 1) break;
      cur = cur.parentElement;
    }
    return path.join(' > ') || null;
  }

  function getElementState(el) {
    const s = [];
    if (el.disabled) s.push('disabled');
    if (el.readOnly) s.push('readonly');
    if (el === document.activeElement) s.push('focused');
    if (el.checked) s.push('checked');
    return s.length ? s : null;
  }

  function cleanText(el) {
    let t = el.innerText || el.value || el.textContent || '';
    return t.replace(/[\n\r\t]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  // ==================== 快照 ====================

  function buildSnapshot(trigger) {
    const elements = scanElements();
    const hash = JSON.stringify(elements.map(e => e.selector));

    if (trigger !== 'forced' && hash === lastScanHash) return null;
    lastScanHash = hash;

    return {
      type: 'page_state',
      trigger: trigger,
      url: location.href,
      title: document.title,
      timestamp: Date.now(),
      element_count: elements.length,
      elements: elements,
    };
  }

  // ==================== 动作执行 ====================

  function handleCommand(msg) {
    switch (msg.type) {
      case 'scan':
        triggerScan('manual');
        break;
      case 'action':
        executeAction(msg.action).then(ok => {
          if (ok) setTimeout(() => triggerScan('post_action'), 600);
        });
        break;
      case 'action_sequence':
        executeSequence(msg.actions);
        break;
    }
  }

  async function executeAction(action) {
    const el = resolveTarget(action.target);
    if (!el) {
      postJSON('/action_result', { ok: false, action: action.action, error: 'Element not found' });
      return false;
    }

    try {
      switch (action.action) {
        case 'click': {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          await sleep(250);
          el.focus();
          el.click();
          const rect = el.getBoundingClientRect();
          const cx = rect.x + rect.width / 2;
          const cy = rect.y + rect.height / 2;
          el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: cx, clientY: cy }));
          el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: cx, clientY: cy }));
          break;
        }
        case 'type':
          el.focus();
          el.value = '';
          for (const ch of action.text) {
            el.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
            el.value += ch;
            el.dispatchEvent(new InputEvent('input', { data: ch, inputType: 'insertText', bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keyup', { key: ch, bubbles: true }));
            await sleep(20 + Math.random() * 40);
          }
          el.dispatchEvent(new Event('change', { bubbles: true }));
          break;
        case 'clear':
          el.focus();
          el.value = '';
          el.dispatchEvent(new Event('change', { bubbles: true }));
          break;
        case 'select':
          el.value = action.value;
          el.dispatchEvent(new Event('change', { bubbles: true }));
          break;
        case 'scroll':
          (action.target === 'window' ? window : el).scrollBy(0, action.amount || 300);
          break;
        case 'hover':
          el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
          el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
          break;
        case 'focus':
          el.focus();
          break;
        case 'navigate':
          if (action.url) location.href = action.url;
          break;
        case 'wait':
          await sleep(action.ms || 1000);
          break;
        default:
          postJSON('/action_result', { ok: false, action: action.action, error: 'Unknown action' });
          return false;
      }

      setStatus('blue', 'YUKI:' + action.action.toUpperCase());
      postJSON('/action_result', { ok: true, action: action.action });
      return true;
    } catch (e) {
      postJSON('/action_result', { ok: false, action: action.action, error: e.message });
      return false;
    }
  }

  async function executeSequence(actions) {
    for (const action of actions) {
      const ok = await executeAction(action);
      if (!ok && action.critical !== false) break;
      await sleep(action.delay || 300);
    }
  }

  function resolveTarget(target) {
    if (typeof target === 'string') {
      try { return document.querySelector(target); } catch { return null; }
    }
    return null;
  }

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ==================== 扫描触发 ====================

  function triggerScan(trigger) {
    const snapshot = buildSnapshot(trigger);
    if (snapshot) {
      postJSON('/scan', snapshot);
      setStatus('blue', 'YUKI:SCAN:' + snapshot.element_count + 'els');
    }
  }

  function throttledScan(trigger) {
    if (scanThrottle) return;
    scanThrottle = setTimeout(() => {
      scanThrottle = null;
      triggerScan(trigger || 'dom_change');
    }, 800);
  }

  // ==================== 初始化 ====================

  createStatusBadge();
  setStatus('gray', 'YUKI:CONNECTING');

  // SSE 连接（接收指令）
  connectSSE();

  // 定时扫描
  setInterval(() => triggerScan('timer'), SCAN_INTERVAL);

  // DOM 变化
  const observer = new MutationObserver((mutations) => {
    const has = mutations.some(m =>
      m.type === 'childList' ||
      (m.type === 'attributes' && ['class', 'style', 'disabled', 'hidden'].includes(m.attributeName))
    );
    if (has) throttledScan('mutation');
  });
  observer.observe(document.body, {
    childList: true, subtree: true,
    attributes: true, attributeFilter: ['class', 'style', 'disabled', 'hidden'],
  });

  // 首次扫描
  setTimeout(() => triggerScan('initial'), 1500);
  console.log('[Yuki-Eye] 🚀 Initialized on', location.hostname);
})();
