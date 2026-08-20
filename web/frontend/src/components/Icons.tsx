/* 统一 SVG 图标库（替代 emoji，保证深浅主题一致的高级质感） */
import React from 'react'

type P = { size?: number; className?: string; style?: React.CSSProperties }

function Svg({ size = 16, children, className, style }: P & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const IcoOverview = (p: P) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
    <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
    <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" />
  </Svg>
)

export const IcoLab = (p: P) => (
  <Svg {...p}>
    <path d="M10 2v6.5L4.5 17a2.2 2.2 0 0 0 1.9 3.4h11.2a2.2 2.2 0 0 0 1.9-3.4L14 8.5V2" />
    <path d="M8.5 2h7" />
    <path d="M7.5 14.5h9" />
  </Svg>
)

export const IcoPaper = (p: P) => (
  <Svg {...p}>
    <path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
    <path d="M14 3v4h4" />
    <path d="M9 12h6M9 16h6" />
  </Svg>
)

export const IcoSearch = (p: P) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Svg>
)

export const IcoSun = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5 5l1.6 1.6M17.4 17.4 19 19M19 5l-1.6 1.6M6.6 17.4 5 19" />
  </Svg>
)

export const IcoMoon = (p: P) => (
  <Svg {...p}>
    <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
  </Svg>
)

export const IcoScan = (p: P) => (
  <Svg {...p}>
    <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
    <circle cx="12" cy="12" r="3.2" fill="currentColor" stroke="none" />
  </Svg>
)

export const IcoStop = (p: P) => (
  <Svg {...p}>
    <rect x="7" y="7" width="10" height="10" rx="2" />
  </Svg>
)

export const IcoShield = (p: P) => (
  <Svg {...p}>
    <path d="M12 3 5 5.8v5.4c0 4.4 3 7.6 7 9.3 4-1.7 7-4.9 7-9.3V5.8L12 3Z" />
    <path d="m9.3 11.8 2 2 3.6-3.8" />
  </Svg>
)

export const IcoWarn = (p: P) => (
  <Svg {...p}>
    <path d="M12 4 2.8 19.4a1 1 0 0 0 .86 1.5h16.68a1 1 0 0 0 .86-1.5L12 4Z" />
    <path d="M12 10v4M12 17.2v.1" />
  </Svg>
)

export const IcoCheck = (p: P) => (
  <Svg {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Svg>
)

export const IcoPulse = (p: P) => (
  <Svg {...p}>
    <path d="M3 12h4l2.5-6.5 4.5 13 2.5-6.5H21" />
  </Svg>
)

export const IcoLayers = (p: P) => (
  <Svg {...p}>
    <path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 12.5 9 5 9-5" />
    <path d="m3 16.5 9 5 9-5" />
  </Svg>
)

export const IcoFlame = (p: P) => (
  <Svg {...p}>
    <path d="M12 3s5 4.5 5 9a5 5 0 0 1-10 0c0-2 1-3.6 2.2-5C9.5 8.6 12 9.5 12 9.5S10 6.5 12 3Z" />
  </Svg>
)

export const IcoTarget = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <circle cx="12" cy="12" r="4.5" />
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
  </Svg>
)

export const IcoWallet = (p: P) => (
  <Svg {...p}>
    <path d="M4 6.5h13a2 2 0 0 1 2 2V8a2 2 0 0 0-2-2H5a1.5 1.5 0 0 1 0-3h11" />
    <path d="M4 6.5v11A1.5 1.5 0 0 0 5.5 19H19a1 1 0 0 0 1-1v-6.5" />
    <path d="M16 13.5h4v-4h-3.6a2 2 0 0 0 0 4Z" />
  </Svg>
)

export const IcoArrowRight = (p: P) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
)

export const IcoRefresh = (p: P) => (
  <Svg {...p}>
    <path d="M20 12a8 8 0 1 1-2.3-5.6" />
    <path d="M20 3v4.5h-4.5" />
  </Svg>
)
