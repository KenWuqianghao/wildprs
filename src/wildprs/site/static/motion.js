(() => {
  'use strict';
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const VIDEO = new URLSearchParams(location.search).has('video');
  const DISTANCE = new Set(['lifetime', 'biggest-day', 'biggest-week', 'sea-crossing']);
  const SEGMENTS = new Set(['strait-of-gibraltar', 'bosphorus-to-sinai', 'mediterranean', 'alps', 'tropics-of-africa', 'sahara']);
  const POINT_LIKE = new Set(['farthest-north', 'farthest-south', 'highest-flight']);
  const css = getComputedStyle(document.documentElement);
  const C = Object.fromEntries(['track', 'paint', 'paper', 'ink', 'mute'].map(k => [k, css.getPropertyValue(`--${k}`).trim()]));
  const DISPLAY = css.getPropertyValue('--display').trim(), TEXT = css.getPropertyValue('--text').trim();
  const REPLAY_MS = 10000, RACE_MS = 11000;
  const FS = VIDEO ? 1.5 : 1;
  const TOP = 1.0;
  const TAU = Math.PI * 2;
  const STILL = 0.25;
  const CROP = VIDEO ? 56 : 0;
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));

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

  /* ---------- track sampling ---------- */
  function locate(t, tau) {           // index i and fraction f with t[i] <= tau <= t[i+1]
    let lo = 0, hi = t.length - 1;
    if (tau <= t[0]) return [0, 0];
    if (tau >= t[hi]) return [hi, 0];
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (t[m] <= tau) lo = m; else hi = m; }
    return [lo, (tau - t[lo]) / Math.max(t[lo + 1] - t[lo], 1e-9)];
  }
  const lerp = (a, i, f) => f && i + 1 < a.length && a[i + 1] != null && a[i] != null ? a[i] + (a[i + 1] - a[i]) * f : a[i];

  function headings(pos, n = 240) {
    const P = Array.from({ length: n + 1 }, (_, k) => pos(k / n));
    const raw = P.map((_, k) => {
      for (const w of [3, 8, 24, 72]) {
        const a = P[Math.max(0, k - w)], b = P[Math.min(n, k + w)];
        if (Math.hypot(b[0] - a[0], b[1] - a[1]) > 1.5) return Math.atan2((a[1] - b[1]) / Math.sin(TOP), b[0] - a[0]);
      }
      return null;
    });
    let last = raw.find(a => a != null) ?? 0;
    const yaw = raw.map(a => (last = a == null ? last : a + Math.round((last - a) / TAU) * TAU));
    const soft = yaw.map((_, k) => { const s = yaw.slice(Math.max(0, k - 2), k + 3); return s.reduce((x, y) => x + y) / s.length; });
    return u => { const x = clamp(u) * n, k = Math.min(n - 1, Math.floor(x)); return soft[k] + (soft[k + 1] - soft[k]) * (x - k); };
  }

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
    seek(p) { this.pause(); this.p = clamp(p); if (this.state === 'idle' || this.state === 'ended') this.set('paused'); this.draw(this.p); }
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

  function scene(root, stage, after, ms, draw) {
    const cv = document.createElement('canvas'); cv.className = 'route';
    after.after(cv);
    const paint = p => draw(fitCanvas(cv, stage.clientWidth, stage.clientHeight), stage.clientWidth, stage.clientHeight, p);
    root.classList.add('live');
    const clock = new Clock(ms, paint);
    paint(1);
    if (!VIDEO) new ResizeObserver(() => paint(clock.p)).observe(stage);
    wireControls(root, clock);
    return { clock, draw: paint };
  }

  function fitCanvas(cv, w, h, zoom = 1) {
    const dpr = (VIDEO ? 1 : Math.min(window.devicePixelRatio || 1, 2)) * zoom;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    g.lineJoin = g.lineCap = 'round'; g.textBaseline = 'middle';
    return g;
  }
  const fonts = () => Promise.all([document.fonts.load(`900 16px ${DISPLAY}`), document.fonts.load(`700 16px ${TEXT}`)]);

  function dot(g, x, y, r, fill, stroke, sw) {
    g.beginPath(); g.arc(x, y, r, 0, TAU);
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = sw; g.stroke(); }
  }
  function checker(g, x, y, cols, rows, q) {
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) { g.fillStyle = (r + c) % 2 ? C.paint : C.ink; g.fillRect(x + c * q, y + r * q, q, q); }
  }
  function fitText(g, text, max, fs, font) {
    g.font = font(fs);
    const w = g.measureText(text).width;
    if (w <= max) return text;
    g.font = font(fs * Math.max(.8, max / w));
    let s = text;
    while (g.measureText(s).width > max + .5 && s.length > 2) s = `${s.slice(0, -2).trimEnd()}…`;
    return s;
  }
  function chip(g, x, y, fs, { id, band, rank, mark }, measure) {
    const h = Math.round(fs * 1.5), pad = fs * .42, ty = y + fs * .07;
    g.font = `800 ${fs}px ${DISPLAY}`;
    const wRank = rank ? Math.max(h * .8, g.measureText(String(rank)).width + pad * 1.4) : 0, wId = g.measureText(id).width;
    const w = wRank + pad + wId + (mark ? pad + g.measureText(mark).width : 0) + pad;
    if (measure) return w;
    g.fillStyle = 'rgba(20,18,16,.22)'; g.fillRect(x + 1, y - h / 2 + 2, w, h);
    g.fillStyle = C.paper; g.fillRect(x, y - h / 2, w, h);
    g.fillStyle = band; g.fillRect(x, y - h / 2, rank ? wRank : w, rank ? h : Math.max(2, h * .2));
    g.strokeStyle = C.ink; g.lineWidth = 1; g.strokeRect(x + .5, y - h / 2 + .5, w - 1, h - 1);
    g.textAlign = 'center';
    if (rank) { g.fillStyle = C.paper; g.fillText(String(rank), x + wRank / 2, ty); }
    g.textAlign = 'left'; g.fillStyle = C.ink; g.fillText(id, x + wRank + pad, rank ? ty : ty + h * .08);
    if (mark) { g.fillStyle = C.track; g.fillText(mark, x + wRank + pad + wId + pad, rank ? ty : ty + h * .08); }
    return w;
  }
  const bold = px => `700 ${px}px ${TEXT}`;
  const shortId = id => id.length > 14 ? `${id.slice(0, 13)}…` : id;

  const hz = taxon => Zoo.species[taxon].hz || 1;
  function topModel(g, taxon, x, y, size, yaw, phase) {
    Zoo.draw(g, Zoo.build(taxon, phase), { fit: Zoo.bounds(taxon), x, y, size, yaw, pitch: TOP, shadow: Models.shadow(taxon, 'rgba(20,18,16,.2)', .35) });
  }
  function sideModel(g, taxon, noseX, floorY, midY, box, phase) {
    const fly = Models.flies(taxon), yaw = -.3, pitch = fly ? .34 : .16;
    const v = Zoo.view(taxon, yaw, pitch), s = Math.min(box[0] / (v.x1 - v.x0), box[1] / (v.y1 - v.y0));
    const w = (v.x1 - v.x0) * s, h = (v.y1 - v.y0) * s, y = fly ? midY : floorY - h / 2;
    const feet = Zoo.bounds(taxon).c[1] - (floorY - (y + (v.y0 + v.y1) / 2 * s)) / (Math.cos(pitch) * s);
    Zoo.draw(g, Zoo.build(taxon, phase), { name: taxon, box, x: noseX - w / 2, y, yaw, pitch, shadow: { col: 'rgba(46,10,0,.3)', y: fly ? feet : undefined } });
    return w;
  }

  /* ---------- record replay ---------- */
  function markStep(total) {
    for (let e = .001; e < 1e7; e *= 10) for (const m of [1, 2, 2.5, 5]) if (total / (m * e) <= 8) return m * e;
    return total;
  }
  async function initReplay(root) {
    const [tr] = await Promise.all([fetch(root.dataset.track).then(r => r.json()), fonts()]);
    const taxon = root.dataset.taxon;
    const stage = root.querySelector('.stage');
    const num = root.querySelector('.num'), numText = num.firstChild, unitEl = num.querySelector('.u');
    const finalUnit = unitEl.textContent;
    const cells = {}; root.querySelectorAll('.ro [data-k]').forEach(c => { cells[c.dataset.k] = c; });
    const finals = {}; for (const k in cells) finals[k] = cells[k].textContent;
    const sun = root.querySelector('.rsun'); if (sun) sun.hidden = false;
    const scrub = root.querySelector('.scrub'), cur = scrub?.querySelector('.cur');
    const T = tr.t, n = T.length, span = T[n - 1] - T[0];
    const dist = DISTANCE.has(tr.category), seg = SEGMENTS.has(tr.category);
    const [lon0, lat1, kx, sc] = tr.view;
    const U = T.map((_, i) => [(tr.lon[i] - lon0) * kx * sc, (lat1 - tr.lat[i]) * sc]);
    const at = tau => { const [i, f] = locate(T, tau); return f ? [U[i][0] + (U[i + 1][0] - U[i][0]) * f, U[i][1] + (U[i + 1][1] - U[i][1]) * f] : U[i]; };
    const yawAt = headings(u => at(T[0] + span * u));
    const moved = [0];
    for (let i = 0; i < n - 1; i++) moved.push(moved[i] + (U[i + 1][0] !== U[i][0] || U[i + 1][1] !== U[i][1] ? T[i + 1] - T[i] : 0));
    const along = dist ? tr.m : tr.km.map(v => v - tr.km[0]);
    const marks = [];
    if (!POINT_LIKE.has(tr.category) && along[n - 1] > 0) {
      const step = markStep(along[n - 1]);
      for (let km = step, i = 0; km <= along[n - 1] - step * .3; km += step) {
        while (along[i + 1] < km) i++;
        const f = (km - along[i]) / (along[i + 1] - along[i]), a = U[i], b = U[i + 1], l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
        marks.push({ km, x: a[0] + (b[0] - a[0]) * f, y: a[1] + (b[1] - a[1]) * f, nx: (a[1] - b[1]) / l, ny: (b[0] - a[0]) / l,
          label: group(km, step % 1 ? (step * 10 % 1 ? 2 : 1) : 0) + (marks.length ? '' : ' km') });
      }
      const out = marks.reduce((v, m) => v + m.nx * (m.x - 600) + m.ny * (m.y - 337), 0) < 0 ? -1 : 1;
      for (const m of marks) { m.nx *= out; m.ny *= out; }
    }
    const setCell = (key, text) => { const b = cells[key]; if (b && b.textContent !== text) b.textContent = text; };

    const ctl = scene(root, stage, stage.querySelector('.map'), REPLAY_MS, (g, W, H, p) => {
      const k = W / 1200, S = clamp(W / 760, .8, 1.25) * FS;
      const tau = T[0] + span * p, [i, f] = locate(T, tau);
      const head = at(tau).map(v => v * k), done = along[i] + ((along[i + 1] ?? along[i]) - along[i]) * f;
      const line = to => { g.beginPath(); g.moveTo(U[0][0] * k, U[0][1] * k); for (let q = 1; q <= to; q++) g.lineTo(U[q][0] * k, U[q][1] * k); };
      line(n - 1); g.strokeStyle = C.track; g.globalAlpha = .4; g.lineWidth = 3.5 * S; g.setLineDash([.1, 8 * S]); g.stroke();
      g.setLineDash([]); g.globalAlpha = 1;
      line(i); g.lineTo(head[0], head[1]); g.lineWidth = 4.5 * S; g.stroke();

      g.font = `800 ${15 * S}px ${DISPLAY}`; g.textAlign = 'center';
      const taken = [];
      for (const m of marks) {
        const x = m.x * k, y = m.y * k, reached = m.km <= done;
        dot(g, x, y, 5 * S, reached ? C.track : C.paper, C.track, 2 * S);
        const w = g.measureText(m.label).width, h = 15 * S, off = 10 * S + Math.abs(m.nx) * w / 2 + Math.abs(m.ny) * h / 2;
        const lx = clamp(x + m.nx * off, w / 2 + 4, W - w / 2 - 4), ly = clamp(y + m.ny * off, h, H - h);
        if (taken.some(b => Math.abs(b[0] - lx) < (b[2] + w) / 2 + 4 && Math.abs(b[1] - ly) < h)) continue;
        taken.push([lx, ly, w]);
        g.strokeStyle = C.paper; g.lineWidth = 4 * S; g.strokeText(m.label, lx, ly);
        g.fillStyle = C.ink; g.fillText(m.label, lx, ly);
      }
      const a = [U[0][0] * k, U[0][1] * k], z = [U[n - 1][0] * k, U[n - 1][1] * k], q = 5 * S;
      if (tr.coarse) {
        g.setLineDash([4 * S, 5 * S]);
        for (const e of [a, z, head]) dot(g, e[0], e[1], Math.max(12, 22 * k) * FS, 'rgba(20,18,16,.06)', 'rgba(20,18,16,.7)', 1.5 * S);
        g.setLineDash([]);
      }
      dot(g, a[0], a[1], 8 * S, C.paper, C.ink, 3 * S); dot(g, a[0], a[1], 2.6 * S, C.ink);
      const flag = Math.max(2, z[1] - 26 * S);
      g.fillStyle = C.ink; g.fillRect(z[0] - 1.5 * S, flag, 3 * S, z[1] - flag);
      checker(g, z[0] + 1.5 * S, flag, 4, 3, q);
      g.strokeStyle = C.ink; g.lineWidth = 1; g.strokeRect(z[0] + 1.5 * S, flag, 4 * q, 3 * q);

      const phase = REDUCED ? STILL : hz(taxon) * lerp(moved, i, f) / span * REPLAY_MS / 1000;
      topModel(g, taxon, head[0], head[1], clamp(W * .058, 30, 56) * (VIDEO ? 1.3 : 1), yawAt(p), phase);

      if (p >= 1) {
        numText.nodeValue = root.dataset.final;
        unitEl.textContent = finalUnit;
        for (const key in cells) if (key !== 'sun') setCell(key, finals[key]);
      } else {
        const v = lerp(tr.m, i, f);
        numText.nodeValue = fmtValue(tr.unit, v, finalUnit);
        if (tr.unit === 'count') unitEl.textContent = Math.round(v) === 1 ? 'country' : 'countries';
        const el = tau - tr.t_zero;
        setCell('time', fmtClock(seg ? clamp(el, 0, tr.value * 3600) : el));
        setCell('dist', fmtKm(Math.max(0, dist ? v : lerp(tr.km, i, f) - tr.km[0])));
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
    });

    if (scrub) {
      const { clock } = ctl;
      scrub.setAttribute('role', 'slider'); scrub.tabIndex = 0;
      scrub.setAttribute('aria-label', 'Replay position, percent');
      scrub.setAttribute('aria-valuemin', '0'); scrub.setAttribute('aria-valuemax', '100');
      const seek = e => { const r = scrub.getBoundingClientRect(); clock.seek((e.clientX - r.left) / r.width); };
      scrub.addEventListener('pointerdown', e => { scrub.setPointerCapture(e.pointerId); seek(e); });
      scrub.addEventListener('pointermove', e => { if (scrub.hasPointerCapture(e.pointerId)) seek(e); });
      scrub.addEventListener('keydown', e => {
        const step = { ArrowLeft: -.02, ArrowRight: .02, Home: -1, End: 1 }[e.key];
        if (step !== undefined) { e.preventDefault(); clock.seek(clock.p + step); }
      });
    }
    autostart(root, ctl.clock);
    return ctl;
  }

  const racePhase = (R, rn, tau) => REDUCED ? STILL : hz(rn.taxon) * Math.min(tau, rn.finish) / R.finish * RACE_MS / 1000 + rn.rank * .37;
  const raceAt = (rn, tau) => locate(rn.t, Math.min(tau, rn.finish));

  function mapRace(R) {
    const yawOf = new Map(R.runners.map(rn => [rn.id, headings(u => { const [i, f] = raceAt(rn, rn.finish * u); return [lerp(rn.x, i, f), lerp(rn.y, i, f)]; })]));
    const order = [...R.runners].reverse();
    return (g, W, H, p) => {
      const k = W / 1200, S = clamp(W / 760, .8, 1.25) * FS, compact = W < 640;
      const tau = R.finish * p;
      const heads = order.map(rn => {
        const [i, f] = raceAt(rn, tau);
        return { rn, i, x: lerp(rn.x, i, f) * k, y: lerp(rn.y, i, f) * k, podium: rn.rank <= 3, fin: tau >= rn.finish };
      });
      g.strokeStyle = C.track;
      for (const h of heads) {
        g.beginPath(); g.moveTo(h.rn.x[0] * k, h.rn.y[0] * k);
        for (let q = 1; q <= h.i; q++) g.lineTo(h.rn.x[q] * k, h.rn.y[q] * k);
        g.lineTo(h.x, h.y);
        g.globalAlpha = h.podium ? .9 : .4; g.lineWidth = (h.podium ? 3 : 2) * S; g.stroke();
      }
      g.globalAlpha = 1;
      const big = clamp(W * .036, 18, 38) * FS;
      for (const h of heads) {
        h.size = h.podium ? big : big * .62;
        topModel(g, h.rn.taxon, h.x, h.y, h.size, yawOf.get(h.rn.id)(Math.min(tau, h.rn.finish) / h.rn.finish), racePhase(R, h.rn, tau));
      }
      if (p >= 1 && !compact) return results(g, W, H, R.runners);
      const labels = heads.filter(h => h.podium || !compact).map(h => {
        const fs = (h.podium ? 13 : 11) * S;
        const part = { id: shortId(h.rn.bib), band: h.rn.band, rank: h.podium ? h.rn.rank : 0, mark: h.podium && h.fin ? h.rn.display : '' };
        const w = chip(g, 0, 0, fs, part, true);
        const x = h.rn.rank % 2 ? h.x + h.size * .8 : h.x - h.size * .8 - w;
        return { part, fs, w, h: fs * 1.5, x: clamp(x, 4, W - w - 4), y: h.y - h.size * .7, rank: h.rn.rank };
      }).sort((a, b) => a.y - b.y);
      labels.forEach((l, q) => {
        for (const o of labels.slice(0, q)) if (l.x < o.x + o.w + 3 && o.x < l.x + l.w + 3) l.y = Math.max(l.y, o.y + (o.h + l.h) / 2 + 2);
        l.y = clamp(l.y, CROP + l.h, H - CROP - l.h);
      });
      for (const l of labels.sort((a, b) => b.rank - a.rank)) chip(g, l.x, l.y, l.fs, l.part);
    };
  }
  function results(g, W, H, runners) {
    const k = W / 1200, x = 30 * k, y = VIDEO ? CROP + 5 : 84 * k, w = (VIDEO ? 640 : 560) * k;
    const row = Math.min(54 * k * FS, (H - CROP - y - 22 * k) / runners.length), fs = clamp(row * .44, 10, 28);
    g.fillStyle = 'rgba(20,18,16,.3)'; g.fillRect(x + 3, y + 6, w, row * runners.length + 4);
    g.fillStyle = C.paper; g.fillRect(x, y, w, row * runners.length + 4);
    runners.forEach((rn, q) => {
      const cy = y + 2 + (q + .5) * row;
      if (q) { g.fillStyle = 'rgba(20,18,16,.16)'; g.fillRect(x + 12 * k, cy - row / 2, w - 24 * k, 1); }
      g.fillStyle = rn.rank === 1 ? C.track : C.ink; g.textAlign = 'center';
      g.font = `900 ${fs * 1.3}px ${DISPLAY}`; g.fillText(String(rn.rank), x + fs * 1.5, cy + fs * .08);
      g.font = `800 ${fs * 1.25}px ${DISPLAY}`; g.textAlign = 'right'; g.fillText(rn.display, x + w - fs * .9, cy + fs * .08);
      const mark = g.measureText(rn.display).width, cx = x + fs * 3;
      const cw = chip(g, cx, cy, fs * .85, { id: shortId(rn.bib), band: rn.band });
      g.textAlign = 'left'; g.fillStyle = C.ink;
      g.fillText(fitText(g, rn.name, x + w - fs * 1.6 - mark - (cx + cw + fs * .6), fs, bold), cx + cw + fs * .6, cy);
    });
  }

  function laneRace(R) {
    const n = R.runners.length;
    return (g, W, H, p) => {
      const k = W / 1200, compact = W < 640, tau = R.finish * p;
      const head = VIDEO ? 138 : compact ? 40 : Math.max(56, 80 * k), foot = VIDEO ? 50 : compact ? 22 : Math.max(26, 36 * k);
      const lh = Math.min((H - head - foot) / n, (H - head - foot) / 5), y1 = head + lh * n;
      const noW = compact ? 28 : Math.max(46, lh * .95), band = compact ? 12 : 30 * k * FS;
      const runH = compact ? lh - 19 : lh, box = [runH * (compact ? 2 : 2.1), runH * (compact ? .92 : 1.12)];
      const infoW = compact ? 0 : clamp(W * .24, 170, 310);
      const x0 = noW + infoW + box[0] + (compact ? 4 : 14), x1 = W - band - 4;
      const X = d => x0 + d / R.dmax * (x1 - x0);

      g.fillStyle = g.strokeStyle = C.paint;
      g.font = `600 ${compact ? 11 : 14 * Math.max(k, .9) * FS}px ${TEXT}`; g.textAlign = 'center';
      const marks = ticks(R.dmax).filter(d => d < R.dmax * .95);
      const upright = (x, w) => { for (let q = 0; q < n; q++) g.fillRect(x - w / 2, head + q * lh + lh - runH, w, runH); };
      marks.forEach((d, q) => {
        g.globalAlpha = .34; upright(X(d), 2); g.globalAlpha = 1;
        g.fillText(group(d, Number.isInteger(d) ? 0 : 1) + (q === marks.length - 1 ? ' km' : ''), X(d), y1 + foot * .5);
      });
      const lw = compact ? 2 : 3;
      for (let q = 0; q <= n; q++) g.fillRect(0, head + q * lh - lw / 2, W, lw);
      upright(x0, 4); g.fillRect(x1 - 2, head, 4, y1 - head);
      const cell = (W - x1 - 2) / 2;
      g.save(); g.beginPath(); g.rect(x1 + 2, head, W, y1 - head); g.clip();
      checker(g, x1 + 2, head, 2, Math.ceil((y1 - head) / cell), cell);
      g.restore();

      R.runners.forEach((rn, q) => {
        const top = head + q * lh, mid = top + lh / 2, fin = tau >= rn.finish;
        const [i, f] = raceAt(rn, tau), nose = X(lerp(rn.d, i, f));
        g.fillStyle = C.paint; g.textAlign = 'center';
        g.font = `900 ${lh * (compact ? .6 : .68)}px ${DISPLAY}`; g.fillText(String(rn.rank), noW / 2, mid + lh * .04);
        const ix = noW + (compact ? 6 : 12), fs = compact ? 11 : clamp(lh * (VIDEO ? .3 : .27), 12, 22);
        const part = { id: shortId(rn.bib), band: rn.band };
        if (compact) {
          const cw = chip(g, ix, top + 11, fs, part);
          g.font = `800 14px ${DISPLAY}`; g.fillStyle = C.paint; g.textAlign = 'right';
          const mw = fin ? g.measureText(rn.display).width + 8 : 0;
          if (fin) g.fillText(rn.display, x1 - 6, top + 12);
          g.textAlign = 'left';
          g.fillText(fitText(g, rn.name, x1 - 6 - mw - (ix + cw + 6), 12, bold), ix + cw + 6, top + 11);
        } else {
          g.textAlign = 'left';
          g.fillText(fitText(g, rn.name, infoW - 16, fs, bold), ix, mid - fs * .74);
          chip(g, ix, mid + fs * .74, fs * .92, part);
        }
        const floor = top + lh - Math.max(3, lh * .09);
        const w = sideModel(g, rn.taxon, nose, floor, compact ? floor - runH * .45 : mid, box, racePhase(R, rn, tau));
        if (fin && !compact) {
          g.font = `800 ${lh * .44}px ${DISPLAY}`; g.fillStyle = C.paint;
          const behind = nose - w - 12 - g.measureText(rn.display).width > x0 + 8;
          g.textAlign = behind ? 'right' : 'left';
          g.fillText(rn.display, behind ? nose - w - 12 : nose + 12, mid + lh * .03);
        }
      });
    };
  }
  function ticks(max) {
    const step = [1, 2, 5].flatMap(m => [.1, 1, 10, 100, 1000, 10000, 100000].map(e => m * e)).sort((a, b) => a - b).find(s => max / s <= 5);
    const out = []; for (let v = step; v <= max; v += step) out.push(v); return out;
  }

  async function initRace(root) {
    const [R] = await Promise.all([fetch(root.dataset.race).then(r => r.json()), fonts()]);
    const stage = root.querySelector('.race-stage'), clockEl = root.querySelector('.race-clock');
    const draw = (R.kind === 'map' ? mapRace : laneRace)(R);
    const ctl = scene(root, stage, stage.querySelector('svg'), RACE_MS, (g, W, H, p) => {
      clockEl.textContent = fmtClock(R.finish * p);
      draw(g, W, H, p);
    });
    autostart(root, ctl.clock, .5);
    return ctl;
  }

  const INTRO = .6, OUTRO = 2.6;
  function raceVideo(ctl) {
    const run = RACE_MS / 1000;
    return { duration: INTRO + run + OUTRO, frame: sec => ctl.draw(clamp((sec - INTRO) / run)) };
  }
  function replayVideo(el, ctl) {
    const run = REPLAY_MS / 1000, HOLD = .7, RISE = .45, CARD = 1.6;
    const card = el.querySelector('.tcard'), cv = card.querySelector('canvas[data-taxon]');
    const taxon = cv.dataset.taxon, o = Models.options(cv), zoom = card.getBoundingClientRect().width / card.offsetWidth;
    return {
      duration: INTRO + run + HOLD + RISE + CARD,
      frame(sec) {
        ctl.draw(clamp((sec - INTRO) / run));
        const t = sec - INTRO - run - HOLD, q = clamp(t / RISE);
        card.style.visibility = t > 0 ? 'visible' : 'hidden';
        card.style.translate = `0 ${((1 - q) ** 4 * innerHeight).toFixed(1)}px`;
        if (t <= 0) return;
        const w = cv.clientWidth, h = cv.clientHeight;
        Zoo.draw(fitCanvas(cv, w, h, zoom), Zoo.build(taxon, o.phase + t * hz(taxon)),
          { ...o, name: taxon, x: w / 2, y: h / 2, box: [w * o.pad, h * o.pad] });
      },
    };
  }

  async function main() {
    const replayEl = document.querySelector('.replay[data-track]');
    const raceEl = document.querySelector('.race[data-race]');
    if (VIDEO) {
      const el = replayEl || raceEl, parts = el.querySelector('template.vid');
      document.body.className = 'video';
      document.body.replaceChildren(el);
      if (replayEl && !parts) throw new Error('this record has no video');
      if (parts) el.append(parts.content);
      const ctl = await (replayEl ? initReplay : initRace)(el);
      window.wildprsVideo = replayEl ? replayVideo(el, ctl) : raceVideo(ctl);
      await document.fonts.ready;
      window.wildprsVideo.frame(0);
      window.wildprsReady = true;
      return;
    }
    if (replayEl) await initReplay(replayEl);
    if (raceEl) await initRace(raceEl);
  }
  main().catch(e => { console.error(e); if (VIDEO) window.wildprsError = String(e); });
})();
