/**
 * The Rotsy mark and wordmark.
 *
 * Hand-authored SVG rather than a raster asset: the same file has to stay legible
 * as a 16px favicon and as a 64px login lockup, and has to sit on both the light
 * and dark themes. The mark is a shield of isometric cubes (the registry) inside
 * a wireframe globe (the network), with a violet arrow sweeping up across it (the
 * improvement the project is named for).
 *
 * Gradient ids are suffixed per instance — two logos on one page with colliding
 * ids would make the second one render with the first one's fill.
 */
import { useId } from 'react';

/** The glyph alone. Square, so it drops into any fixed-size slot. */
export function LogoMark({ size = 24, className = '' }) {
  const uid = useId().replace(/:/g, '');
  const shield = `shield-${uid}`;
  const cube = `cube-${uid}`;
  const arrow = `arrow-${uid}`;
  const glow = `glow-${uid}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="Rotsy"
    >
      <defs>
        <linearGradient id={shield} x1="32" y1="4" x2="32" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1e3a8a" />
          <stop offset="0.55" stopColor="#111f4d" />
          <stop offset="1" stopColor="#0a1230" />
        </linearGradient>
        <linearGradient id={cube} x1="20" y1="18" x2="46" y2="46" gradientUnits="userSpaceOnUse">
          <stop stopColor="#a5f3fc" />
          <stop offset="0.5" stopColor="#38bdf8" />
          <stop offset="1" stopColor="#6366f1" />
        </linearGradient>
        <linearGradient id={arrow} x1="12" y1="52" x2="56" y2="12" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6d28d9" />
          <stop offset="0.5" stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#c084fc" />
        </linearGradient>
        <radialGradient id={glow} cx="32" cy="32" r="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#22d3ee" stopOpacity="0.35" />
          <stop offset="1" stopColor="#22d3ee" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Wireframe globe. Drawn first so the shield sits over its centre. */}
      <circle cx="32" cy="32" r="29" fill={`url(#${glow})`} />
      <g stroke="#38bdf8" strokeWidth="0.9" opacity="0.55" fill="none">
        <circle cx="32" cy="32" r="28.5" />
        <ellipse cx="32" cy="32" rx="13" ry="28.5" />
        <ellipse cx="32" cy="32" rx="24" ry="28.5" />
        <path d="M4.6 24h54.8M4.6 40h54.8M3.5 32h57" />
      </g>

      {/* Shield. */}
      <path
        d="M32 6.5 55 15v16.5c0 12.9-9.4 21.9-23 26.5-13.6-4.6-23-13.6-23-26.5V15z"
        fill={`url(#${shield})`}
        stroke="#38bdf8"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />

      {/* Isometric cubes — the stored artefacts. */}
      <g fill={`url(#${cube})`} stroke="#e0f2fe" strokeWidth="0.7" strokeLinejoin="round">
        <path d="M32 15.5 40 20v9l-8 4.5-8-4.5v-9z" opacity="0.95" />
        <path d="M23 30.5 31 35v9l-8 4.5-8-4.5v-9z" opacity="0.8" />
        <path d="M41 30.5 49 35v9l-8 4.5-8-4.5v-9z" opacity="0.8" />
      </g>

      {/* The arrow, sweeping up and to the right across the shield. */}
      <path
        d="M13 47c7.5-9 13.5-4 19-9.5S42.5 20 51.5 13"
        stroke={`url(#${arrow})`}
        strokeWidth="6.5"
        strokeLinecap="round"
        fill="none"
      />
      <path d="M40.5 12.5 55.5 10l-2.5 15z" fill="#a855f7" />
    </svg>
  );
}

/**
 * Mark plus wordmark. `size` drives the glyph; the wordmark scales with it.
 * The word uses `currentColor` so it inherits whatever the surrounding text is,
 * which is what keeps it readable in both themes without a second asset.
 */
export default function LogoLockup({ size = 20, className = '', tagline = false }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <LogoMark size={size} />
      <span className="inline-flex flex-col leading-none">
        <span
          className="font-semibold tracking-tight text-slate-800 dark:text-slate-100"
          style={{ fontSize: size * 0.8 }}
        >
          rotsy
        </span>
        {tagline && (
          <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.14em] text-slate-400 dark:text-slate-500">
            Nexus &amp; DevSecOps improvement
          </span>
        )}
      </span>
    </span>
  );
}
