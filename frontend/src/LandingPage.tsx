import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import './App.css';
import cardIllustrations from './assets/card-illustrations.png';

// ─── Nav items ───────────────────────────────────────────────
const NAV_ITEMS = [
  { label: 'HOME', hash: '#home' },
  { label: 'EVENTS', hash: '#events' },
  { label: 'PROJECTS', hash: '#projects' },
  { label: 'TEAM', hash: '#team' },
  { label: 'RESOURCES', hash: '#resources' },
] as const;

const SECOND_PAGE_CARDS = [
  { title: 'JOIN', artwork: 'join' },
  { title: 'RESOURCES', artwork: 'resources' },
  { title: 'TEAM', artwork: 'team' },
  { title: 'GALLERY', artwork: 'gallery' },
  { title: 'EVENTS', artwork: 'events' },
] as const;

// ─── Theme helpers ────────────────────────────────────────────
type Theme = 'light' | 'dark';
const STORAGE_KEY = 'atc-theme';

function getInitialTheme(): Theme {
  if (typeof window === 'undefined') return 'light';
  const saved = localStorage.getItem(STORAGE_KEY) as Theme | null;
  if (saved === 'light' || saved === 'dark') return saved;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

// ─── Theme icon (hoisted outside App to satisfy lint) ─────────
function ThemeIcon({ theme }: { theme: Theme }) {
  return theme === 'dark' ? (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  ) : (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

// ─── Stylized A / Lambda glyph (Source of truth for all brand A letters) ─
function StylizedA({ className = 'lambda-glyph' }: { className?: string }) {
  return (
    <svg viewBox="0 0 100 88" className={className} aria-hidden="true">
      <polygon points="40,0 60,0 96,87 72,87 50,22 28,87 4,87" fill="currentColor" />
      <polygon points="12,65 31,65 24,75 5,75" fill="currentColor" />
    </svg>
  );
}

// ─── BrandText: replaces every uppercase A with the stylized glyph ──────────
function BrandText({ children }: { children: string }) {
  if (!children.includes('A')) return <>{children}</>;
  const parts = children.split('A');
  return (
    <span aria-label={children}>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && (
            <span className="brand-stylized-a" aria-hidden="true">
              <StylizedA />
            </span>
          )}
        </span>
      ))}
    </span>
  );
}

function CardArtwork({ kind }: { kind: (typeof SECOND_PAGE_CARDS)[number]['artwork'] }) {
  const crops = {
    join: '67 66 410 410',
    resources: '542 63 450 450',
    team: '1045 65 440 440',
    gallery: '264 520 440 440',
    events: '820 505 490 490',
  } as const;

  return (
    <div className={`card-art-stage artwork-${kind}`}>
      <div className="artwork-halo" aria-hidden="true" />
      <svg className="card-artwork" viewBox={crops[kind]} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <image className="card-source-image" href={cardIllustrations} x="0" y="0" width="1536" height="1024" />
      </svg>
    </div>
  );
}

function updateCardGlow(event: ReactPointerEvent<HTMLElement>) {
  if (event.pointerType === 'touch') return;
  const bounds = event.currentTarget.getBoundingClientRect();
  const x = (event.clientX - bounds.left) / bounds.width;
  const y = (event.clientY - bounds.top) / bounds.height;
  event.currentTarget.style.setProperty('--pointer-x', `${x * bounds.width}px`);
  event.currentTarget.style.setProperty('--pointer-y', `${y * bounds.height}px`);
  event.currentTarget.style.setProperty('--tilt-x', `${((0.5 - y) * 12).toFixed(2)}deg`);
  event.currentTarget.style.setProperty('--tilt-y', `${((x - 0.5) * 12).toFixed(2)}deg`);
}

function resetCardTilt(event: ReactPointerEvent<HTMLElement>) {
  event.currentTarget.style.setProperty('--tilt-x', '0deg');
  event.currentTarget.style.setProperty('--tilt-y', '0deg');
}

