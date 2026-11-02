"""Small local outline icons for custom cards; navigation uses native icons."""

PATHS = {
    "bolt": '<path d="m13 2-9 12h7l-1 8 10-12h-7z"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/>'
                '<path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
    "model": '<path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 4h.01"/>',
    "chart": '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="3"/>'
            '<path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
    "tower": '<path d="M12 2 7 22M12 2l5 20M8.6 9h6.8M7.6 14h8.8M6.4 19h11.2"/>',
    "thermo": '<path d="M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0z"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/>'
              '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    "chevron": '<path d="m9 6 6 6-6 6"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7"/>',
    "close": '<path d="M7 7l10 10M17 7 7 17"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
}


def svg(name: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{PATHS[name]}</svg>')
