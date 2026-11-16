(() => {
  const Z = (window.Zoo = { kinds: {}, species: {} });
  const TAU = Math.PI * 2;
  Z.TAU = TAU;
  Z.clock = null;
  if (typeof location !== "undefined") { const fz = new URLSearchParams(location.search).get("freeze"); if (fz != null) Z.clock = +fz; }
  Z.now = () => (Z.clock != null ? Z.clock : performance.now() / 1000);

  const V = (Z.V = {
    add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
    sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
    mul: (a, s) => [a[0] * s, a[1] * s, a[2] * s],
    dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
    cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
    len: (a) => Math.hypot(a[0], a[1], a[2]),
    norm: (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
    lerp: (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t],
  });

  const rgbCache = new Map();
  const rgb = (Z.rgb = (c) => {
    if (Array.isArray(c)) return c;
    let v = rgbCache.get(c);
    if (!v) {
      let h = c.slice(1);
      if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
      v = [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
      rgbCache.set(c, v);
    }
    return v;
  });
  const mixc = (Z.mixc = (a, b, t) => { a = rgb(a); b = rgb(b); return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; });
  const hash = (Z.hash = (i, seed = 0) => { const x = Math.sin(i * 127.1 + seed * 311.7) * 43758.5453; return x - Math.floor(x); });
  const lerp = (Z.lerp = (a, b, t) => a + (b - a) * t);
  const clamp = (Z.clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x)));
  const smooth = (Z.smooth = (t) => { t = clamp(t); return t * t * (3 - 2 * t); });
  Z.curve = (pts, x) => {
    if (x <= pts[0][0]) return pts[0][1];
    for (let i = 1; i < pts.length; i++) if (x <= pts[i][0]) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]; return y0 + (y1 - y0) * ((x - x0) / (x1 - x0 || 1)); }
    return pts[pts.length - 1][1];
  };

  const newell = (p) => {
    let nx = 0, ny = 0, nz = 0;
    for (let i = 0; i < p.length; i++) { const a = p[i], b = p[(i + 1) % p.length]; nx += (a[1] - b[1]) * (a[2] + b[2]); ny += (a[2] - b[2]) * (a[0] + b[0]); nz += (a[0] - b[0]) * (a[1] + b[1]); }
    return [nx, ny, nz];
  };
  const centroid = (p) => { let x = 0, y = 0, z = 0; for (const q of p) { x += q[0]; y += q[1]; z += q[2]; } return [x / p.length, y / p.length, z / p.length]; };


  Z.tube = (mesh, rings, o = {}) => {
    const n = o.n || 8, off = Math.PI / n + (o.rot || 0), cut = o.belly ?? -0.35, exp = o.exp || 1;
    const R = rings.length, pts = [];
    for (let i = 0; i < R; i++) {
      const r = rings[i], prev = rings[Math.max(0, i - 1)].c, next = rings[Math.min(R - 1, i + 1)].c;
      const t = V.norm(V.sub(next, prev));
      let A = r.A || (Math.abs(t[2]) > 0.92 ? [1, 0, 0] : [0, 0, 1]);
      A = V.norm(V.sub(A, V.mul(t, V.dot(A, t))));
      let B = V.cross(A, t); if (B[1] < -1e-6) B = [-B[0], -B[1], -B[2]];
      const row = [];
      for (let k = 0; k < n; k++) {
        const th = off + (TAU * k) / n; let cx = Math.cos(th), sy = Math.sin(th);
        if (exp !== 1) { cx = Math.sign(cx) * Math.abs(cx) ** exp; sy = Math.sign(sy) * Math.abs(sy) ** exp; }
        const h = sy < 0 && r.ryb != null ? r.ryb : r.ry;
        row.push([r.c[0] + A[0] * r.rx * cx + B[0] * h * sy, r.c[1] + A[1] * r.rx * cx + B[1] * h * sy, r.c[2] + A[2] * r.rx * cx + B[2] * h * sy]);
      }
      pts.push(row);
    }
    let fi = 0;
    const push = (p, col, axis) => {
      const q = p.filter((a, i) => { const b = p[(i + 1) % p.length]; return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) > 1e-9; });
      if (q.length < 3) return;
      const nn = newell(q), c = centroid(q);
      if (V.dot(nn, V.sub(c, axis)) < 0) q.reverse();
      let cc = rgb(col);
      if (o.mottle) { const h = hash(fi++, o.mottle.seed || 1); if (h < (o.mottle.amt ?? 0.4)) cc = mixc(cc, o.mottle.col, 0.35 + 0.65 * hash(fi, 7)); }
      mesh.push({ p: q, c: cc });
    };
    for (let i = 0; i < R - 1; i++) {
      const r = rings[i], axis = V.lerp(r.c, rings[i + 1].c, 0.5);
      for (let k = 0; k < n; k++) {
        const k2 = (k + 1) % n, mid = Math.sin(off + (TAU * (k + 0.5)) / n);
        const col = mid < cut && r.belly ? r.belly : r.col;
        push([pts[i][k], pts[i][k2], pts[i + 1][k2], pts[i + 1][k]], col, axis);
      }
    }
    if (o.cap0 !== false && rings[0].rx > 1e-6 && rings[0].ry > 1e-6) push(pts[0].slice(), rings[0].capCol || rings[0].col, rings[1].c);
    if (o.cap1 !== false && rings[R - 1].rx > 1e-6 && rings[R - 1].ry > 1e-6) push(pts[R - 1].slice(), rings[R - 1].capCol || rings[R - 2].col, rings[R - 2].c);
    return mesh;
  };

  Z.ball = (mesh, c, rx, ry, rz, col, o = {}) => {
    const m = o.m || 5, rings = [];
    for (let i = 0; i <= m; i++) { const a = (Math.PI * i) / m, s = Math.sin(a); rings.push({ c: [c[0] - rx * Math.cos(a), c[1], c[2]], rx: rz * s, ry: ry * s, col, belly: o.bellyCol }); }
    return Z.tube(mesh, rings, { n: o.n || 6, cap0: false, cap1: false, belly: o.belly, mottle: o.mottle });
  };

  Z.plate = (mesh, pts, col, back) => { const n = newell(pts); if (n[1] < -1e-9 || (Math.abs(n[1]) <= 1e-9 && n[2] < 0)) pts = pts.slice().reverse(); mesh.push({ p: pts, c: rgb(col), b: rgb(back ?? col) }); return mesh; };

  Z.strip = (mesh, ribs) => {
    for (let i = 0; i < ribs.length - 1; i++) {
      const a = ribs[i], b = ribs[i + 1], m = a.m ?? 0;
      if (m > 0) {
        const ma = V.lerp(a.l, a.t, m), mb = V.lerp(b.l, b.t, b.m ?? m);
        mesh.push({ p: [ma, mb, b.l, a.l], c: rgb(a.cf), b: rgb(a.bf ?? a.cf) });
        mesh.push({ p: [a.t, b.t, mb, ma], c: rgb(a.cr ?? a.cf), b: rgb(a.br ?? a.bf ?? a.cr ?? a.cf) });
      } else mesh.push({ p: [a.t, b.t, b.l, a.l], c: rgb(a.cf), b: rgb(a.bf ?? a.cf) });
    }
    return mesh;
  };

  Z.map = (faces, fn) => { for (const f of faces) f.p = f.p.map(fn); return faces; };
  Z.rotX = (a, piv = [0, 0, 0]) => { const c = Math.cos(a), s = Math.sin(a); return (p) => { const y = p[1] - piv[1], z = p[2] - piv[2]; return [p[0], piv[1] + y * c + z * s, piv[2] + z * c - y * s]; }; };
  Z.rotY = (a, piv = [0, 0, 0]) => { const c = Math.cos(a), s = Math.sin(a); return (p) => { const x = p[0] - piv[0], z = p[2] - piv[2]; return [piv[0] + x * c + z * s, p[1], piv[2] + z * c - x * s]; }; };
  Z.rotZ = (a, piv = [0, 0, 0]) => { const c = Math.cos(a), s = Math.sin(a); return (p) => { const x = p[0] - piv[0], y = p[1] - piv[1]; return [piv[0] + x * c - y * s, piv[1] + x * s + y * c, p[2]]; }; };
  Z.move = (d) => (p) => [p[0] + d[0], p[1] + d[1], p[2] + d[2]];
  Z.scale = (s) => (p) => [p[0] * s, p[1] * s, p[2] * s];
  Z.mirror = (faces) => faces.map((f) => ({ ...f, p: f.p.map((p) => [p[0], p[1], -p[2]]).reverse() }));
  Z.pair = (mesh, part) => { for (const f of part) mesh.push(f); for (const f of Z.mirror(part)) mesh.push(f); return mesh; };

  const boundsCache = new Map();
  Z.build = (name, t = 0) => {
    const sp = Z.species[name];
    if (!sp) throw new Error("no model for " + name);
    return Z.kinds[sp.kind](sp, ((t % 1) + 1) % 1);
  };
  Z.bounds = (name) => {
    let b = boundsCache.get(name);
    if (b) return b;
    let lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (const t of [0, 0.25, 0.5, 0.75]) for (const f of Z.build(name, t)) for (const p of f.p) for (let k = 0; k < 3; k++) { if (p[k] < lo[k]) lo[k] = p[k]; if (p[k] > hi[k]) hi[k] = p[k]; }
    const c = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    b = { lo, hi, c, r: Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2 };
    boundsCache.set(name, b);
    return b;
  };

  const LIGHT = V.norm([-0.35, 0.85, 0.45]);
  Z.shaders = {
    flat: (c, lam) => { const v = 0.52 + 0.6 * lam, k = 0.2 * (1 - lam); return [c[0] * v * (1 - k) + 38 * k, c[1] * v * (1 - k) + 46 * k, c[2] * v * (1 - k) + 92 * k]; },
    toon: (c, lam) => { const v = lam > 0.42 ? 1.06 : 0.74; return [c[0] * v, c[1] * v, c[2] * v]; },
    cut: (c) => c,
  };
  const viewCache = new Map();
  Z.view = (name, yaw, pitch = 0, roll = 0) => {
    const key = `${name}|${yaw == null ? "*" : Math.round(yaw * 30)}|${Math.round(pitch * 30)}|${Math.round(roll * 30)}`;
    let v = viewCache.get(key);
    if (v) return v;
    const c0 = Z.bounds(name).c, yaws = yaw == null ? Array.from({ length: 12 }, (_, i) => (i * TAU) / 12) : [yaw];
    const cp = Math.cos(pitch), sp = Math.sin(pitch), cr = Math.cos(roll), sr = Math.sin(roll);
    v = { x0: 1e9, x1: -1e9, y0: 1e9, y1: -1e9 };
    for (let k = 0; k < 8; k++) for (const f of Z.build(name, k / 8)) for (const p of f.p) {
      const x = p[0] - c0[0], y = p[1] - c0[1], z = p[2] - c0[2], y1 = y * cr + z * sr, z1 = z * cr - y * sr;
      for (const a of yaws) { const X = x * Math.cos(a) + z1 * Math.sin(a), z2 = z1 * Math.cos(a) - x * Math.sin(a), Y = y1 * cp - z2 * sp; if (X < v.x0) v.x0 = X; if (X > v.x1) v.x1 = X; if (Y < v.y0) v.y0 = Y; if (Y > v.y1) v.y1 = Y; }
    }
    if (yaw == null) { const r = Math.max(-v.x0, v.x1); v.x0 = -r; v.x1 = r; }
    viewCache.set(key, v);
    return v;
  };

  Z.draw = (g, faces, o) => {
    const yaw = o.yaw || 0, pitch = o.pitch || 0, roll = o.roll || 0;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch), cr = Math.cos(roll), sr = Math.sin(roll);
    const fit = o.fit || (o.name ? Z.bounds(o.name) : null), c0 = fit ? fit.c : [0, 0, 0];
    let s = fit && o.size ? o.size / fit.r : o.size, ox = o.x, oy = o.y;
    if (o.box) {
      const v = Z.view(o.name, o.anyYaw ? null : yaw, pitch, roll);
      s = Math.min(o.box[0] / (v.x1 - v.x0), o.box[1] / (v.y1 - v.y0)); ox -= ((v.x0 + v.x1) / 2) * s; oy += ((v.y0 + v.y1) / 2) * s;
    }
    const tf = (p) => {
      let x = p[0] - c0[0], y = p[1] - c0[1], z = p[2] - c0[2];
      let y1 = y * cr + z * sr, z1 = z * cr - y * sr;
      let x2 = x * cy + z1 * sy, z2 = z1 * cy - x * sy;
      let y3 = y1 * cp - z2 * sp, z3 = z2 * cp + y1 * sp;
      return [x2, y3, z3];
    };
    const L = [LIGHT[0], LIGHT[1] * cp - LIGHT[2] * sp, LIGHT[2] * cp + LIGHT[1] * sp];
    const shader = typeof o.shader === "function" ? o.shader : Z.shaders[o.shader || "flat"];
    const d = o.persp ? o.persp * (fit ? fit.r : 1) : 0;
    const items = [];
    for (const f of faces) {
      const q = f.p.map(tf);
      let n = newell(q), col = f.c;
      if (n[2] < 0) { if (!f.b) continue; n = [-n[0], -n[1], -n[2]]; col = f.b; }
      const l = Math.hypot(n[0], n[1], n[2]) || 1;
      const lam = Math.max(0, (n[0] * L[0] + n[1] * L[1] + n[2] * L[2]) / l);
      let zc = 0; for (const p of q) zc += p[2];
      items.push({ q, z: zc / q.length + (f.b ? 0 : -1e-4), col, lam });
    }
    items.sort((a, b) => a.z - b.z);
    const path = (q) => { g.beginPath(); for (let i = 0; i < q.length; i++) { const k = d ? d / (d - q[i][2]) : 1, X = ox + q[i][0] * s * k, Y = oy - q[i][1] * s * k; i ? g.lineTo(X, Y) : g.moveTo(X, Y); } g.closePath(); };
    g.lineJoin = "round";
    if (o.shadow) {
      const gy = (o.shadow.y ?? (fit ? fit.lo[1] : 0)) - c0[1];
      g.fillStyle = o.shadow.col || "rgba(0,0,0,.18)";
      g.beginPath();
      for (const f of faces) {
        const q = f.p.map((p) => { const x = p[0] - c0[0], z = p[2] - c0[2], z2 = z * cy - x * sy; return [ox + (x * cy + z * sy) * s, oy - (gy * cp - z2 * sp) * s]; });
        let area = 0; for (let i = 0; i < q.length; i++) { const a = q[i], b = q[(i + 1) % q.length]; area += a[0] * b[1] - b[0] * a[1]; }
        if (area < 0) q.reverse();
        for (let i = 0; i < q.length; i++) i ? g.lineTo(q[i][0], q[i][1]) : g.moveTo(q[i][0], q[i][1]);
        g.closePath();
      }
      g.fill("nonzero");
    }
    if (o.outline) { g.strokeStyle = o.outline.col; g.lineWidth = o.outline.w * 2; g.beginPath(); for (const it of items) { const q = it.q; for (let i = 0; i < q.length; i++) { const k = d ? d / (d - q[i][2]) : 1, X = ox + q[i][0] * s * k, Y = oy - q[i][1] * s * k; i ? g.lineTo(X, Y) : g.moveTo(X, Y); } g.closePath(); } g.stroke(); }
    g.lineWidth = o.seam ?? 0.7;
    for (const it of items) {
      const c = o.ink ? rgb(o.ink) : shader(it.col, it.lam, it);
      const css = `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
      g.fillStyle = css; g.strokeStyle = o.edge || css;
      path(it.q); g.fill(); g.stroke();
    }
  };


  Z.mount = (canvas, name, o = {}) => {
    const st = { yaw: -0.5, pitch: 0.28, spin: 0, speed: 1, pad: 0.9, drag: false, taxon: name, ...o };
    const g = canvas.getContext("2d"), still = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, raf = 0, last = null, phase = st.phase || 0, turn = 0, visible = true, dirty = true, dragging = null;
    const size = () => { const dpr = Math.min(2, window.devicePixelRatio || 1); w = canvas.clientWidth; h = canvas.clientHeight; canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); g.setTransform(dpr, 0, 0, dpr, 0, 0); dirty = true; };
    const frame = () => {
      raf = requestAnimationFrame(frame);
      const sec = Z.now(), hz = Z.species[st.taxon].hz || 1, dt = last == null ? 0 : sec - last; last = sec;
      const moving = !st.paused && !still;
      if (Z.clock != null) { phase = sec * hz * st.speed + (st.phase || 0); turn = st.spin * sec; }
      else if (moving) { phase += dt * hz * st.speed; turn += dt * st.spin; }
      if (!visible || !w || !(dirty || moving)) return;
      dirty = false;
      g.clearRect(0, 0, w, h);
      Z.draw(g, Z.build(st.taxon, phase), { ...st, name: st.taxon, x: w / 2, y: h / 2, box: [w * st.pad, h * st.pad], anyYaw: !!(st.spin || st.drag), yaw: st.yaw + turn });
    };
    size();
    if (typeof ResizeObserver === "function") new ResizeObserver(size).observe(canvas);
    if (typeof IntersectionObserver === "function") new IntersectionObserver((es) => { visible = es[es.length - 1].isIntersecting; dirty = true; }).observe(canvas);
    if (st.drag) {
      canvas.style.touchAction = "pan-y"; canvas.style.cursor = "grab";
      canvas.addEventListener("pointerdown", (e) => { dragging = [e.clientX, e.clientY, st.yaw, st.pitch]; canvas.setPointerCapture(e.pointerId); });
      canvas.addEventListener("pointermove", (e) => { if (!dragging) return; st.yaw = dragging[2] + (e.clientX - dragging[0]) * 0.012; st.pitch = clamp(dragging[3] + (e.clientY - dragging[1]) * 0.01, -0.3, 1.45); dirty = true; });
      canvas.addEventListener("pointerup", () => (dragging = null));
    }
    raf = requestAnimationFrame(frame);
    return { set: (p) => { Object.assign(st, p); dirty = true; }, stop: () => cancelAnimationFrame(raf), state: st };
  };
})();

(() => {
  const Z = window.Zoo, { TAU, V, curve, lerp } = Z, rad = (d) => (d * Math.PI) / 180;

  Z.wing = (W, a1, a2) => {
    const L = W.len, root = W.root, mid = W.mid ?? root, tip = W.tip ?? 0.05, fwd = W.fwd ?? 0.06, sweep = W.sweep ?? 0.1, wr = W.wrist ?? 0.45;
    const stations = [0, wr * 0.5, wr, lerp(wr, 1, 0.4), lerp(wr, 1, 0.75), 1];
    const lead = (s) => curve([[0, root * 0.42], [wr, root * 0.42 + fwd], [1, root * 0.42 + fwd - sweep]], s);
    const chord = (s) => curve([[0, root], [wr, mid], [lerp(wr, 1, 0.75), lerp(mid, tip, W.taper ?? 0.55)], [1, tip]], s);
    const tipFrom = W.tipFrom ?? 2;
    const ribs = stations.map((s) => {
      const lx = lead(s), isTip = s >= tipFrom - 1e-9;
      return { s, l: [lx, 0, s * L], t: [lx - chord(s), 0, s * L], m: W.m ?? 0.45, cf: isTip && W.tipc ? W.tipc : W.cf, cr: isTip && W.tipc ? W.tipc : W.cr ?? W.cf, bf: W.ucf ?? W.cf, br: isTip && W.tipc ? W.utipc ?? W.tipc : W.ucr ?? W.cr ?? W.cf };
    });
    const inner = [], outer = [];
    Z.strip(inner, ribs.filter((r) => r.s <= wr + 1e-9));
    Z.strip(outer, ribs.filter((r) => r.s >= wr - 1e-9));
    const nf = W.fingers || 0;
    if (nf) {
      const end = ribs[ribs.length - 1], fl = W.fingerLen ?? 0.22 * L, col = W.fingerCol ?? W.tipc ?? W.cr ?? W.cf;
      for (let i = 0; i < nf; i++) {
        const u0 = (i / nf) * 0.92, u1 = ((i + 0.78) / nf) * 0.92, a = V.lerp(end.l, end.t, u0), b = V.lerp(end.l, end.t, u1);
        const ang = rad(lerp(-22, 38, nf > 1 ? i / (nf - 1) : 0.5)), dir = [-Math.sin(ang), 0, Math.cos(ang)], len = fl * lerp(1, 0.72, nf > 1 ? i / (nf - 1) : 0);
        const c = V.lerp(a, b, 0.5), ta = V.add(V.lerp(a, c, 0.45), V.mul(dir, len)), tb = V.add(V.lerp(b, c, 0.45), V.mul(dir, len * 0.96));
        Z.plate(outer, [a, ta, tb, b], col, W.ufingerCol ?? col);
      }
    }
    Z.map(outer, Z.rotX(a2, [0, 0, wr * L]));
    const all = inner.concat(outer);
    Z.map(all, Z.rotX(a1));
    return all;
  };

  Z.kinds.bird = (sp, t) => {
    const m = [], ph = TAU * t, B = sp.body, W = sp.wing, N = sp.neck, H = sp.head, Bi = sp.bill, T = sp.tail;
    const amp = W.amp ?? 24, bob = (-amp / 700) * Math.sin(ph + 0.5), br = B.r, bw = B.w ?? 1.05;
    const body = [
      { c: [-0.44, 0.0, 0], rx: br * 0.3 * bw, ry: br * 0.24, col: B.rump ?? B.col, belly: B.vent ?? B.belly },
      { c: [-0.28, -0.005, 0], rx: br * 0.74 * bw, ry: br * 0.7, col: B.back ?? B.col, belly: B.belly },
      { c: [-0.05, -0.01, 0], rx: br * bw, ry: br * 0.96, col: B.back ?? B.col, belly: B.belly },
      { c: [0.14, 0.0, 0], rx: br * 0.94 * bw, ry: br * 0.9, col: B.col, belly: B.breast ?? B.belly },
      { c: [0.3, 0.025, 0], rx: Math.max(N.r * 1.7, br * 0.5), ry: Math.max(N.r * 1.7, br * 0.5), col: N.col ?? B.col, belly: N.belly ?? B.breast ?? B.belly },
    ];
    const na = rad(N.up ?? 6), hx = 0.3 + N.len * Math.cos(na), hy = 0.025 + N.len * Math.sin(na);
    const hr = H.r, hl = H.l ?? 1.25;
    const neck = [...body];
    if (N.len > 0.12) neck.push({ c: [lerp(0.3, hx, 0.45), lerp(0.025, hy, 0.45) - (N.sag ?? 0), 0], rx: N.r, ry: N.r, col: N.col ?? B.col, belly: N.belly ?? B.belly });
    neck.push({ c: [hx - hr * hl * 0.8, hy, 0], rx: Math.min(hr * 0.8, Math.max(N.r, hr * 0.6)), ry: Math.min(hr * 0.8, Math.max(N.r, hr * 0.6)), col: H.col, belly: H.throat ?? H.col });
    Z.tube(m, neck, { n: 8, belly: B.cut ?? -0.2, mottle: B.mottle, cap1: false });
    Z.ball(m, [hx, hy, 0], hr * hl, hr, hr * 0.92, H.cap ?? H.col, { n: 8, m: 4, bellyCol: H.col, belly: H.capCut ?? (H.cap ? 0.25 : -2), mottle: H.mottle });
    for (const s of [1, -1]) Z.ball(m, [hx + hr * 0.4, hy + hr * 0.22, s * hr * 0.74], hr * 0.2, hr * 0.2, hr * 0.14, H.eye ?? "#14110f", { n: 5, m: 2 });
    const bx = hx + hr * hl * 0.82, bl = Bi.len, bc = Bi.curve ?? 0, bdrop = Bi.drop ?? 0;
    const billPts = [0, 0.35, 0.7, 1].map((u) => [bx + bl * u, hy - hr * 0.12 - bdrop * bl * u - bc * bl * u * u, 0]);
    Z.tube(m, billPts.map((c, i) => ({ c, rx: Bi.r * [1, 0.8, 0.55, 0.12][i] * (Bi.w ?? 0.8), ry: Bi.r * [1, 0.78, 0.5, 0.12][i], col: i >= 2 && Bi.tip ? Bi.tip : Bi.col })), { n: 5, cap0: false });
    const tl = T.len, tsp = T.spread, fork = T.fork ?? 0, tx = -0.42, ty = 0.0;
    const tailR = [[tx, ty, br * 0.22], [tx - tl * 0.6, ty, tsp * 0.8], [tx - tl, ty, tsp], [tx - tl * (1 - fork * 0.5) - (T.round ?? 0.04), ty, tsp * 0.45], [tx - tl * (1 - fork) - (fork ? 0 : (T.round ?? 0.04) * 1.4), ty, 0], [tx, ty, 0]];
    Z.pair(m, Z.plate([], tailR, T.col, T.under ?? T.col));
    if (T.band) { const b0 = 0.72; const bp = [[tx - tl * b0, ty + 0.002, tsp * 0.86], tailR[2].map((v, i) => v + (i === 1 ? 0.002 : 0)), tailR[3].map((v, i) => v + (i === 1 ? 0.002 : 0)), tailR[4].map((v, i) => v + (i === 1 ? 0.002 : 0)), [tx - tl * b0, ty + 0.002, 0]]; Z.pair(m, Z.plate([], bp, T.band, T.band)); }
    if (sp.legs) {
      const Lg = sp.legs, r = Lg.r ?? 0.012, ex = tx - (Lg.len ?? 0.3);
      const leg = Z.tube([], [{ c: [-0.3, -br * 0.55, 0.05], rx: r * 1.6, ry: r * 1.6, col: Lg.col }, { c: [tx - 0.02, -br * 0.3, 0.04], rx: r, ry: r, col: Lg.col }, { c: [ex, -0.03, 0.035], rx: r, ry: r, col: Lg.col }], { n: 4 });
      Z.plate(leg, [[ex, -0.03, 0.02], [ex - 0.07, -0.035, 0.012], [ex - 0.075, -0.035, 0.05], [ex, -0.03, 0.05]], Lg.col);
      Z.pair(m, leg);
    }
    const a1 = rad((W.dih ?? 4) + amp * Math.sin(ph)), a2 = rad((W.amp2 ?? amp * 0.75) * Math.sin(ph - 1.1) - (W.droop ?? 0));
    const wing = Z.wing(W, a1, a2);
    Z.map(wing, Z.move([W.x ?? 0.1, br * 0.5, br * 0.62 * bw]));
    Z.pair(m, wing);
    return Z.map(m, Z.move([0, bob, 0]));
  };
})();

(() => {
  const Z = window.Zoo, { TAU, V, curve, lerp, clamp, smooth } = Z, rad = (d) => (d * Math.PI) / 180;
  const dirXY = (deg) => [Math.cos(rad(deg)), Math.sin(rad(deg)), 0];
  const restripe = (rings, n, a, b) => {
    const out = [], R = rings.length - 1;
    for (let i = 0; i <= n; i++) {
      const u = (i / n) * R, k = Math.min(R - 1, Math.floor(u)), f = u - k, p = rings[k], q = rings[k + 1];
      out.push({ ...p, c: V.lerp(p.c, q.c, f), rx: lerp(p.rx, q.rx, f), ry: lerp(p.ry, q.ry, f), ryb: p.ryb != null ? lerp(p.ryb, q.ryb ?? q.ry, f) : undefined, col: i % 2 ? b : a, belly: p.belly && (i % 2 ? b : p.belly) });
    }
    return out;
  };

  const GAITS = {
    walk: { off: { lh: 0, lf: 0.25, rh: 0.5, rf: 0.75 }, duty: 0.62, A: 20, bob: 0.008, bobN: 2 },
    amble: { off: { lh: 0, lf: 0.2, rh: 0.5, rf: 0.7 }, duty: 0.66, A: 17, bob: 0.006, bobN: 2 },
    trot: { off: { lh: 0.5, lf: 0, rh: 0, rf: 0.5 }, duty: 0.5, A: 25, bob: 0.02, bobN: 2 },
    bound: { off: { lh: 0, lf: 0.45, rh: 0.07, rf: 0.52 }, duty: 0.34, A: 38, bob: 0.05, bobN: 1, pitch: 7 },
    hop: { off: { lh: 0, lf: 0, rh: 0, rf: 0 }, duty: 0.36, A: 24, bob: 0.1, bobN: 1, pitch: 3 },
  };
  const legCycle = (g, p) => {
    p = ((p % 1) + 1) % 1;
    if (p < g.duty) return { a: g.A * (1 - (2 * p) / g.duty), flex: 0 };
    const u = (p - g.duty) / (1 - g.duty);
    return { a: g.A * (-1 + 2 * smooth(u)), flex: Math.sin(Math.PI * u) };
  };
  const REST = { front: { fr: [0.44, 0.36, 0.2], ang: [-7, 5, 0], flex: [8, -62, -25] }, hind: { fr: [0.4, 0.38, 0.22], ang: [20, -26, 5], flex: [18, -44, -30] }, pillar: { fr: [0.5, 0.36, 0.14], ang: [0, 0, 0], flex: [10, -40, -10] } };

  Z.kinds.quad = (sp, t) => {
    const m = [], ph = TAU * t, B = sp.body, Lg = sp.legs, N = sp.neck, H = sp.head, T = sp.tail, g = GAITS[sp.gait || "walk"];
    const w = B.w ?? 0.82, rc = B.rc, rb = B.rb ?? rc * 0.88, rh = B.rh ?? rc * 0.92;
    const lf = Lg.f, lh = Lg.h ?? lf, xS = 0.33, xH = -0.36;
    const top = (len, kind) => { const r = REST[kind]; let y = 0; for (let i = 0; i < 3; i++) y += len * r.fr[i] * Math.cos(rad(r.ang[i])); return y; };
    const kF = Lg.pillar ? "pillar" : "front", kH = Lg.pillar ? "pillar" : "hind";
    const yS = top(lf, kF), yH = top(lh, kH);
    const yc = (x) => lerp(yH, yS, (x - xH) / (xS - xH)) + (B.lift ?? rc * 0.25);
    const stripe = sp.stripes;
    const hump = B.hump ?? 0;
    let trunk = [
      { c: [-0.55, yc(-0.55) + rh * 0.15, 0], rx: rh * 0.42 * w, ry: rh * 0.5 },
      { c: [-0.38, yc(-0.38), 0], rx: rh * w, ry: rh },
      { c: [-0.06, yc(-0.06) + (B.sag ?? 0), 0], rx: rb * w, ry: rb },
      { c: [0.25, yc(0.25) + hump * 0.5, 0], rx: rc * w, ry: rc + hump * 0.5 },
      { c: [0.44, yc(0.44) + rc * 0.1 + hump * 0.3, 0], rx: rc * 0.8 * w, ry: rc * 0.86 },
    ].map((r) => ({ ...r, col: B.col, belly: B.belly }));
    const nb = [0.46, yc(0.46) + rc * 0.3 + hump * 0.3, 0], nd = dirXY(N.ang), nt = V.add(nb, V.mul(nd, N.len));
    const neck = [
      { c: V.add(nb, V.mul(nd, N.len * 0.3)), rx: lerp(N.r0, N.r1, 0.4) * (N.w ?? 0.8), ry: lerp(N.r0, N.r1, 0.4), col: N.col ?? B.col, belly: N.belly ?? B.belly },
      { c: V.add(nb, V.mul(nd, N.len * 0.9)), rx: N.r1 * (N.w ?? 0.8), ry: N.r1, col: N.col ?? B.col, belly: N.belly ?? B.belly },
    ];
    let skin = trunk.concat(neck);
    if (stripe) skin = restripe(skin, stripe.n ?? 22, B.col, stripe.col);
    Z.tube(m, skin, { n: B.n ?? 8, belly: B.cut ?? -0.3, mottle: B.mottle, exp: B.exp });
    const hd = dirXY(H.ang ?? -18), hu = [-hd[1], hd[0], 0], hc = V.add(nt, V.mul(hd, H.r * 0.5));
    const S = H.snout, at = (f, u = 0) => V.add(V.add(hc, V.mul(hd, f)), V.mul(hu, u));
    let head = [
      { c: at(-H.r * 0.95), rx: H.r * 0.55, ry: H.r * 0.6, col: H.col ?? B.col, belly: H.jaw },
      { c: at(0), rx: H.r * (H.w ?? 0.95), ry: H.r, col: H.col ?? B.col, belly: H.jaw },
      { c: at(H.r * 0.85, -H.r * 0.15), rx: S.r * 1.15, ry: S.r * 1.2, col: S.col ?? H.col ?? B.col, belly: H.jaw },
      { c: at(H.r * 0.85 + S.len, -H.r * 0.15 - (S.drop ?? 0)), rx: S.r * 0.8, ry: S.r * 0.85, col: S.col ?? H.col ?? B.col, capCol: S.nose ?? "#1b1716" },
    ];
    if (stripe) head = restripe(head, 7, H.col ?? B.col, stripe.col);
    Z.tube(m, head, { n: 8, belly: -0.25 });
    for (const s of [1, -1]) Z.ball(m, V.add(at(H.r * 0.35, H.r * 0.3), [0, 0, s * H.r * (H.w ?? 0.95) * 0.82]), H.r * 0.16, H.r * 0.16, H.r * 0.1, "#14110f", { n: 5, m: 2 });
    const E = H.ears;
    if (E) {
      const eb = at(-H.r * (E.back ?? 0.35), H.r * (E.up ?? 0.75)), el = E.len, ew = E.w ?? el * 0.5, flap = E.flap ? rad(E.flap) * Math.sin(ph * 2) : 0;
      let ear = [];
      const shape = E.type === "fan" ? [[0, 0], [-0.25, 0.25], [-1.0, 0.1], [-1.15, -0.55], [-0.7, -1.1], [-0.15, -0.9], [0.05, -0.3]] : E.type === "round" ? [[-0.5, 0], [-0.6, 0.6], [0, 1], [0.6, 0.6], [0.5, 0]] : E.type === "long" ? [[-0.4, 0], [-0.55, 0.5], [0, 1], [0.5, 0.5], [0.35, 0]] : [[-0.5, 0], [0, 1], [0.5, 0]];
      if (E.type === "fan") { Z.plate(ear, shape.map(([a, b]) => [a * el, b * el, 0]), E.col ?? H.col ?? B.col, E.inner ?? E.col ?? B.col); Z.map(ear, Z.rotY(rad(-(E.splay ?? 22)) - flap)); Z.map(ear, Z.move(V.add(at(-H.r * 0.3, H.r * 0.55), [0, 0, H.r * 0.8]))); }
      else {
        Z.plate(ear, shape.map(([a, b]) => [a * ew, b * el, 0]), E.inner ?? E.col ?? B.col, E.col ?? H.col ?? B.col);
        if (E.tip) Z.plate(ear, [[-0.12 * ew, 0.76 * el, 0.001], [0, (E.tuft ?? 1) * el, 0.001], [0.12 * ew, 0.76 * el, 0.001]], E.tip, E.tip);
        Z.map(ear, Z.rotY(rad(E.turn ?? 55))); Z.map(ear, Z.rotX(rad(-(E.splay ?? 20)))); Z.map(ear, Z.rotZ(rad(E.tilt ?? -12)));
        Z.map(ear, Z.move(V.add(eb, [0, 0, H.r * (E.z ?? 0.55)])));
      }
      Z.pair(m, ear);
    }
    for (const h of sp.horns || []) {
      const pts = h.pts.map(([f, u, o]) => V.add(at(f * H.r, u * H.r), [0, 0, o * H.r]));
      Z.pair(m, Z.tube([], pts.map((c, i) => ({ c, rx: h.r * lerp(1, h.end ?? 0.15, i / (pts.length - 1)), ry: h.r * lerp(1, h.end ?? 0.15, i / (pts.length - 1)), col: h.col })), { n: h.n ?? 5 }));
    }
    if (sp.trunk) {
      const K = sp.trunk, sw = Math.sin(ph) * 0.05, pts = [0, 0.25, 0.5, 0.75, 1].map((u) => V.add(at(H.r * 0.85 + S.len * 0.9 + K.len * 0.2 * Math.sin(u * 2.4) + sw * u * K.len * 3, -H.r * 0.2 - K.len * (u - 0.12 * u * u * (1 + Math.sin(ph)))), [0, 0, 0]));
      Z.tube(m, pts.map((c, i) => ({ c, rx: lerp(S.r * 0.8, K.r, i / 4), ry: lerp(S.r * 0.8, K.r, i / 4), col: K.col ?? B.col })), { n: 6 });
    }
    if (sp.mane) { const M = sp.mane, a = V.add(at(-H.r * 0.4, H.r * 0.9), [0, 0, 0]), b = V.add(nb, [-(M.back ?? 0.1), N.r0 * 0.9, 0]), up = V.mul(hu, M.h); Z.plate(m, [a, V.add(V.lerp(a, b, 0.1), up), V.add(V.lerp(a, b, 0.5), up), V.add(V.lerp(a, b, 0.9), V.mul(up, 0.8)), b], M.col, M.col); }
    if (sp.beard) { const M = sp.beard, a = at(H.r * 0.2, -H.r * 0.8), b = V.add(nb, [0.02, -N.r0 * 0.9, 0]); Z.plate(m, [a, V.add(V.lerp(a, b, 0.3), [0, -M.h, 0]), V.add(V.lerp(a, b, 0.8), [0, -M.h * 0.7, 0]), b], M.col, M.col); }
    if (T) {
      const tb = [-0.56, yc(-0.56) + rh * 0.35, 0], td = dirXY(180 + (T.ang ?? 40)), sway = Math.sin(ph + 1) * (T.sway ?? 0.03);
      const prof = T.bushy ? [0.55, 1, 0.95, 0.2] : T.tuft ? [1, 0.7, 0.6, 1.8, 0.4] : [1, 0.8, 0.55, 0.2];
      const rings = prof.map((k, i) => { const u = i / (prof.length - 1); return { c: [tb[0] + td[0] * T.len * u, tb[1] + td[1] * T.len * u + (T.curl ?? 0) * u * u * T.len, sway * u * u * 4 * T.len], rx: T.r * k, ry: T.r * k, col: u > (T.tipAt ?? 0.72) && T.tip ? T.tip : T.col ?? B.col, belly: T.under }; });
      Z.tube(m, rings, { n: 6 });
    }
    const leg = (front, side, key) => {
      const R = REST[front ? kF : kH], L = front ? lf : lh, { a, flex } = legCycle(g, t + g.off[key]);
      const rU = (front ? Lg.rU : Lg.rUh ?? Lg.rU * 1.2), rL = Lg.rL;
      let p = [front ? xS : xH, front ? yS : yH, side * (front ? rc : rh) * w * (Lg.stance ?? 0.62)];
      const pts = [p];
      for (let i = 0; i < 3; i++) { const th = rad(R.ang[i] + a + R.flex[i] * flex * (Lg.lift ?? 1)); p = [p[0] + Math.sin(th) * L * R.fr[i], p[1] - Math.cos(th) * L * R.fr[i], p[2]]; pts.push(p); }
      const cu = Lg.col ?? B.col, cl = Lg.lower ?? cu;
      let rings = [
        { c: V.add(pts[0], [0, rU * 0.9, -side * rU * 0.3]), rx: rU * 1.1, ry: rU * 1.5, col: cu },
        { c: pts[1], rx: rU * 0.78, ry: rU * 0.9, col: cu },
        { c: pts[2], rx: rL, ry: rL, col: cl },
        { c: pts[3], rx: rL * (Lg.pillar ? 1.05 : 0.9), ry: rL * (Lg.pillar ? 1.05 : 0.9), col: cl },
      ];
      if (stripe) rings = restripe(rings, 9, cu, stripe.col);
      Z.tube(m, rings, { n: 6 });
      const fr = rL * (Lg.footR ?? 1.35), fc = Lg.foot ?? cl;
      Z.ball(m, [pts[3][0] + fr * (Lg.pillar ? 0.1 : 0.5), pts[3][1] - fr * 0.1, pts[3][2]], fr * (Lg.pillar ? 1.05 : 1.4), fr * 0.75, fr, fc, { n: 6, m: 3 });
    };
    if (!Lg.noFront) { leg(true, 1, "rf"); leg(true, -1, "lf"); }
    if (sp.pitch) Z.map(m, Z.rotZ(rad(sp.pitch), [xH, yH, 0]));
    leg(false, 1, "rh"); leg(false, -1, "lh");
    const bob = g.bob * Math.sin(ph * g.bobN + 0.6) + g.bob, pit = rad((g.pitch ?? 0) * Math.sin(ph + 1.2));
    if (pit) Z.map(m, Z.rotZ(pit, [xH, yH, 0]));
    return Z.map(m, Z.move([0, bob * (sp.gait === "hop" || sp.gait === "bound" ? Math.max(0, Math.sin(ph + 0.6)) * 2 : 1), 0]));
  };

  Z.kinds.swim = (sp, t) => {
    const m = [], ph = TAU * t, Wv = sp.wave || {}, amp = Wv.amp ?? 0.05, k = Wv.k ?? 3.2, lateral = Wv.axis === "z";
    const off = (x) => { const u = clamp((sp.profile[0][0] - x) / (sp.profile[0][0] - sp.profile[sp.profile.length - 1][0])); return amp * u * u * Math.sin(ph - k * u); };
    const slope = (x) => (off(x + 0.01) - off(x - 0.01)) / 0.02;
    const bend = (p) => (lateral ? [p[0], p[1], p[2] + off(p[0])] : [p[0], p[1] + off(p[0]), p[2]]);
    const rings = sp.profile.map(([x, ry, rx, y, col, belly]) => ({ c: [x, y ?? 0, 0], rx: rx ?? ry, ry, ryb: sp.flatBelly ? ry * sp.flatBelly : undefined, col: col ?? sp.col, belly: belly ?? sp.belly }));
    const body = Z.tube([], rings, { n: sp.n ?? 10, belly: sp.cut ?? -0.25, mottle: sp.mottle, exp: sp.exp });
    Z.map(body, bend); for (const f of body) m.push(f);
    const tailX = sp.profile[sp.profile.length - 1][0], tailY = sp.profile[sp.profile.length - 1][3] ?? 0;
    if (sp.fluke) {
      const F = sp.fluke, a = Math.atan(slope(tailX)) * (F.gain ?? 1.6);
      const half = Z.plate([], [[tailX + F.chord * 0.5, tailY, 0], [tailX + F.chord * 0.15, tailY, F.span * 0.45], [tailX - F.chord * 0.55, tailY, F.span], [tailX - F.chord * 0.75, tailY, F.span * 0.85], [tailX - F.chord * 0.3, tailY, F.span * 0.3], [tailX - F.chord * (0.3 - (F.notch ?? 0.2)), tailY, 0]], F.col ?? sp.col, F.under ?? F.col ?? sp.col);
      const both = Z.pair([], half);
      Z.map(both, lateral ? Z.rotX(Math.PI / 2, [tailX, tailY, 0]) : (p) => p);
      Z.map(both, lateral ? Z.rotY(-a, [tailX, tailY, 0]) : Z.rotZ(a, [tailX, tailY, 0]));
      Z.map(both, lateral ? Z.move([0, 0, off(tailX)]) : Z.move([0, off(tailX), 0]));
      for (const f of both) m.push(f);
    }
    if (sp.hind) {
      const F = sp.hind, a = Math.atan(slope(tailX)) * 1.5;
      for (const s of [1, -1]) { const fl = Z.plate([], [[tailX + 0.02, tailY + 0.0, 0], [tailX - F.len * 0.6, tailY + F.w * 0.9, 0], [tailX - F.len, tailY + F.w * 0.5, 0], [tailX - F.len * 0.95, tailY - F.w * 0.5, 0], [tailX - F.len * 0.5, tailY - F.w * 0.7, 0]], F.col ?? sp.col); Z.map(fl, Z.rotY(-a + s * rad(F.splay ?? 12), [tailX, tailY, 0])); Z.map(fl, Z.move([0, 0, off(tailX) + s * 0.012])); for (const f of fl) m.push(f); }
    }
    if (sp.pec) {
      const P = sp.pec, fa = rad((P.ang ?? 25) + (P.flap ?? 6) * Math.sin(ph + 0.8)), sw = rad(P.sweep ?? 35);
      const shape = P.shape || [[0, 0.5], [0.35, 0.62], [0.8, 0.4], [1, 0.05], [0.8, -0.25], [0.3, -0.45], [0, -0.4]];
      const fin = Z.plate([], shape.map(([o, f]) => [f * P.chord, 0, o * P.len]), P.col ?? sp.col, P.under ?? P.col ?? sp.col);
      Z.map(fin, Z.rotY(sw)); Z.map(fin, Z.rotX(-fa)); Z.map(fin, Z.move([P.x, P.y ?? -0.04, P.z])); Z.map(fin, bend);
      Z.pair(m, fin);
    }
    if (sp.dorsal) { const D = sp.dorsal, y = D.y; const fin = Z.plate([], [[D.x + D.len * 0.5, y, 0], [D.x - D.len * 0.35, y + D.h, 0], [D.x - D.len * 0.6, y + D.h * 0.9, 0], [D.x - D.len * 0.5, y, 0]], D.col ?? sp.col); Z.map(fin, bend); for (const f of fin) m.push(f); }
    for (const e of sp.extra || []) { const part = e.tube ? Z.tube([], e.tube.map(([x, y, z, r, col]) => ({ c: [x, y, z], rx: r, ry: r, col: col ?? e.col })), { n: e.n ?? 5 }) : e.ball ? Z.ball([], e.ball.slice(0, 3), e.ball[3], e.ball[4], e.ball[5], e.col, { n: 6, m: 3 }) : Z.plate([], e.plate, e.col, e.back); Z.map(part, bend); e.single ? part.forEach((f) => m.push(f)) : Z.pair(m, part); }
    if (sp.eye) for (const s of [1, -1]) { const b = Z.ball([], [sp.eye[0], sp.eye[1], s * sp.eye[2]], sp.eye[3], sp.eye[3], sp.eye[3] * 0.6, "#14110f", { n: 5, m: 2 }); Z.map(b, bend); b.forEach((f) => m.push(f)); }
    return m;
  };

  Z.kinds.turtle = (sp, t) => {
    const m = [], ph = TAU * t, Sh = sp.shell, K = sp.skin;
    const prof = Sh.prof || [[0.42, 0.02, 0.1], [0.3, 0.13, 0.27], [0.05, 0.19, 0.36], [-0.2, 0.17, 0.32], [-0.4, 0.09, 0.17], [-0.5, 0.01, 0.03]];
    Z.tube(m, prof.map(([x, ry, rx]) => ({ c: [x, 0, 0], rx: rx * (Sh.w ?? 1), ry: ry * (Sh.h ?? 1), ryb: ry * 0.45, col: Sh.col, belly: Sh.under })), { n: 10, belly: -0.05, mottle: Sh.mottle });
    Z.tube(m, [{ c: [0.36, -0.01, 0], rx: 0.07, ry: 0.06, col: K.col, belly: K.under }, { c: [0.52, 0.0, 0], rx: 0.06, ry: 0.055, col: K.col, belly: K.under }], { n: 6, cap0: false, cap1: false });
    Z.ball(m, [0.6, 0.005, 0], 0.11 * (sp.head ?? 1), 0.07 * (sp.head ?? 1), 0.075 * (sp.head ?? 1), K.col, { n: 8, m: 4, bellyCol: K.under, belly: -0.2, mottle: K.mottle });
    for (const s of [1, -1]) Z.ball(m, [0.64, 0.03, s * 0.062 * (sp.head ?? 1)], 0.016, 0.016, 0.01, "#14110f", { n: 5, m: 2 });
    const F = sp.flip, a = rad((F.amp ?? 34) * Math.sin(ph)), tw = rad(18 * Math.cos(ph));
    const ribs = [[0, 0.07, -0.07], [0.3, 0.05, -0.13], [0.65, -0.1, -0.24], [1, -0.34, -0.38]].map(([s, lx, tx]) => ({ l: [lx, 0, s * F.len], t: [tx, 0, s * F.len], cf: K.col, bf: K.under ?? K.col }));
    const fl = Z.strip([], ribs);
    Z.map(fl, Z.rotZ(tw)); Z.map(fl, Z.rotX(a - rad(6))); Z.map(fl, Z.move([0.26, -0.02, 0.22]));
    Z.pair(m, fl);
    const hs = rad(10 * Math.sin(ph + 1.5));
    const hf = Z.plate([], [[-0.36, -0.03, 0.1], [-0.5, -0.03, 0.2], [-0.66, -0.03, 0.2], [-0.62, -0.03, 0.1], [-0.46, -0.03, 0.05]], K.col, K.under ?? K.col);
    Z.map(hf, Z.rotY(hs, [-0.4, 0, 0.1])); Z.pair(m, hf);
    Z.tube(m, [{ c: [-0.48, -0.02, 0], rx: 0.02, ry: 0.015, col: K.col }, { c: [-0.6, -0.03, 0], rx: 0.004, ry: 0.004, col: K.col }], { n: 4 });
    return Z.map(m, Z.move([0, 0.015 * Math.sin(ph - 1), 0]));
  };

  Z.kinds.tortoise = (sp, t) => {
    const m = [], ph = TAU * t, Sh = sp.shell, K = sp.skin, g = GAITS.walk, yb = 0.2;
    const sad = Sh.saddle ?? 0;
    const prof = [[0.46, 0.1 + sad * 0.9, 0.2, sad * 0.5], [0.3, 0.26 + sad * 0.35, 0.34, sad * 0.15], [0.0, 0.36, 0.4, 0], [-0.3, 0.3, 0.36, 0], [-0.48, 0.1, 0.18, 0]];
    Z.tube(m, prof.map(([x, ry, rx, dy]) => ({ c: [x, yb + dy * 0.3, 0], rx, ry, ryb: 0.07, col: Sh.col, belly: Sh.under })), { n: 10, belly: -0.02, mottle: Sh.mottle });
    const nod = 0.01 * Math.sin(ph * 2), nb = [0.4, yb + 0.06 + sad * 0.25, 0], nt = [0.4 + sp.neck * 0.75, yb + 0.06 + sad * 0.25 + sp.neck * (0.5 + sad) + nod, 0];
    Z.tube(m, [{ c: nb, rx: 0.075, ry: 0.075, col: K.col }, { c: V.lerp(nb, nt, 0.6), rx: 0.055, ry: 0.055, col: K.col }, { c: nt, rx: 0.05, ry: 0.05, col: K.col }], { n: 6, cap0: false, cap1: false });
    Z.ball(m, [nt[0] + 0.06, nt[1] + 0.01, 0], 0.1, 0.062, 0.065, K.col, { n: 8, m: 4 });
    for (const s of [1, -1]) Z.ball(m, [nt[0] + 0.1, nt[1] + 0.03, s * 0.055], 0.014, 0.014, 0.009, "#14110f", { n: 5, m: 2 });
    for (const [key, x, s] of [["rf", 0.28, 1], ["lf", 0.28, -1], ["rh", -0.3, 1], ["lh", -0.3, -1]]) {
      const { a, flex } = legCycle(g, t + g.off[key]), th = rad(a * 0.9), top = [x, yb + 0.02, s * 0.27], knee = [x + Math.sin(th) * 0.1 + (x > 0 ? 0.03 : -0.03), yb - 0.07 + flex * 0.03, s * 0.31], foot = [x + Math.sin(th) * 0.2, flex * 0.05, s * 0.3];
      Z.tube(m, [{ c: top, rx: 0.085, ry: 0.085, col: K.col }, { c: knee, rx: 0.075, ry: 0.075, col: K.col }, { c: foot, rx: 0.07, ry: 0.07, col: K.col, capCol: K.foot ?? K.col }], { n: 6 });
    }
    Z.tube(m, [{ c: [-0.46, yb, 0], rx: 0.03, ry: 0.03, col: K.col }, { c: [-0.56, yb - 0.06, 0], rx: 0.006, ry: 0.006, col: K.col }], { n: 4 });
    return Z.map(m, Z.move([0, 0.006 * Math.sin(ph * 2), 0]));
  };

  Z.kinds.bat = (sp, t) => {
    const m = [], ph = TAU * t, B = sp.body, H = sp.head, W = sp.wing, L = W.len, c = W.chord ?? 0.5;
    const amp = W.amp ?? 38, bob = (-amp / 520) * Math.sin(ph + 0.5);
    Z.tube(m, [{ c: [-0.3, 0, 0], rx: B.r * 0.35, ry: B.r * 0.35, col: B.col, belly: B.belly }, { c: [-0.12, 0, 0], rx: B.r * 0.9, ry: B.r * 0.85, col: B.col, belly: B.belly }, { c: [0.1, 0.01, 0], rx: B.r, ry: B.r * 0.95, col: B.mantle ?? B.col, belly: B.belly }, { c: [0.24, 0.03, 0], rx: B.r * 0.7, ry: B.r * 0.7, col: B.mantle ?? B.col, belly: B.mantle ?? B.belly }], { n: 8, belly: -0.2 });
    const hx = 0.3, hy = 0.05, hr = H.r;
    Z.tube(m, [{ c: [hx - hr * 0.8, hy, 0], rx: hr * 0.7, ry: hr * 0.7, col: H.col }, { c: [hx, hy, 0], rx: hr, ry: hr, col: H.col }, { c: [hx + hr * 0.8, hy - hr * 0.1, 0], rx: hr * 0.55, ry: hr * 0.55, col: H.col }, { c: [hx + hr * 0.8 + H.snout, hy - hr * 0.2, 0], rx: hr * 0.3, ry: hr * 0.3, col: H.muzzle ?? H.col, capCol: "#1b1716" }], { n: 6 });
    for (const s of [1, -1]) Z.ball(m, [hx + hr * 0.45, hy + hr * 0.3, s * hr * 0.72], hr * 0.2, hr * 0.2, hr * 0.12, "#14110f", { n: 5, m: 2 });
    const E = H.ears; const ear = Z.plate([], [[-E.w * 0.5, 0, 0], [-E.w * 0.2, E.len, 0], [E.w * 0.35, E.len * 0.8, 0], [E.w * 0.5, 0, 0]], E.col, E.col);
    Z.map(ear, Z.rotY(rad(60))); Z.map(ear, Z.rotX(rad(-(E.splay ?? 25)))); Z.map(ear, Z.move([hx - hr * 0.2, hy + hr * 0.7, hr * 0.55])); Z.pair(m, ear);
    const S = [0, 0, 0], Wp = [0.2 * L, 0, 0.4 * L], El = [-0.02 * L, 0, 0.2 * L], F3 = [-0.1 * L * c * 2, 0, L], F4 = [-0.9 * L * c, 0, 0.76 * L], F5 = [-1.02 * L * c, 0, 0.4 * L], An = [-0.42, 0, 0.03];
    const sc = (a, b, k = 0.14) => V.lerp(V.lerp(a, b, 0.5), Wp, k);
    const mem = W.col, und = W.under ?? W.col, inner = [], outer = [];
    Z.plate(inner, [S, Wp, F5, sc(F5, An, 0.2), An], mem, und);
    Z.plate(inner, [S, [0.12 * L, 0, 0.2 * L], Wp], mem, und);
    Z.plate(outer, [Wp, F3, sc(F3, F4), F4], mem, und); Z.plate(outer, [Wp, F4, sc(F4, F5), F5], mem, und);
    const bone = (list, pts) => Z.tube(list, pts.map((p, i) => ({ c: [p[0], p[1] + 0.004, p[2]], rx: W.bone ?? 0.011, ry: W.bone ?? 0.011, col: W.arm ?? B.col })), { n: 3 });
    bone(inner, [S, El, Wp]); bone(outer, [Wp, F3]); bone(outer, [Wp, F4]); bone(inner, [Wp, F5]);
    const a1 = rad((W.dih ?? 2) + amp * Math.sin(ph)), a2 = rad(amp * 0.9 * Math.sin(ph - 1.0) - 8);
    Z.map(outer, Z.rotX(a2, [0, 0, 0.4 * L]));
    const wing = inner.concat(outer); Z.map(wing, Z.rotX(a1)); Z.map(wing, Z.move([0.12, B.r * 0.45, B.r * 0.6]));
    Z.pair(m, wing);
    if (sp.tailMem) Z.plate(m, [[-0.28, 0, B.r * 0.3], [-0.42, 0, 0.09], [-0.5, 0, 0], [-0.42, 0, -0.09], [-0.28, 0, -B.r * 0.3]], mem, und);
    if (sp.freeTail) Z.tube(m, [{ c: [-0.3, 0, 0], rx: 0.012, ry: 0.012, col: B.col }, { c: [-0.3 - sp.freeTail, 0, 0], rx: 0.004, ry: 0.004, col: B.col }], { n: 4 });
    return Z.map(m, Z.move([0, bob, 0]));
  };

  Z.kinds.ray = (sp, t) => {
    const m = [], ph = TAU * t, L = sp.span, top = sp.col, und = sp.belly;
    const st = [0, 0.14, 0.32, 0.52, 0.72, 0.88, 1];
    const ribs = st.map((s) => {
      const y = sp.amp * Math.pow(s, 1.4) * Math.sin(ph - 1.5 * s), lx = curve([[0, 0.42], [0.14, 0.4], [0.5, 0.22], [1, -0.12]], s), tx = curve([[0, -0.42], [0.14, -0.4], [0.5, -0.24], [0.88, -0.2], [1, -0.17]], s);
      return { l: [lx, y, s * L], t: [tx, y * 0.9, s * L], m: 0.4, cf: top, cr: top, bf: und, br: und };
    });
    Z.pair(m, Z.strip([], ribs));
    Z.tube(m, [[0.5, 0.03, 0.15], [0.3, 0.075, 0.2], [0, 0.085, 0.16], [-0.3, 0.05, 0.08], [-0.46, 0.012, 0.02]].map(([x, ry, rx]) => ({ c: [x, 0.012, 0], rx, ry, ryb: ry * 0.7, col: top, belly: und })), { n: 8, belly: -0.1 });
    if (sp.mark) for (const s of [1, -1]) Z.plate(m, [[0.34, 0.058, s * 0.08], [0.3, 0.05, s * 0.3], [0.12, 0.05, s * 0.2], [0.2, 0.07, s * 0.05]], sp.mark, top);
    const cf = Z.plate([], [[0.44, 0.0, 0.11], [0.66, -0.02, 0.1], [0.64, -0.05, 0.16], [0.42, -0.01, 0.19]], top, und); Z.pair(m, cf);
    const sway = Math.sin(ph + 2) * 0.05;
    Z.tube(m, [0, 0.33, 0.66, 1].map((u) => ({ c: [-0.44 - u * sp.tail, 0.01, sway * u * u], rx: 0.016 * (1 - u * 0.9), ry: 0.016 * (1 - u * 0.9), col: top })), { n: 4 });
    return Z.map(m, Z.move([0, 0.03 * Math.sin(ph - 0.6), 0]));
  };

  Z.kinds.penguin = (sp, t) => {
    const m = [], ph = TAU * t, bk = sp.col, wh = sp.belly;
    const prof = [[0.5, 0.075, 0.07, 0.02, bk, bk], [0.38, 0.1, 0.1, 0.01, bk, sp.neck ?? wh], [0.22, 0.17, 0.18, 0, bk, sp.chest ?? wh], [-0.05, 0.2, 0.21, 0, bk, wh], [-0.32, 0.15, 0.16, 0, bk, wh], [-0.5, 0.05, 0.06, 0.01, bk, wh]];
    Z.tube(m, prof.map(([x, ry, rx, y, col, belly]) => ({ c: [x, y, 0], rx, ry, col, belly })), { n: 10, belly: 0.1 });
    Z.ball(m, [0.56, 0.03, 0], 0.11, 0.085, 0.08, bk, { n: 8, m: 4 });
    for (const s of [1, -1]) { Z.ball(m, [0.6, 0.05, s * 0.066], 0.014, 0.014, 0.009, "#f5f1e6", { n: 5, m: 2 }); if (sp.cheek) Z.plate(m, [[0.47, 0.06, s * 0.079], [0.52, 0.0, s * 0.084], [0.42, -0.05, s * 0.1], [0.36, 0.02, s * 0.098]], sp.cheek, sp.cheek); }
    Z.tube(m, [{ c: [0.64, 0.02, 0], rx: 0.03, ry: 0.028, col: sp.bill }, { c: [0.74, 0.012, 0], rx: 0.018, ry: 0.016, col: sp.billStripe ?? sp.bill }, { c: [0.84, -0.005, 0], rx: 0.004, ry: 0.004, col: sp.bill }], { n: 5, cap0: false });
    const a = rad(38 * Math.sin(ph)), tw = rad(20 * Math.cos(ph));
    const fl = Z.strip([], [[0, 0.07, -0.07], [0.5, 0.02, -0.12], [1, -0.14, -0.2]].map(([s, lx, tx]) => ({ l: [lx, 0, s * 0.42], t: [tx, 0, s * 0.42], cf: bk, bf: wh })));
    Z.map(fl, Z.rotZ(tw)); Z.map(fl, Z.rotX(a)); Z.map(fl, Z.move([0.2, 0.02, 0.17])); Z.pair(m, fl);
    const foot = Z.plate([], [[-0.46, -0.02, 0.02], [-0.6, -0.03, 0.02], [-0.64, -0.03, 0.09], [-0.5, -0.02, 0.07]], sp.feet ?? "#2a2624"); Z.pair(m, foot);
    Z.plate(m, [[-0.48, 0.02, 0.03], [-0.62, 0.03, 0], [-0.48, 0.02, -0.03]], bk, bk);
    return Z.map(m, Z.rotZ(rad(4 * Math.sin(ph - 1)), [0, 0, 0]));
  };
})();
