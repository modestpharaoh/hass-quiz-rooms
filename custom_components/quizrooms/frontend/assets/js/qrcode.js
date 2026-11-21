/* Self-contained pure-JS QR Code Generator (Byte Mode, ECC Level M, Versions 1-10).
   Zero external dependencies. Renders crisp inline SVG for solid white padded frames. */
(function () {
  // GF(256) with primitive polynomial 0x11D
  const GF_EXP = new Uint8Array(512);
  const GF_LOG = new Uint8Array(256);
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];

  function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return GF_EXP[GF_LOG[a] + GF_LOG[b]];
  }

  function rsGeneratorPoly(degree) {
    let poly = [1];
    for (let i = 0; i < degree; i++) {
      const next = new Array(poly.length + 1).fill(0);
      const factor = GF_EXP[i];
      for (let j = 0; j < poly.length; j++) {
        next[j] ^= poly[j];
        next[j + 1] ^= gfMul(poly[j], factor);
      }
      poly = next;
    }
    return poly;
  }

  function rsRemainder(data, ecCount) {
    const gen = rsGeneratorPoly(ecCount);
    const res = new Array(ecCount).fill(0);
    for (let i = 0; i < data.length; i++) {
      const factor = data[i] ^ res.shift();
      res.push(0);
      if (factor !== 0) {
        for (let j = 0; j < ecCount; j++) {
          res[j] ^= gfMul(gen[j + 1], factor);
        }
      }
    }
    return res;
  }

  // Version 1..10 specs for ECC Level M:
  // [totalCodewords, ecPerBlock, [[blockCount, dataCodewordsPerBlock], ...], alignmentPositions]
  const VERSION_SPECS = {
    1: [26, 10, [[1, 16]], []],
    2: [44, 16, [[1, 28]], [6, 18]],
    3: [70, 26, [[1, 44]], [6, 22]],
    4: [100, 18, [[2, 32]], [6, 26]],
    5: [134, 24, [[2, 43]], [6, 30]],
    6: [172, 16, [[4, 27]], [6, 34]],
    7: [196, 18, [[4, 31]], [6, 22, 38]],
    8: [242, 22, [[2, 38], [2, 39]], [6, 24, 42]],
    9: [292, 22, [[3, 36], [2, 37]], [6, 26, 46]],
    10: [346, 26, [[4, 43], [1, 44]], [6, 28, 50]],
  };

  function encodeData(text) {
    const bytes = new TextEncoder().encode(text);
    let version = 1;
    let totalDataCw = 0;
    for (let v = 1; v <= 10; v++) {
      const blocks = VERSION_SPECS[v][2];
      const cap = blocks.reduce((acc, [cnt, dcw]) => acc + cnt * dcw, 0);
      const countBits = v <= 9 ? 8 : 16;
      const neededBits = 4 + countBits + bytes.length * 8;
      if (neededBits <= cap * 8) {
        version = v;
        totalDataCw = cap;
        break;
      }
      if (v === 10) {
        version = 10;
        totalDataCw = cap;
      }
    }

    const bits = [];
    const pushBits = (val, len) => {
      for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
    };

    // Byte mode indicator (0100)
    pushBits(0b0100, 4);
    pushBits(bytes.length, version <= 9 ? 8 : 16);
    for (const b of bytes) pushBits(b, 8);

    // Terminator + byte alignment + pad codewords
    const capBits = totalDataCw * 8;
    const term = Math.min(4, capBits - bits.length);
    for (let i = 0; i < term; i++) bits.push(0);
    while (bits.length % 8 !== 0) bits.push(0);

    const dataCw = [];
    for (let i = 0; i < bits.length; i += 8) {
      let val = 0;
      for (let b = 0; b < 8; b++) val = (val << 1) | bits[i + b];
      dataCw.push(val);
    }
    const pads = [0xec, 0x11];
    let pIdx = 0;
    while (dataCw.length < totalDataCw) {
      dataCw.push(pads[pIdx % 2]);
      pIdx++;
    }

    // Split into RS blocks and interleave
    const [, ecPerBlock, blockGroups] = VERSION_SPECS[version];
    const dataBlocks = [];
    const ecBlocks = [];
    let offset = 0;
    for (const [count, dcw] of blockGroups) {
      for (let i = 0; i < count; i++) {
        const blk = dataCw.slice(offset, offset + dcw);
        offset += dcw;
        dataBlocks.push(blk);
        ecBlocks.push(rsRemainder(blk, ecPerBlock));
      }
    }

    const interleaved = [];
    const maxDataLen = Math.max(...dataBlocks.map((b) => b.length));
    for (let i = 0; i < maxDataLen; i++) {
      for (const blk of dataBlocks) {
        if (i < blk.length) interleaved.push(blk[i]);
      }
    }
    for (let i = 0; i < ecPerBlock; i++) {
      for (const blk of ecBlocks) interleaved.push(blk[i]);
    }
    return { version, codewords: interleaved };
  }

  function makeMatrix(version, codewords) {
    const size = version * 4 + 17;
    const mod = Array.from({ length: size }, () => new Int8Array(size));
    const isFunc = Array.from({ length: size }, () => new Uint8Array(size));

    const setFunc = (r, c, val) => {
      if (r >= 0 && r < size && c >= 0 && c < size) {
        mod[r][c] = val ? 1 : 0;
        isFunc[r][c] = 1;
      }
    };

    // Finder patterns + separators
    const placeFinder = (r0, c0) => {
      for (let dr = -1; dr <= 7; dr++) {
        for (let dc = -1; dc <= 7; dc++) {
          const r = r0 + dr;
          const c = c0 + dc;
          if (r < 0 || r >= size || c < 0 || c >= size) continue;
          const on =
            dr >= 0 &&
            dr <= 6 &&
            dc >= 0 &&
            dc <= 6 &&
            (dr === 0 || dr === 6 || dc === 0 || dc === 6 || (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
          setFunc(r, c, on ? 1 : 0);
        }
      }
    };
    placeFinder(0, 0);
    placeFinder(0, size - 7);
    placeFinder(size - 7, 0);

    // Alignment patterns
    const alignPos = VERSION_SPECS[version][3];
    for (const r0 of alignPos) {
      for (const c0 of alignPos) {
        if (isFunc[r0][c0]) continue;
        for (let dr = -2; dr <= 2; dr++) {
          for (let dc = -2; dc <= 2; dc++) {
            const on = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
            setFunc(r0 + dr, c0 + dc, on ? 1 : 0);
          }
        }
      }
    }

    // Timing patterns
    for (let i = 8; i < size - 8; i++) {
      if (!isFunc[6][i]) setFunc(6, i, i % 2 === 0 ? 1 : 0);
      if (!isFunc[i][6]) setFunc(i, 6, i % 2 === 0 ? 1 : 0);
    }

    // Dark module & reserve format info
    setFunc(size - 8, 8, 1);
    for (let i = 0; i <= 8; i++) {
      if (i !== 6) {
        isFunc[8][i] = 1;
        isFunc[i][8] = 1;
      }
    }
    for (let i = size - 8; i < size; i++) {
      isFunc[8][i] = 1;
      isFunc[i][8] = 1;
    }

    // Version info for >= 7
    if (version >= 7) {
      let rem = version;
      for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
      const bits = (version << 12) | rem;
      for (let i = 0; i < 18; i++) {
        const bit = (bits >>> i) & 1;
        const a = size - 11 + (i % 3);
        const b = Math.floor(i / 3);
        setFunc(a, b, bit);
        setFunc(b, a, bit);
      }
    }

    // Place data bits in upward/downward zigzag
    let bitIdx = 0;
    const totalBits = codewords.length * 8;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) {
        const upward = ((right + 1) & 2) === 0;
        const r = upward ? size - 1 - vert : vert;
        for (let j = 0; j < 2; j++) {
          const c = right - j;
          if (!isFunc[r][c]) {
            let bit = 0;
            if (bitIdx < totalBits) {
              bit = (codewords[bitIdx >>> 3] >>> (7 - (bitIdx & 7))) & 1;
              bitIdx++;
            }
            mod[r][c] = bit;
          }
        }
      }
    }

    // Evaluate masks 0..7
    const maskCond = (m, r, c) => {
      switch (m) {
        case 0: return (r + c) % 2 === 0;
        case 1: return r % 2 === 0;
        case 2: return c % 3 === 0;
        case 3: return (r + c) % 3 === 0;
        case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
        case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
        case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
        default: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
      }
    };

    const writeFormat = (grid, mask) => {
      // ECC Level M = 00
      const data = (0b00 << 3) | mask;
      let rem = data;
      for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
      const bits = ((data << 10) | rem) ^ 0x5412;

      for (let i = 0; i <= 5; i++) grid[8][i] = (bits >>> (14 - i)) & 1;
      grid[8][7] = (bits >>> 8) & 1;
      grid[8][8] = (bits >>> 7) & 1;
      grid[7][8] = (bits >>> 6) & 1;
      for (let i = 9; i < 15; i++) grid[14 - i][8] = (bits >>> (14 - i)) & 1;

      for (let i = 0; i < 8; i++) grid[8][size - 1 - i] = (bits >>> i) & 1;
      for (let i = 0; i < 7; i++) grid[size - 7 + i][8] = (bits >>> (14 - i)) & 1;
    };

    const penaltyScore = (grid) => {
      let score = 0;
      // Adjacent modules in row/col
      for (let r = 0; r < size; r++) {
        let runR = 1, runC = 1;
        for (let c = 1; c < size; c++) {
          if (grid[r][c] === grid[r][c - 1]) {
            runR++;
            if (runR === 5) score += 3;
            else if (runR > 5) score += 1;
          } else runR = 1;

          if (grid[c][r] === grid[c - 1][r]) {
            runC++;
            if (runC === 5) score += 3;
            else if (runC > 5) score += 1;
          } else runC = 1;
        }
      }
      // 2x2 blocks
      for (let r = 0; r < size - 1; r++) {
        for (let c = 0; c < size - 1; c++) {
          const v = grid[r][c];
          if (v === grid[r][c + 1] && v === grid[r + 1][c] && v === grid[r + 1][c + 1]) {
            score += 3;
          }
        }
      }
      return score;
    };

    let bestGrid = null;
    let bestScore = Infinity;
    for (let m = 0; m < 8; m++) {
      const candidate = mod.map((row, r) => {
        const copy = new Int8Array(row);
        for (let c = 0; c < size; c++) {
          if (!isFunc[r][c] && maskCond(m, r, c)) copy[c] ^= 1;
        }
        return copy;
      });
      writeFormat(candidate, m);
      const sc = penaltyScore(candidate);
      if (sc < bestScore) {
        bestScore = sc;
        bestGrid = candidate;
      }
    }
    return bestGrid;
  }

  function toSVG(text, quietZone = 2) {
    const { version, codewords } = encodeData(String(text || ""));
    const grid = makeMatrix(version, codewords);
    const size = grid.length;
    const total = size + quietZone * 2;
    const paths = [];
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (grid[r][c]) {
          paths.push(`M${c + quietZone},${r + quietZone}h1v1h-1z`);
        }
      }
    }
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${total} ${total}" shape-rendering="crispEdges" role="img" aria-label="QR Code">` +
      `<rect width="${total}" height="${total}" fill="#FFFFFF"/>` +
      `<path d="${paths.join("")}" fill="#000000"/>` +
      `</svg>`
    );
  }

  window.QuizRoomsQR = { toSVG };
})();
