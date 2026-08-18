// Pure-JS Ed25519 (RFC 8032 reference algorithm), no dependencies.
// Deliberate mirror of shared/crypto.py's implementation and same rationale:
// this project avoids relying on native WebCrypto Ed25519 support (still
// inconsistent across browsers) and any external crypto library, using
// BigInt for the same arbitrary-precision arithmetic the Python side uses.
// Exposes window.ed25519 = { generateKeypair, publicKeyFromPrivate, sign,
// verify, fingerprint, bytesToHex, hexToBytes }.
(function (global) {
  "use strict";

  const B = 256n;
  const Q = 2n ** 255n - 19n;
  const L = 2n ** 252n + 27742317777372353535851937790883648493n;

  function mod(a, m) {
    const r = a % m;
    return r >= 0n ? r : r + m;
  }

  function expmod(b, e, m) {
    let result = 1n;
    b = mod(b, m);
    while (e > 0n) {
      if (e & 1n) result = mod(result * b, m);
      e >>= 1n;
      b = mod(b * b, m);
    }
    return result;
  }

  function inv(x) {
    return expmod(x, Q - 2n, Q);
  }

  const D = mod(-121665n * inv(121666n), Q);
  const I = expmod(2n, (Q - 1n) / 4n, Q);

  function xrecover(y) {
    const xx = mod((y * y - 1n) * inv(D * y * y + 1n), Q);
    let x = expmod(xx, (Q + 3n) / 8n, Q);
    if (mod(x * x - xx, Q) !== 0n) {
      x = mod(x * I, Q);
    }
    if (mod(x, 2n) !== 0n) {
      x = Q - x;
    }
    return x;
  }

  const BY = mod(4n * inv(5n), Q);
  const BX = xrecover(BY);
  const BASE = [mod(BX, Q), mod(BY, Q)];

  function edwards(p, q) {
    const [x1, y1] = p, [x2, y2] = q;
    const x3 = mod((x1 * y2 + x2 * y1) * inv(1n + D * x1 * x2 * y1 * y2), Q);
    const y3 = mod((y1 * y2 + x1 * x2) * inv(1n - D * x1 * x2 * y1 * y2), Q);
    return [x3, y3];
  }

  function scalarmult(p, e) {
    if (e === 0n) return [0n, 1n];
    let q = scalarmult(p, e / 2n);
    q = edwards(q, q);
    if (e & 1n) q = edwards(q, p);
    return q;
  }

  function isOnCurve(p) {
    const [x, y] = p;
    return mod(-x * x + y * y - 1n - D * x * x * y * y, Q) === 0n;
  }

  async function sha512(bytes) {
    const digest = await crypto.subtle.digest("SHA-512", bytes);
    return new Uint8Array(digest);
  }

  function bytesToBigIntLE(bytes) {
    let v = 0n;
    for (let i = bytes.length - 1; i >= 0; i--) v = (v << 8n) | BigInt(bytes[i]);
    return v;
  }

  function bigIntToBytesLE(v, len) {
    const out = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      out[i] = Number(v & 0xffn);
      v >>= 8n;
    }
    return out;
  }

  function encodeInt(y) {
    return bigIntToBytesLE(y, Number(B) / 8);
  }

  function encodePoint(p) {
    const [x, y] = p;
    let out = bigIntToBytesLE(y, 32);
    out = new Uint8Array(out);
    if (x & 1n) out[31] |= 0x80;
    return out;
  }

  function decodeInt(s) {
    return bytesToBigIntLE(s);
  }

  function decodePoint(s) {
    const signBit = (s[31] & 0x80) !== 0;
    const yBytes = new Uint8Array(s);
    yBytes[31] &= 0x7f;
    const y = bytesToBigIntLE(yBytes);
    let x = xrecover(y);
    if ((x & 1n) !== (signBit ? 1n : 0n)) {
      x = Q - x;
    }
    const p = [x, y];
    if (!isOnCurve(p)) throw new Error("decoding point that is not on curve");
    return p;
  }

  function concatBytes(...arrs) {
    let len = 0;
    for (const a of arrs) len += a.length;
    const out = new Uint8Array(len);
    let off = 0;
    for (const a of arrs) { out.set(a, off); off += a.length; }
    return out;
  }

  async function hint(m) {
    const h = await sha512(m);
    return bytesToBigIntLE(h);
  }

  async function clampA(seed) {
    const h = await sha512(seed);
    const bytes = new Uint8Array(h.slice(0, 32));
    bytes[0] &= 248;
    bytes[31] &= 127;
    bytes[31] |= 64;
    const a = bytesToBigIntLE(bytes);
    return [h, a];
  }

  async function publicFromSeed(seed) {
    const [, a] = await clampA(seed);
    return encodePoint(scalarmult(BASE, a));
  }

  async function generateKeypair() {
    const seed = new Uint8Array(32);
    crypto.getRandomValues(seed);
    const pub = await publicFromSeed(seed);
    return { privateKey: seed, publicKey: pub };
  }

  async function sign(privateKey, message) {
    const pub = await publicFromSeed(privateKey);
    const [h, a] = await clampA(privateKey);
    const rScalar = await hint(concatBytes(h.slice(32, 64), message));
    const rPoint = scalarmult(BASE, mod(rScalar, L));
    const rEnc = encodePoint(rPoint);
    const s = mod(rScalar + (await hint(concatBytes(rEnc, pub, message))) * a, L);
    return concatBytes(rEnc, encodeInt(s));
  }

  async function verify(publicKey, message, signature) {
    if (signature.length !== 64 || publicKey.length !== 32) return false;
    let rPoint, aPoint;
    try {
      rPoint = decodePoint(signature.slice(0, 32));
      aPoint = decodePoint(publicKey);
    } catch (e) {
      return false;
    }
    const s = decodeInt(signature.slice(32, 64));
    if (s >= L) return false;
    const h = await hint(concatBytes(encodePoint(rPoint), publicKey, message));
    const left = scalarmult(BASE, s);
    const right = edwards(rPoint, scalarmult(aPoint, mod(h, L)));
    return left[0] === right[0] && left[1] === right[1];
  }

  function bytesToHex(bytes) {
    return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  function hexToBytes(hex) {
    const out = new Uint8Array(hex.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  async function sha256Hex(bytes) {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return bytesToHex(new Uint8Array(digest));
  }

  async function fingerprint(publicKey) {
    const full = await sha256Hex(publicKey);
    return full.slice(0, 16);
  }

  global.ed25519 = {
    generateKeypair, publicKeyFromPrivate: publicFromSeed, sign, verify,
    fingerprint, bytesToHex, hexToBytes, sha256Hex,
  };
})(window);
