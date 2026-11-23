"""Small local outline icons for custom HTML; navigation uses native Material icons."""

PATHS = {
    "bolt": '<path d="m13 2-9 12h7l-1 8 10-12h-7z"/>',
    "database": '<ellipse cx="12" cy="5.5" rx="7" ry="2.5"/>'
                '<path d="M5 5.5v13c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-13"/>'
                '<path d="M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
    "model": '<path d="M9 18h6M10 21h4"/>'
             '<path d="M12 3a6 6 0 0 0-3.6 10.8c.7.5 1.1 1.3 1.1 2.2h5c0-.9.4-1.7 1.1-2.2'
             'A6 6 0 0 0 12 3z"/>',
    "payments": '<rect x="2" y="5" width="16" height="11" rx="2"/>'
                '<circle cx="10" cy="10.5" r="2.5"/><path d="M6 19.5h14a1.5 1.5 0 0 0 1.5-1.5V9"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "chart": '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
    "tower": '<path d="M12 3 7.5 21M12 3l4.5 18M9 15h6M10.2 9.5h3.6M5 7l7-4 7 4"/>',
    "thermo": '<path d="M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/>'
              '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    "display": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "chevron": '<path d="m9 6 6 6-6 6"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "close": '<path d="M7 7l10 10M17 7 7 17"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v4h4M12 8v4l3 2"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "move": '<path d="M12 3v18M8.5 6.5 12 3l3.5 3.5M8.5 17.5 12 21l3.5-3.5M3 12h18'
            'M6.5 8.5 3 12l3.5 3.5M17.5 8.5 21 12l-3.5 3.5"/>',
}


def svg(name: str, size: int | None = None, stroke: float = 1.8) -> str:
    dimensions = f' width="{size}" height="{size}"' if size else ""
    return (f'<svg viewBox="0 0 24 24"{dimensions} fill="none" stroke="currentColor" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{PATHS[name]}</svg>')
