(() => {
  'use strict';
  const Z = window.Zoo;
  const RAMP = '.:-=+*#%@';
  const SS = 4;
  const T_DIM = 0.36, T_HOT = 0.85, GAMMA = 0.92;
  const VIEW = {
    bird: { yaw: 1.35, pitch: 1.2, phase: 0 }, bat: { yaw: 1.4, pitch: 1.0, phase: 0.1 }, ray: { yaw: 1.2, pitch: 1.15, phase: 0.1 },
    turtle: { yaw: 0.4, pitch: 1.25, phase: 0.1 }, penguin: { yaw: -0.45, pitch: 0.6, phase: 0.1 },
    quad: { yaw: -0.5, pitch: 0.22, phase: 0.1 }, swim: { yaw: -0.45, pitch: 0.6, phase: 0.1 }, tortoise: { yaw: -0.6, pitch: 0.3, phase: 0.1 },
  };

  function ramp() {
    const c = document.createElement('canvas'); c.width = 30; c.height = 50;
    const g = c.getContext('2d', { willReadFrequently: true }); g.font = "600 34px 'Martian Mono'"; g.fillStyle = '#fff';
    const ink = [...RAMP].map(ch => { g.clearRect(0, 0, 30, 50); g.fillText(ch, 3, 38); const d = g.getImageData(0, 0, 30, 50).data; let s = 0; for (let i = 3; i < d.length; i += 4) s += d[i]; return [ch, s]; }).sort((a, b) => a[1] - b[1]).map(x => x[0]);
    return Array.from({ length: 64 }, (_, i) => (i ? ink[Math.min(ink.length - 1, Math.floor(((i - 1) / 63) * ink.length))] : ' '));
  }
  const luma = r => (c, lam, it) => { const l = (0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]) / 255, v = 255 * Math.min(1, (0.25 + 0.75 * lam) * (0.5 + 0.5 * l) * (0.8 + 0.2 * Z.clamp(0.5 + it.z / r))); return [v, v, v]; };

  function levels(g, cols, rows) {
    const W = cols * SS, d = g.getImageData(0, 0, W, rows * 2 * SS).data, n = SS * SS * 2;
    const lv = new Float32Array(cols * rows), edge = new Float32Array(cols * rows), hist = new Uint16Array(65);
    let count = 0;
    for (let r = 0, k = 0; r < rows; r++) for (let c = 0; c < cols; c++, k++) {
      let a = 0, v = 0;
      for (let y = 0; y < 2 * SS; y++) { let i = ((r * 2 * SS + y) * W + c * SS) * 4; for (let x = 0; x < SS; x++, i += 4) { a += d[i + 3]; v += d[i] * d[i + 3]; } }
      const cov = a / (n * 255);
      if (cov < 0.1) continue;
      const s = v / a / 255; lv[k] = s; edge[k] = Math.min(1, 0.3 + 1.2 * cov); hist[Math.min(64, (s * 64) | 0)]++; count++;
    }
    let acc = 0, p = 64; for (let i = 64; i >= 0; i--) { acc += hist[i]; if (acc >= count * 0.06) { p = i; break; } }
    const gain = Math.min(4, 0.97 / Math.max(0.05, (p + 0.5) / 64));
    for (let k = 0; k < lv.length; k++) if (lv[k] > 0) lv[k] = (0.27 + 0.73 * Math.min(1, lv[k] * gain)) * edge[k];
    return lv;
  }

  function drawView(host, lut) {
    const taxon = host.dataset.taxon, v = VIEW[Z.species[taxon].kind], st = getComputedStyle(host);
    const cw = parseFloat(st.getPropertyValue('--cw')), cols = Math.round(host.clientWidth / cw), rows = Math.round(host.clientHeight / (2 * cw));
    const cv = document.createElement('canvas'), W = (cv.width = cols * SS), H = (cv.height = rows * 2 * SS), g = cv.getContext('2d', { willReadFrequently: true });
    Z.draw(g, Z.build(taxon, v.phase), { name: taxon, x: W / 2, y: H / 2, box: [W * 0.96, H * 0.96], yaw: v.yaw, pitch: v.pitch, shader: luma(Z.bounds(taxon).r) });
    const lv = levels(g, cols, rows), out = [[], [], []];
    for (let r = 0, k = 0; r < rows; r++) {
      const line = ['', '', ''];
      for (let x = 0; x < cols; x++, k++) {
        const l = lv[k], tone = l <= 0 ? -1 : l < T_DIM ? 0 : l < T_HOT ? 1 : 2;
        for (let i = 0; i < 3; i++) line[i] += i === tone ? lut[Math.max(1, Math.round(Math.pow(l, GAMMA) * 63))] : ' ';
      }
      for (let i = 0; i < 3; i++) out[i].push(line[i]);
    }
    out.forEach((rowsOf, i) => { const pre = host.appendChild(document.createElement('pre')); pre.className = 'g' + (i + 1); pre.textContent = rowsOf.join('\n'); });
  }

  document.fonts.load("600 34px 'Martian Mono'").then(() => {
    const lut = ramp();
    for (const host of document.querySelectorAll('.view[data-taxon]')) drawView(host, lut);
    requestAnimationFrame(() => (document.documentElement.dataset.models = 'ready'));
  });
})();
