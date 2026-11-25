// Small inline SVG icons (24 px grid, stroke follows the text colour). Decorative: always paired with a label.
const PATHS = {
  map: 'M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Zm0 0v14m6-12v14',
  gauge: 'M4 18a8 8 0 1 1 16 0M12 18l4-6M12 10V8',
  chart: 'M4 20V4m0 16h16M8 16v-5m4 5V8m4 8v-3',
  play: 'M7 5v14l11-7L7 5Z',
  shield: 'M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6l-7-3Zm-3 9 2 2 4-4',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8a7 7 0 0 1 14 0',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6 6 18',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  fit: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  layers: 'm12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
  chevron: 'm9 6 6 6-6 6',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-5v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4',
  moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z',
  monitor: 'M3 5h18v11H3V5Zm6 15h6m-3-4v4',
  signal: 'M9 3h6v18H9V3Zm3 3.5v.01M12 12v.01M12 17.5v.01',
  wifi: 'M4 9a12 12 0 0 1 16 0M7 12.5a7.5 7.5 0 0 1 10 0M10 16a3 3 0 0 1 4 0M12 19.5v.01',
  ambulance: 'M3 7h11v9H3V7Zm11 3h4l3 3v3h-7v-6ZM7 18.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Zm10 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3ZM8.5 9.5v4m-2-2h4',
  route: 'M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm12-10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 17h5a3 3 0 0 0 0-6h-2a3 3 0 0 1 0-6h5',
  alert: 'M12 4 3 20h18L12 4Zm0 6v5m0 2.5v.01',
  cone: 'M10 4h4l4 14H6l4-14ZM4 20h16M8.6 9h6.8M7.4 13.5h9.2',
  leaf: 'M5 19c0-8 5-13 15-14 0 9-5 14-13 14H5Zm0 0 8-8',
  car: 'M5 11l1.6-5h10.8L19 11M4 11h16v6H4v-6Zm2 6v2m12-2v2M7.5 14h.01M16.5 14h.01',
  dna: 'M7 3c0 5 10 5 10 10s-10 5-10 8M17 3c0 5-10 5-10 10s10 5 10 8M8.5 6h7m-7 12h7M9 12h6',
  list: 'M8 6h12M8 12h12M8 18h12M4 6v.01M4 12v.01M4 18v.01',
  book: 'M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Zm0 13a3 3 0 0 1 3-3h11',
  flag: 'M5 21V4m0 1h12l-2.5 4L17 13H5',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Zm4 4h4',
};

export default function Icon({ name, size = 18, className }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={PATHS[name] || ''} />
    </svg>
  );
}