// ─── Component ────────────────────────────────────────────────
export default function LandingPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const cardsSectionRef = useRef<HTMLElement>(null);

  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [activeHash, setActiveHash] = useState<string>(() => window.location.hash || '#home');
  const [menuOpen, setMenuOpen] = useState(false);

  // 4×4 plus-sign grid in upper-right
  const plusCols = [1422, 1475, 1528, 1580];
  const plusRows = [166, 216, 267, 319];

  // Lower-left subtle dotted grid
  const dotCols: number[] = [];
  for (let x = 12; x <= 270; x += 15.2) dotCols.push(x);
  const dotRows: number[] = [];
  for (let y = 680; y <= 918; y += 15.2) dotRows.push(y);

  // Cards section corner decorative grids
  const cardsDotCols: number[] = [];
  for (let x = 40; x <= 280; x += 18) cardsDotCols.push(x);
  const cardsDotRows: number[] = [];
  for (let y = 720; y <= 940; y += 18) cardsDotRows.push(y);

  // ── Apply theme to <html> and persist ──────────────────────
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark') {
      root.setAttribute('data-theme', 'dark');
    } else {
      root.removeAttribute('data-theme');
    }
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  // ── Lock body scroll when menu is open ─────────────────────
  useEffect(() => {
    document.body.style.overflow = menuOpen ? 'hidden' : '';
    return () => { document.body.style.overflow = ''; };
  }, [menuOpen]);

  // ── Active nav: hashchange + popstate ──────────────────────
  useEffect(() => {
    const syncHash = () => setActiveHash(window.location.hash || '#home');

    window.addEventListener('hashchange', syncHash);
    window.addEventListener('popstate', syncHash);

    const sectionIds = NAV_ITEMS.map((n) => n.hash.replace('#', ''));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActiveHash(`#${entry.target.id}`);
        }
      },
      { threshold: 0.5 }
    );

    sectionIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => {
      window.removeEventListener('hashchange', syncHash);
      window.removeEventListener('popstate', syncHash);
      observer.disconnect();
    };
  }, []);

  // ── Inertial mouse parallax loop (desktop only) ────────────
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (mediaQuery.matches) return;
    if (window.matchMedia('(pointer: coarse)').matches) return;

    let targetMouseX = 0;
    let targetMouseY = 0;
    let currentMouseX = 0;
    let currentMouseY = 0;
    let animId: number;

    const handleMouseMove = (e: MouseEvent) => {
      targetMouseX = (e.clientX / window.innerWidth) * 2 - 1;
      targetMouseY = (e.clientY / window.innerHeight) * 2 - 1;
    };
    const handleMouseLeave = () => {
      targetMouseX = 0;
      targetMouseY = 0;
    };

    window.addEventListener('mousemove', handleMouseMove, { passive: true });
    window.addEventListener('mouseleave', handleMouseLeave);

    const lerp = (s: number, e: number, f: number) => s + (e - s) * f;

    const tick = () => {
      currentMouseX = lerp(currentMouseX, targetMouseX, 0.04);
      currentMouseY = lerp(currentMouseY, targetMouseY, 0.04);

      if (containerRef.current) {
        containerRef.current.style.setProperty('--shift-circles-x', `${(currentMouseX * 12).toFixed(2)}px`);
        containerRef.current.style.setProperty('--shift-circles-y', `${(currentMouseY * 12).toFixed(2)}px`);
        containerRef.current.style.setProperty('--shift-grid-x', `${(currentMouseX * -6).toFixed(2)}px`);
        containerRef.current.style.setProperty('--shift-grid-y', `${(currentMouseY * -6).toFixed(2)}px`);
        containerRef.current.style.setProperty('--shift-crosshair-x', `${(currentMouseX * 8).toFixed(2)}px`);
        containerRef.current.style.setProperty('--shift-crosshair-y', `${(currentMouseY * 8).toFixed(2)}px`);
      }

      if (cardsSectionRef.current) {
        cardsSectionRef.current.style.setProperty('--shift-circles-x', `${(currentMouseX * 12).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-circles-y', `${(currentMouseY * 12).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-grid-x', `${(currentMouseX * -6).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-grid-y', `${(currentMouseY * -6).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-crosshair-x', `${(currentMouseX * 8).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-crosshair-y', `${(currentMouseY * 8).toFixed(2)}px`);
        cardsSectionRef.current.style.setProperty('--shift-orbit-tilt-x', `${(currentMouseY * -5).toFixed(2)}deg`);
        cardsSectionRef.current.style.setProperty('--shift-orbit-tilt-y', `${(currentMouseX * 5).toFixed(2)}deg`);
      }
      animId = requestAnimationFrame(tick);
    };

    animId = requestAnimationFrame(tick);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseleave', handleMouseLeave);
      cancelAnimationFrame(animId);
    };
  }, []);

  const handleNavClick = (hash: string) => {
    setActiveHash(hash);
    setMenuOpen(false);
  };


  return (
    <div className="site-pages">
      <div className="landing-container" ref={containerRef} id="home">

        {/* ── Background Geometry ──────────────────────────────── */}
        <svg
          className="geometric-background"
          viewBox="0 0 1648 928"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          preserveAspectRatio="xMidYMid slice"
          aria-hidden="true"
        >
          <defs>
            <style>{`
            .svg-line   { stroke: var(--line);       }
            .svg-circle { stroke: var(--line);       }
            .svg-dot    { fill:   var(--dot);        }
            .svg-plus   { stroke: var(--plus);       }
            .svg-ch     { stroke: var(--crosshair);  }
            .svg-bar    { fill:   var(--bar);        }
            .svg-cfill  { fill:   var(--circle-fill);}
          `}</style>
          </defs>

          {/* Static Construction Lines */}
          <g className="geo-static-lines">
            <line className="svg-line geo-line-h" x1="0" y1="502" x2="1648" y2="502" strokeWidth="1" />
            <line className="svg-line geo-line-v" x1="1162" y1="60" x2="1162" y2="885" strokeWidth="1" />
            <line className="svg-line geo-line-v-sub" x1="55" y1="10" x2="55" y2="180" strokeWidth="1" />
          </g>

          {/* Interactive / Animated Circles */}
          <g className="geo-interactive-circles">
            {/* Outer circle: cx=1162, cy=502, r=462 */}
            <g className="geo-circle-outer">
              <g className="geo-float-outer">
                <circle className="svg-cfill" cx="1162" cy="502" r="462" stroke="none" opacity="0.32" />
                <circle className="svg-circle" cx="1162" cy="502" r="462" strokeWidth="1" />
              </g>
            </g>

            {/* Orbital circle: cx=1015, cy=482, r=373 */}
            <g className="geo-circle-orbital">
              <g className="geo-float-orbital">
                <circle className="svg-circle" cx="1015" cy="482" r="373" strokeWidth="1" />
              </g>
            </g>

            {/* Inner circle: cx=1162, cy=502, r=236 */}
            <g className="geo-circle-inner">
              <g className="geo-float-inner">
                <circle className="svg-circle" cx="1162" cy="502" r="236" strokeWidth="1" />
              </g>
            </g>

            {/* Large sweeping circle: cx=254, cy=712, r=729 */}
            <g className="geo-circle-sweep">
              <g className="geo-float-sweep">
                <circle className="svg-circle" cx="254" cy="712" r="729" strokeWidth="1" />
              </g>
            </g>
          </g>

          {/* Markers */}
          <rect className="svg-bar geo-center-marker" x="1159" y="499" width="6" height="6" />
          <rect className="svg-bar geo-edge-bar geo-left-bar" x="54" y="340" width="30" height="9" rx="1.5" />
          <rect className="svg-bar geo-edge-bar geo-right-bar" x="1560" y="658" width="28" height="9" rx="1.5" />

          {/* Crosshairs */}
          <g className="geo-crosshairs">
            <g className="crosshair-marker crosshair-ul">
              <line className="svg-ch" x1="68" y1="125" x2="100" y2="125" strokeWidth="1.2" />
              <line className="svg-ch" x1="84" y1="107" x2="84" y2="143" strokeWidth="1.2" />
            </g>
            <g className="crosshair-marker crosshair-lr">
              <line className="svg-ch" x1="1527" y1="847" x2="1563" y2="847" strokeWidth="1.2" />
              <line className="svg-ch" x1="1545" y1="825" x2="1545" y2="869" strokeWidth="1.2" />
            </g>
          </g>

          {/* Grids */}
          <g className="geo-grids">
            <g className="plus-grid">
              {plusRows.map((py, ri) =>
                plusCols.map((px, ci) => (
                  <g key={`plus-${ri}-${ci}`} className="plus-mark">
                    <line className="svg-plus" x1={px - 4} y1={py} x2={px + 4} y2={py} strokeWidth="1.2" />
                    <line className="svg-plus" x1={px} y1={py - 4} x2={px} y2={py + 4} strokeWidth="1.2" />
                  </g>
                ))
              )}
            </g>
            <g className="dot-grid">
              {dotRows.map((dy, rIndex) =>
                dotCols.map((dx, cIndex) => {
                  const colRatio = (dx - 12) / (270 - 12);
                  const rowRatio = (dy - 680) / (918 - 680);
                  const op = Math.max(0.15, 0.82 - colRatio * 0.45 - (1 - rowRatio) * 0.35);
                  return (
                    <circle key={`dot-${rIndex}-${cIndex}`} className="svg-dot grid-dot" cx={dx} cy={dy} r="1.4" opacity={op} />
                  );
                })
              )}
            </g>
          </g>
        </svg>

        {/* ── Header ──────────────────────────────────────────── */}
        <header className="site-header">
          <div className="header-logo">
            <span className="logo-text">
              <span className="logo-stylized-a" aria-hidden="true">
                <StylizedA />
              </span>
              <span className="sr-only">A</span>TC
            </span>
          </div>

          {/* Desktop nav */}
          <nav className="header-nav" aria-label="Main Navigation">
            {NAV_ITEMS.map(({ label, hash }) => {
              const isActive = activeHash === hash;
              return (
                <div key={hash} className={`nav-item${isActive ? ' active' : ''}`}>
                  <a href={hash} onClick={() => handleNavClick(hash)}><BrandText>{label}</BrandText></a>
                  {isActive ? <div className="nav-underline" /> : <div className="nav-hover-line" />}
                </div>
              );
            })}
            <button
              className="theme-toggle"
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
            >
              <ThemeIcon theme={theme} />
            </button>
          </nav>

          {/* Mobile controls: theme toggle + hamburger */}
          <div className="mobile-controls">
            <button
              className="theme-toggle"
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
            >
              <ThemeIcon theme={theme} />
            </button>
            <button
              className="hamburger"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              {/* Three bars → X morphing via CSS */}
              <span className={`ham-bar ham-bar-1${menuOpen ? ' open' : ''}`} />
              <span className={`ham-bar ham-bar-2${menuOpen ? ' open' : ''}`} />
              <span className={`ham-bar ham-bar-3${menuOpen ? ' open' : ''}`} />
            </button>
          </div>
        </header>

        {/* ── Mobile nav drawer ───────────────────────────────── */}
        <div
          className={`mobile-nav-overlay${menuOpen ? ' open' : ''}`}
          onClick={() => setMenuOpen(false)}
          aria-hidden={!menuOpen}
        />
        <nav
          className={`mobile-nav-drawer${menuOpen ? ' open' : ''}`}
          aria-label="Mobile Navigation"
        >
          {NAV_ITEMS.map(({ label, hash }) => {
            const isActive = activeHash === hash;
            return (
              <a
                key={hash}
                href={hash}
                className={`mobile-nav-item${isActive ? ' active' : ''}`}
                onClick={() => handleNavClick(hash)}
              >
                <BrandText>{label}</BrandText>
              </a>
            );
          })}
        </nav>

        {/* ── Hero ────────────────────────────────────────────── */}
        <main className="hero-content">
          <div className="title-lockup">
            <h1 className="main-title">
              <span className="title-row title-row-1">
                <span className="letter-stylized-a" aria-hidden="true">
                  <StylizedA />
                </span>
                <span className="sr-only">A</span>
                <span>L</span>
                <span className="letter-stylized-a letter-stylized-a-second" aria-hidden="true">
                  <StylizedA />
                </span>
                <span className="sr-only">A</span>
                <span>N</span>
              </span>
              <span className="title-row title-row-2">TURING</span>
            </h1>
            <div className="title-sub">
              <span>C</span>
              <span>L</span>
              <span>U</span>
              <span>B</span>
            </div>
          </div>
        </main>
      </div>
      <section className="blank-page" ref={cardsSectionRef} aria-label="Explore the Alan Turing Club">
        {/* ── Cards Section Geometric Background ──────────────── */}
        <div className="cards-geo-container" aria-hidden="true">
          {/* Corner Decorative Accents */}
          <svg
            className="cards-geo-backdrop"
            viewBox="0 0 1600 1000"
            preserveAspectRatio="xMidYMid slice"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            {/* Top-Right Plus Grid */}
            <g className="cards-plus-grid" opacity="0.55">
              {[70, 118, 166, 214, 262].map((py, ri) =>
                [1360, 1408, 1456, 1504, 1552].map((px, ci) => (
                  <g key={`cplus-${ri}-${ci}`}>
                    <line className="svg-plus" x1={px - 4} y1={py} x2={px + 4} y2={py} strokeWidth="1.2" />
                    <line className="svg-plus" x1={px} y1={py - 4} x2={px} y2={py + 4} strokeWidth="1.2" />
                  </g>
                ))
              )}
            </g>

            {/* Bottom-Left Dot Grid */}
            <g className="cards-dot-grid">
              {cardsDotRows.map((dy, rIndex) =>
                cardsDotCols.map((dx, cIndex) => {
                  const colRatio = (dx - 40) / (280 - 40);
                  const rowRatio = (dy - 720) / (940 - 720);
                  const op = Math.max(0.12, 0.68 - colRatio * 0.42 - (1 - rowRatio) * 0.3);
                  return (
                    <circle key={`cdot-${rIndex}-${cIndex}`} className="svg-dot" cx={dx} cy={dy} r="1.3" opacity={op} />
                  );
                })
              )}
            </g>
          </svg>

          {/* 3D Rotating Sphere / Orbital Structure */}
          <div className="cards-geo-scene">
            <div className="cards-geo-rig">
              {/* Primary Outer Ring */}
              <div className="cards-geo-ring cards-ring-outer" />

              {/* Orbital Meridian Rings */}
              <div className="cards-geo-ring cards-ring-orbit-1" />
              <div className="cards-geo-ring cards-ring-orbit-2" />
              <div className="cards-geo-ring cards-ring-orbit-3" />
              <div className="cards-geo-ring cards-ring-equator" />

              {/* Inner Concentric 3D Rig */}
              <div className="cards-geo-inner-rig">
                <div className="cards-geo-ring cards-ring-inner" />
                <div className="cards-geo-ring cards-ring-inner-orbit" />
              </div>
            </div>
          </div>
        </div>

        <div className="second-page-grid">
          {SECOND_PAGE_CARDS.map(({ title, artwork }) => (
            <article
              className="landing-card"
              key={title}
              onPointerMove={updateCardGlow}
              onPointerLeave={resetCardTilt}
            >
              <CardArtwork kind={artwork} />
              <h2><BrandText>{title}</BrandText></h2>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
