"""Portable offline reports. No image pixels or original OME XML are embedded."""

from __future__ import annotations

import html
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import __version__ as pillow_version

from . import __version__
from .audit import RELATIVE_TOLERANCE, Result, sha256


def _csv_safe(value: str) -> str:
    # Quoting CSV fields alone does not prevent spreadsheet formula execution.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def write_reports(results: list[Result], manifest: Path, output: Path, *, max_pixels: int) -> dict:
    manifest = manifest.resolve()
    output = output.resolve()
    if output.is_relative_to(manifest.parent):
        raise ValueError("Report directory must be outside the dataset directory.")
    output.mkdir(parents=True, exist_ok=True)
    destinations = [output / name for name in ("audit.json", "audit.csv", "report.html")]
    if any(path.is_symlink() for path in destinations):
        raise ValueError("Refusing to overwrite a report symlink.")
    counts = {key: sum(r.status == key for r in results) for key in ("no_findings", "review", "blocked")}
    document = {
        "schema_version": "1.0", "tool_version": __version__,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pandas": pd.__version__, "Pillow": pillow_version},
        "manifest_sha256": sha256(manifest),
        "policy": {"relative_calibration_tolerance": RELATIVE_TOLERANCE, "max_pixels": max_pixels,
                   "scope": "single-plane grayscale PNG/TIFF, combined ADS masks",
                   "no_findings_means": "No implemented check found a problem, not scientific validation."},
        "summary": counts, "results": [r.to_dict() for r in results],
    }
    destinations[0].write_text(json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    rows = [{"image": _csv_safe(r.image), "mask": _csv_safe(r.mask), "status": r.status,
             "width": r.width, "height": r.height, "pixel_size_um": r.pixel_size_um,
             "field_width_um": r.field_width_um, "field_height_um": r.field_height_um,
             "codes": ";".join(f.code for f in r.findings)} for r in results]
    pd.DataFrame(rows).to_csv(destinations[1], index=False)
    e = html.escape
    cards = []
    for r in results:
        findings = "".join(
            f'<li><strong>{e(f.code)}</strong><p>{e(f.message)}</p><p class="action">{e(f.action)}</p></li>'
            for f in r.findings
        ) or '<li>No implemented check found a problem. Confirm calibration against acquisition records.</li>'
        sources = "".join(f'<li>{e(name)}: {x:g} x {y:g} um/pixel</li>' for name, (x, y) in r.calibrations.items())
        dimensions = f'{r.width} x {r.height} pixels' if r.width else 'Image dimensions unavailable'
        physical = (f'{r.field_width_um:g} x {r.field_height_um:g} um field of view'
                    if r.field_width_um is not None else 'Physical field of view unresolved')
        cards.append(f'''<article data-status="{r.status}"><div class="cardhead"><h2>{e(r.image)}</h2>
        <span class="badge {r.status}">{r.status.replace('_', ' ')}</span></div>
        <p class="meta">{dimensions} | {physical}</p><p class="meta">Mask: {e(r.mask or 'not provided')}</p>
        <h3>Calibration evidence</h3><ul>{sources or '<li>No usable calibration source</li>'}</ul>
        <h3>Findings and next actions</h3><ul class="findings">{findings}</ul></article>''')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Morphometry Preflight report</title><style>
    *{box-sizing:border-box}body{font:16px/1.6 system-ui,sans-serif;background:#f3f6f8;color:#20323b;margin:0}
    main{max-width:1050px;margin:auto;padding:40px 24px}h1{font-size:38px;letter-spacing:-1px;line-height:1.15;margin:8px 0 16px}
    h2{font-size:19px;overflow-wrap:anywhere;margin:0}h3{font-size:14px;text-transform:uppercase;letter-spacing:1px;margin:24px 0 8px}
    .eyebrow{color:#386675;text-transform:uppercase;letter-spacing:2px;font-size:12px;font-weight:700}.intro{max-width:780px}
    .stats{display:flex;gap:16px;flex-wrap:wrap;margin:28px 0}.stat{flex:1;min-width:160px;background:white;border:1px solid #d6e1e6;padding:20px;border-radius:12px}
    .stat b{display:block;font-size:32px;line-height:1.2}.stat span,.meta{color:#526771}.meta{font-size:14px}
    article{background:white;padding:26px;border:1px solid #d6e1e6;border-radius:12px;margin:20px 0}
    .cardhead{display:flex;gap:16px;align-items:flex-start;justify-content:space-between}.badge{padding:4px 12px;border-radius:20px;font-size:12px;white-space:nowrap;text-transform:uppercase;font-weight:700}
    .blocked{background:#fce9e6;color:#9e3322}.review{background:#fff0ce;color:#765400}.no_findings{background:#ddf3ec;color:#176047}
    ul{padding-left:20px}.findings li{margin:16px 0}.findings p{margin:4px 0}.action{color:#526771}
    button{font:inherit;background:white;border:1px solid #9db2bd;border-radius:20px;padding:6px 16px;cursor:pointer;margin:0 6px 6px 0}
    button[aria-pressed=true]{background:#203e4c;color:white}footer{font-size:13px;color:#526771;margin:36px 0}
    @media(max-width:600px){.cardhead{display:block}.badge{display:inline-block;margin-top:8px}h1{font-size:30px}article{padding:18px}}
    </style><main><header><div class="eyebrow">Microscopy / Input audit</div><h1>Morphometry Preflight</h1>
    <p class="intro">Check calibration evidence and combined segmentation masks before extracting measurements.
    This report does not modify images and does not establish scientific or clinical validity.</p></header>
    <section class="stats" aria-label="Summary">SUMMARY</section>
    <nav aria-label="Filter results"><button aria-pressed="true" data-filter="all">All inputs</button>
    <button aria-pressed="false" data-filter="blocked">Blocked</button><button aria-pressed="false" data-filter="review">Review</button>
    <button aria-pressed="false" data-filter="no_findings">No findings</button></nav>
    <section aria-label="Input results">CARDS</section><footer>
    Calibration tolerance: 1% relative to the smaller value. All sources are shown; conflicting sources are not resolved automatically.
    PNG/TIFF grayscale 2D inputs only. File hashes, tool versions and evidence are recorded in audit.json.
    Reports may contain sensitive filenames. Review them before sharing. No external scripts, tracking or network requests.
    </footer></main><script>
    for(const button of document.querySelectorAll('button[data-filter]'))button.addEventListener('click',()=>{
      for(const item of document.querySelectorAll('article[data-status]'))item.hidden=button.dataset.filter!=='all'&&item.dataset.status!==button.dataset.filter;
      for(const other of document.querySelectorAll('button[data-filter]'))other.setAttribute('aria-pressed',String(other===button));
    });</script></html>'''
    summary = "".join(f'<div class="stat"><b>{counts[key]}</b><span>{label}</span></div>'
                      for key, label in (("blocked", "Blocked inputs"), ("review", "Need review"), ("no_findings", "No findings")))
    destinations[2].write_text(page.replace("SUMMARY", summary).replace("CARDS", "".join(cards)), encoding="utf-8")
    return document
