"""Generate six explicitly synthetic cases with known expected findings.

No patient or employer data are used. Does not simulate model accuracy.
"""
from pathlib import Path
import csv

import numpy as np
from PIL import Image, TiffImagePlugin


def make_demo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(42)
    image = rng.integers(50, 210, size=(96, 128), dtype=np.uint8)
    yy, xx = np.ogrid[:96, :128]
    distance = (xx - 64) ** 2 + (yy - 48) ** 2
    mask = np.where(distance < 15 ** 2, 255, np.where(distance < 21 ** 2, 127, 0)).astype(np.uint8)
    rows = []
    cases = ["consistent", "scale_conflict", "wrong_grid", "missing_scale", "wrong_labels", "empty_mask"]
    for case in cases:
        folder = root / case
        folder.mkdir(exist_ok=True)
        xml = ('<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"><Image ID="Image:0">'
               '<Pixels SizeX="128" SizeY="96" SizeZ="1" SizeC="1" SizeT="1" '
               'PhysicalSizeX="100" PhysicalSizeY="100" PhysicalSizeXUnit="nm" PhysicalSizeYUnit="nm"/>'
               '</Image></OME>')
        info = TiffImagePlugin.ImageFileDirectory_v2()
        if case != "missing_scale":
            info[270] = xml
            (folder / 'pixel_size_in_micrometer.txt').write_text('0.1\n', encoding='utf-8')
        Image.fromarray(image).save(folder / 'image.tif', tiffinfo=info)
        current = mask.copy()
        if case == "wrong_grid": current = current[:80]
        if case == "wrong_labels": current[current == 127] = 128
        if case == "empty_mask": current[:] = 0
        Image.fromarray(current).save(folder / 'mask.png')
        rows.append({"image": f"{case}/image.tif", "mask": f"{case}/mask.png",
                     "pixel_size_um": "0.2" if case == "scale_conflict" else ""})
    manifest = root / 'manifest.csv'
    with manifest.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['image', 'mask', 'pixel_size_um'])
        writer.writeheader()
        writer.writerows(rows)
    return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(make_demo(parser.parse_args().output))
