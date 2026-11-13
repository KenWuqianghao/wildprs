/* Wild PRs motion: record replay, board race, home card routes, and the frame clock for videos.
   No dependencies. Without JS the pages show the static poster and the final numbers. */
(() => {
  'use strict';
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const VIDEO = new URLSearchParams(location.search).has('video');
  const DISTANCE = new Set(['lifetime', 'biggest-day', 'biggest-week', 'sea-crossing']);
  const SEGMENTS = new Set(['strait-of-gibraltar', 'bosphorus-to-sinai', 'mediterranean', 'alps', 'tropics-of-africa', 'sahara']);
  const C = { trk0: '#3fd0c9', trk1: '#ffc94a', night: '#0a111c', ink: '#f4f1ea', ink2: '#9aa6b8', line: '#2a3850' };
  const RANK = ['#ffc94a', '#e8edf4', '#f0a06a'];
  const REPLAY_MS = 10000, RACE_MS = 11000, CARD_MS = 1700;
  const FS = VIDEO ? 1.75 : 1;   // type and marks read in a small feed preview

  /* ---------- formatting: mirrors site.py ---------- */
  const group = (v, d = 0) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  function fmtClock(sec) {
    const sign = sec < 0 ? '−' : ''; sec = Math.abs(sec);
    const h = sec / 3600;
    if (h < 48) return `${sign}${Math.floor(h)}:${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')} h`;
    const d = h / 24;
    if (d < 60) return `${sign}${Math.floor(d)} d ${String(Math.floor(h % 24)).padStart(2, '0')} h`;
    if (d < 730) return `${sign}${group(d)} d`;
    return `${sign}${(d / 365.25).toFixed(1)} years`;
  }
  const fmtKm = km => km < 10 ? `${km.toFixed(1)} km` : `${group(km)} km`;
  function fmtValue(unit, v, finalUnit) {
    if (unit === 'deg') return `${Math.abs(v).toFixed(1)}°`;
    if (unit === 'h') return finalUnit === 'days' ? (v / 24).toFixed(1) : v.toFixed(1);
    if (unit === 'count' || unit === 'km/h') return String(Math.round(v));
    return group(v);
  }
  const ease = p => p < .5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2;

  /* ---------- track sampling ---------- */
  function locate(t, tau) {           // index i and fraction f with t[i] <= tau <= t[i+1]
    let lo = 0, hi = t.length - 1;
    if (tau <= t[0]) return [0, 0];
    if (tau >= t[hi]) return [hi, 0];
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (t[m] <= tau) lo = m; else hi = m; }
    return [lo, (tau - t[lo]) / Math.max(t[lo + 1] - t[lo], 1e-9)];
  }
  const lerp = (a, i, f) => f && i + 1 < a.length && a[i + 1] != null && a[i] != null ? a[i] + (a[i + 1] - a[i]) * f : a[i];

  /* ---------- clock: idle -> playing <-> paused -> ended; restart -> playing ---------- */
  class Clock {
    constructor(ms, draw) { this.ms = ms; this.draw = draw; this.p = 1; this.speed = 1; this.state = 'idle'; this.raf = 0; this.last = 0; this.listeners = []; }
    on(fn) { this.listeners.push(fn); }
    emit() { for (const fn of this.listeners) fn(this.state); }
    set(state) { this.state = state; this.emit(); }
    tick = now => {
      const dt = this.last ? now - this.last : 0; this.last = now;
      this.p = Math.min(1, this.p + dt * this.speed / this.ms);
      this.draw(this.p);
      if (this.p >= 1) { this.raf = 0; this.set('ended'); return; }
      this.raf = requestAnimationFrame(this.tick);
    };
    play() {
      if (this.state === 'playing') return;
      if (this.p >= 1) this.p = 0;
      this.last = 0; this.set('playing');
      if (!this.raf) this.raf = requestAnimationFrame(this.tick);
    }
    pause() { if (this.state !== 'playing') return; cancelAnimationFrame(this.raf); this.raf = 0; this.set('paused'); }
    toggle() { this.state === 'playing' ? this.pause() : this.play(); }
    restart() { this.p = 0; this.draw(0); this.state = 'paused'; this.play(); }
    seek(p) { this.pause(); this.p = Math.max(0, Math.min(1, p)); if (this.state === 'idle' || this.state === 'ended') this.set('paused'); this.draw(this.p); }
  }

  function wireControls(root, clock) {
    const play = root.querySelector('.b-play'), restart = root.querySelector('.b-restart');
    root.querySelector('.rctl')?.classList.add('on');
    play?.addEventListener('click', () => clock.toggle());
    restart?.addEventListener('click', () => clock.restart());
    root.querySelectorAll('.spd button').forEach(b => b.addEventListener('click', () => {
      clock.speed = +b.dataset.s;
      root.querySelectorAll('.spd button').forEach(o => o.setAttribute('aria-pressed', String(o === b)));
    }));
    clock.on(state => {
      const playing = state === 'playing';
      play?.classList.toggle('playing', playing);
      play?.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    });
  }

  function autostart(el, clock, threshold = .45) {
    if (REDUCED || VIDEO) return;
    const io = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting && clock.state === 'idle') { clock.play(); io.disconnect(); }
    }, { threshold });
    io.observe(el);
  }

  function fitCanvas(cv, w, h) {
    const dpr = Math.min(window.devicePixelRatio || 1, VIDEO ? 1 : 2);
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return g;
  }

  /* ---------- route drawing shared by replay and home cards ---------- */
  function projector(view, k) {      // map units of the 1200x675 poster -> CSS px
    const [lon0, lat1, kx, sc] = view;
    return (lon, lat) => [(lon - lon0) * kx * sc * k, (lat1 - lat) * sc * k];
  }
  function drawRoute(g, P, upto, k, opt = {}) {   // P: [[x,y]...] up to and including the head point
    if (P.length < 2) return;
    const a = P[0], b = upto;
    const grad = g.createLinearGradient(a[0], a[1], opt.end ? opt.end[0] : b[0], opt.end ? opt.end[1] : b[1]);
    grad.addColorStop(0, C.trk0); grad.addColorStop(1, C.trk1);
    g.lineJoin = g.lineCap = 'round';
    g.beginPath(); g.moveTo(a[0], a[1]);
    for (let i = 1; i < P.length; i++) g.lineTo(P[i][0], P[i][1]);
    g.strokeStyle = grad;
    g.globalAlpha = .10; g.lineWidth = 26 * k; g.stroke();
    g.globalAlpha = .16; g.lineWidth = 13 * k; g.stroke();
    g.globalAlpha = 1; g.lineWidth = 5 * k; g.stroke();
  }
  function dot(g, x, y, r, fill, stroke, sw) {
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2);
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = sw; g.stroke(); }
  }

  /* ---------- record replay ---------- */
  async function initReplay(root) {
    const tr = await (await fetch(root.dataset.track)).json();
    const poster = root.querySelector('.poster');
    const num = poster.querySelector('.num');
    const numText = num.firstChild;
    const finalUnit = num.querySelector('.u')?.textContent;
    const cells = {}; root.querySelectorAll('.ro [data-k]').forEach(c => { cells[c.dataset.k] = c; });
    const finals = {}; for (const k in cells) finals[k] = cells[k].textContent;
    const sun = root.querySelector('.rsun'); if (sun) sun.hidden = false;
    const scrub = document.querySelector('.scrub'), cur = scrub?.querySelector('.cur');
    const cv = document.createElement('canvas'); cv.className = 'route';
    poster.querySelector('.map').after(cv);
    const T = tr.t, n = T.length, span = T[n - 1] - T[0];
    const [ea, eb] = tr.eff;
    const dist = DISTANCE.has(tr.category);
    const seg = SEGMENTS.has(tr.category);
    const trail = span * .06;
    let W = 0, k = 1, P = [], proj;
    const layout = () => {
      W = poster.clientWidth; k = W / 1200; proj = projector(tr.view, k);
      P = T.map((_, i) => proj(tr.lon[i], tr.lat[i]));
    };
    const draw = p => {
      if (poster.clientWidth !== W) layout();
      const g = fitCanvas(cv, W, W * 675 / 1200);
      g.clearRect(0, 0, W, W);
      const tau = T[0] + span * p;
      const [i, f] = locate(T, tau);
      const head = proj(lerp(tr.lon, i, f), lerp(tr.lat, i, f));
      const end = P[n - 1];
      drawRoute(g, [...P.slice(0, i + 1), head], head, k, { end });
      // bright fading trail just behind the head
      const [j] = locate(T, tau - trail);
      g.lineCap = 'round';
      for (let q = Math.max(j, 0); q <= i; q++) {
        const b = q === i ? head : P[q + 1], a = P[q];
        g.globalAlpha = .9 * Math.max(0, 1 - (tau - T[q]) / trail);
        g.strokeStyle = '#fffbe9'; g.lineWidth = 3 * k;
        g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke();
      }
      g.globalAlpha = 1;
      dot(g, P[0][0], P[0][1], 9 * k, C.night, C.trk0, 5 * k);
      if (tr.coarse) dot(g, head[0], head[1], 22 * k, 'rgba(255,255,255,.14)', 'rgba(255,255,255,.6)', 2 * k);
      dot(g, head[0], head[1], (p >= 1 ? 11 : 10) * k, C.trk1, C.night, 4 * k);
      if (p < 1) dot(g, head[0], head[1], (16 + 8 * ((tau / span * 6) % 1)) * k, null, 'rgba(255,201,74,.45)', 2 * k);
      // numbers
      if (p >= 1) {
        numText.nodeValue = root.dataset.final;
        for (const key in cells) if (key !== 'sun') setCell(key, finals[key]);
      } else {
        numText.nodeValue = fmtValue(tr.unit, lerp(tr.m, i, f), finalUnit);
        const el = tau - tr.t_zero;
        setCell('time', fmtClock(seg ? Math.max(0, Math.min(el, tr.value * 3600)) : el));
        setCell('dist', fmtKm(Math.max(0, dist ? lerp(tr.m, i, f) : lerp(tr.km, i, f) - tr.km[0])));
        setCell('speed', `${group(Math.max(0, lerp(tr.kmh, i, f)))} km/h`);
        if (tr.h) { const hv = lerp(tr.h, i, f); setCell('height', hv == null ? '-' : `${group(hv)} m`); }
      }
      if (sun) {
        const rel = tau - T[0];
        const night = tr.night.some(([a, b]) => rel >= a && rel <= b);
        sun.classList.toggle('night', night);
        setCell('sun', night ? 'Night' : 'Day');
      }
      if (cur) { cur.hidden = false; cur.style.left = `${p * 100}%`; }
      if (scrub) scrub.setAttribute('aria-valuenow', String(Math.round(p * 100)));
    };
    function setCell(key, text) { const b = cells[key]; if (b && b.textContent !== text) b.textContent = text; }

    root.classList.add('live');
    const clock = new Clock(REPLAY_MS, draw);
    layout(); draw(1);
    new ResizeObserver(() => { layout(); draw(clock.p); }).observe(poster);
    wireControls(root, clock);
    if (scrub) {
      scrub.setAttribute('role', 'slider'); scrub.tabIndex = 0;
      scrub.setAttribute('aria-label', 'Replay position, percent');
      scrub.setAttribute('aria-valuemin', '0'); scrub.setAttribute('aria-valuemax', '100');
      const at = e => { const r = scrub.getBoundingClientRect(); clock.seek((e.clientX - r.left) / r.width); };
      scrub.addEventListener('pointerdown', e => { scrub.setPointerCapture(e.pointerId); at(e); });
      scrub.addEventListener('pointermove', e => { if (scrub.hasPointerCapture(e.pointerId)) at(e); });
      scrub.addEventListener('keydown', e => {
        const step = { ArrowLeft: -.02, ArrowRight: .02, Home: -1, End: 1 }[e.key];
        if (step !== undefined) { e.preventDefault(); clock.seek(clock.p + step); }
      });
    }
    autostart(root, clock);
    return { clock, draw };
  }

  /* ---------- board race ---------- */
  async function initRace(root) {
    const R = await (await fetch(root.dataset.race)).json();
    const stage = root.querySelector('.race-stage'), clockEl = root.querySelector('.race-clock');
    const cv = document.createElement('canvas'); cv.className = 'route';
    stage.querySelector('svg').after(cv);
    const runners = R.runners;
    const colour = r => RANK[r.rank - 1] || C.trk0;
    const thumbs = R.kind === 'chart' ? [...document.querySelectorAll('.board .thumb')].map((svg, i) => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('r', '4.5'); c.setAttribute('class', 'tdot'); svg.append(c); return c;
    }) : [];
    const pos = (rn, tau) => {   // index, fraction at race time tau (clamped to the runner's finish)
      const t = Math.min(tau, rn.finish);
      return locate(rn.t, t);
    };
    const draw = p => {
      const W = stage.clientWidth, H = stage.clientHeight, k = W / 1200 * FS, ku = W / 1200;
      const compact = W < 640;
      const g = fitCanvas(cv, W, H);
      g.clearRect(0, 0, W, H);
      const tau = R.finish * p;
      clockEl.textContent = fmtClock(tau);
      g.font = `700 ${Math.max(10, 15 * k)}px WS, system-ui, sans-serif`;
      g.textBaseline = 'middle';
      const done = runners.filter(rn => tau >= rn.finish);
      if (R.kind === 'map') {
        for (const rn of [...runners].reverse()) {
          const [i, f] = pos(rn, tau);
          const hx = lerp(rn.x, i, f) * ku, hy = lerp(rn.y, i, f) * ku;
          g.beginPath(); g.moveTo(rn.x[0] * ku, rn.y[0] * ku);
          for (let q = 1; q <= i; q++) g.lineTo(rn.x[q] * ku, rn.y[q] * ku);
          g.lineTo(hx, hy);
          g.strokeStyle = colour(rn); g.globalAlpha = rn.rank <= 3 ? .85 : .5; g.lineWidth = (rn.rank <= 3 ? 3 : 2) * k;
          g.lineJoin = g.lineCap = 'round'; g.stroke(); g.globalAlpha = 1;
        }
        for (const rn of [...runners].reverse()) {
          const [i, f] = pos(rn, tau);
          const hx = lerp(rn.x, i, f) * ku, hy = lerp(rn.y, i, f) * ku;
          const r = Math.max(8, 13 * k);
          dot(g, hx, hy, r, colour(rn), C.night, 3 * k);
          g.fillStyle = C.night; g.textAlign = 'center';
          g.font = `900 ${Math.max(10, 14 * k)}px WS, system-ui, sans-serif`;
          g.fillText(String(rn.rank), hx, hy + .5);
        }
      } else {
        const [x0p, y0, x1p, y1p] = R.plot;
        const kx = W / 1200, ky = H / 675, y1 = y1p;
        const x0 = compact ? 46 / kx : x0p, x1 = compact ? 1200 - 34 / kx : x1p;
        const X = t => (x0 + t / R.finish * (x1 - x0)) * kx, Y = d => (y1 - d / R.dmax * (y1 - y0)) * ky;
        // axes and ticks
        g.strokeStyle = C.line; g.lineWidth = 1; g.fillStyle = C.ink2;
        g.font = `500 ${Math.max(10, 14 * k)}px WS, system-ui, sans-serif`;
        g.textAlign = 'right';
        for (const d of ticks(R.dmax)) {
          g.beginPath(); g.moveTo(x0 * kx, Y(d)); g.lineTo(x1 * kx, Y(d)); g.globalAlpha = .6; g.stroke(); g.globalAlpha = 1;
          g.fillText(group(d), x0 * kx - 8, Y(d));
        }
        g.beginPath(); g.moveTo(x0 * kx, y0 * ky); g.lineTo(x0 * kx, y1 * ky); g.lineTo(x1 * kx, y1 * ky); g.stroke();
        g.textAlign = 'center';
        for (const s of timeTicks(R.finish)) g.fillText(fmtClock(s).replace(':00 h', ' h'), X(s), y1 * ky + 12 + 8 * k);
        const heads = [];
        for (const rn of [...runners].reverse()) {
          const [i, f] = pos(rn, tau);
          g.beginPath();
          let started = false;
          for (let q = 0; q <= i; q++) { if (rn.t[q] < 0) continue; const x = X(rn.t[q]), y = Y(rn.d[q]); started ? g.lineTo(x, y) : g.moveTo(x, y); started = true; }
          const ht = Math.max(0, Math.min(tau, rn.finish)), hd = lerp(rn.d, i, f);
          started ? g.lineTo(X(ht), Y(hd)) : g.moveTo(X(ht), Y(hd));
          g.strokeStyle = colour(rn); g.globalAlpha = rn.rank <= 3 ? 1 : .55; g.lineWidth = (rn.rank <= 3 ? 3.2 : 2) * k;
          g.lineJoin = g.lineCap = 'round'; g.stroke(); g.globalAlpha = 1;
          heads.push({ rn, x: X(ht), y: Y(hd), d: hd });
        }
        for (const h of heads) dot(g, h.x, h.y, 5 * k, colour(h.rn), C.night, 2 * k);
        // labels at the heads, pushed apart so that they do not overlap
        heads.sort((a, b) => a.y - b.y);
        const fs = Math.max(compact ? 10 : 11, 14 * k), gap = fs * 1.35; let last = -1e9;
        for (const h of heads) { h.ly = Math.max(h.y, last + gap); last = h.ly; }
        const over = last - y1 * ky; if (over > 0) for (const h of heads) h.ly -= over;
        g.textAlign = 'left';
        for (const h of heads) {
          const fin = tau >= h.rn.finish;
          g.fillStyle = colour(h.rn); g.globalAlpha = h.rn.rank <= 3 || fin ? 1 : .75;
          g.font = `${h.rn.rank <= 3 ? 900 : 700} ${fs}px WS, system-ui, sans-serif`;
          const label = compact ? String(h.rn.rank) : `${h.rn.rank}  ${h.rn.name}  ${fin ? h.rn.display : fmtKmShort(h.d)}`;
          g.fillText(label, h.x + 9 * k, h.ly);
          g.globalAlpha = 1;
        }
        thumbs.forEach((c, idx) => {
          const rn = runners[idx]; if (!rn) return;
          const [i, f] = pos(rn, tau);
          c.setAttribute('cx', lerp(rn.x, i, f).toFixed(1)); c.setAttribute('cy', lerp(rn.y, i, f).toFixed(1));
        });
      }
      // map race: finish times beside the podium markers while racing, the standings when it ends
      if (R.kind === 'map' && p < 1) {
        g.textAlign = 'left'; g.textBaseline = 'middle';
        g.font = `900 ${Math.max(10, 15 * k)}px WS, system-ui, sans-serif`;
        for (const rn of done) if (rn.rank <= 3) {
          const [i, f] = pos(rn, tau);
          const hx = lerp(rn.x, i, f) * ku, hy = lerp(rn.y, i, f) * ku;
          g.lineWidth = 4 * k; g.strokeStyle = C.night; g.strokeText(rn.display, hx + 18 * k, hy);
          g.fillStyle = colour(rn); g.fillText(rn.display, hx + 18 * k, hy);
        }
      }
      if (R.kind === 'map' && p >= 1 && !compact) {
        g.fillStyle = 'rgba(10,17,28,.62)'; g.fillRect(0, 0, W, H);
        g.textAlign = 'left'; g.textBaseline = 'middle';
        const order = [...runners].sort((a, b) => a.rank - b.rank);
        const lh = Math.min(34 * k, (H - 230 * ku) / order.length), lx = 44 * ku, ly = (VIDEO ? 200 : 96) * ku;
        const fs = Math.max(10, Math.min(16 * k, lh * .6));
        order.forEach((rn, q) => {
          const y = ly + q * lh;
          dot(g, lx + fs * .7, y, fs * .7, colour(rn), null, 0);
          g.fillStyle = C.night; g.textAlign = 'center'; g.font = `900 ${fs * .8}px WS, system-ui, sans-serif`;
          g.fillText(String(rn.rank), lx + fs * .7, y + .5);
          g.textAlign = 'left'; g.fillStyle = C.ink; g.font = `700 ${fs}px WS, system-ui, sans-serif`;
          g.fillText(rn.name, lx + fs * 2, y);
          g.fillStyle = C.trk1; g.font = `900 ${fs}px WS, system-ui, sans-serif`;
          g.textAlign = 'right'; g.fillText(rn.display, lx + fs * 2 + 640 * ku * (VIDEO ? 1.15 : 1), y);
        });
      }
    };
    root.classList.add('live');
    const clock = new Clock(RACE_MS, draw);
    draw(1);
    new ResizeObserver(() => draw(clock.p)).observe(stage);
    wireControls(root, clock);
    autostart(root, clock, .5);
    return { clock, draw };
  }
  function fmtKmShort(d) { return `${group(d)} km`; }
  function ticks(max) {
    const step = [1, 2, 5].flatMap(m => [1, 10, 100, 1000, 10000, 100000].map(e => m * e)).sort((a, b) => a - b).find(s => max / s <= 5);
    const out = []; for (let v = step; v <= max; v += step) out.push(v); return out;
  }
  function timeTicks(sec) {
    const steps = [600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400, 30 * 86400, 91 * 86400, 182 * 86400, 365.25 * 86400];
    const step = steps.find(s => sec / s <= 6) || 365.25 * 86400;
    const out = []; for (let v = step; v <= sec; v += step) out.push(v); return out;
  }

  /* ---------- home cards ---------- */
  function initCards(cards) {
    if (REDUCED) return;
    const io = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting) { io.unobserve(e.target); drawCard(e.target); }
    }, { threshold: .35 });
    cards.forEach(c => io.observe(c));
  }
  async function drawCard(card) {
    const img = card.querySelector('img');
    const bare = new Image(); bare.src = card.dataset.bare;
    let tr;
    try { [tr] = await Promise.all([fetch(card.dataset.track).then(r => r.json()), bare.decode()]); } catch { return; }
    const cv = document.createElement('canvas'); cv.className = 'route';
    card.append(cv);
    img.src = bare.src;
    const n = tr.t.length, span = tr.t[n - 1] - tr.t[0];
    let start = 0;
    const frame = now => {
      start ||= now;
      const p = Math.min(1, (now - start) / CARD_MS), q = ease(p);
      const W = img.clientWidth, k = W / 1200, proj = projector(tr.view, k);
      const g = fitCanvas(cv, W, img.clientHeight);
      g.clearRect(0, 0, W, img.clientHeight);
      const tau = tr.t[0] + span * q;
      const [i, f] = locate(tr.t, tau);
      const P = tr.t.slice(0, i + 1).map((_, j) => proj(tr.lon[j], tr.lat[j]));
      const head = proj(lerp(tr.lon, i, f), lerp(tr.lat, i, f));
      P.push(head);
      drawRoute(g, P, head, k, { end: proj(tr.lon[n - 1], tr.lat[n - 1]) });
      if (!tr.coarse) dot(g, P[0][0], P[0][1], 9 * k, C.night, C.trk0, 5 * k);
      dot(g, head[0], head[1], 11 * k, C.trk1, C.night, 5 * k);
      if (p < 1) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  /* ---------- video mode: the capture script sets each frame's time ---------- */
  function videoMode(el, ctl, kind) {
    document.body.className = `video video-${kind}`;
    document.body.replaceChildren(el);
    const INTRO = 0.6, OUTRO = 2.6, RUN = kind === 'race' ? RACE_MS / 1000 : REPLAY_MS / 1000;
    window.wildprsVideo = {
      duration: INTRO + RUN + OUTRO,
      frame(sec) {
        const p = Math.max(0, Math.min(1, (sec - INTRO) / RUN));
        ctl.draw(p);
        const ro = el.querySelector('.ro');
        if (ro) ro.style.opacity = String(1 - Math.max(0, Math.min(1, (sec - INTRO - RUN - 0.5) / 0.5)));
      },
    };
  }

  async function main() {
    const replayEl = document.querySelector('.replay[data-track]');
    const raceEl = document.querySelector('.race[data-race]');
    if (replayEl) {
      const ctl = await initReplay(replayEl);
      if (VIDEO) videoMode(replayEl, ctl, 'replay');
    }
    if (raceEl) {
      if (VIDEO) { document.body.className = 'video video-race'; document.body.replaceChildren(raceEl); }
      const ctl = await initRace(raceEl);
      if (VIDEO) videoMode(raceEl, ctl, 'race');
    }
    const cards = document.querySelectorAll('.card[data-track]');
    if (cards.length) initCards(cards);
    if (VIDEO) { await document.fonts.ready; window.wildprsReady = true; }
  }
  main().catch(e => { console.error(e); if (VIDEO) window.wildprsError = String(e); });
})();
