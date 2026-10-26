"""Known-failure tests: calibration ambiguity must not become silent acceptance."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, TiffImagePlugin

from morphometry_preflight.audit import _ome_scale, audit_manifest, sha256
from morphometry_preflight.cli import main
from morphometry_preflight.report import write_reports

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'examples'))
from make_demo import make_demo


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'data'
        self.root.mkdir()
        self.image = np.arange(100, dtype=np.uint8).reshape(10, 10)
        Image.fromarray(self.image).save(self.root / 'image.png')
        Image.fromarray(np.where(self.image > 50, 255, 127).astype(np.uint8)).save(self.root / 'mask.png')

    def manifest(self, **changes):
        row = {'image': 'image.png', 'mask': 'mask.png', 'pixel_size_um': '0.1'}
        row.update(changes)
        path = self.root / 'manifest.csv'
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return path

    def result(self, **changes):
        return audit_manifest(self.manifest(**changes))[0]

    def codes(self, result):
        return {f.code for f in result.findings}

    def ome(self, **changes):
        attributes = dict(SizeX='10', SizeY='10', SizeZ='1', SizeC='1', SizeT='1',
                          PhysicalSizeX='100', PhysicalSizeY='100', PhysicalSizeXUnit='nm', PhysicalSizeYUnit='nm')
        attributes.update(changes)
        attrs = ' '.join(f'{k}="{v}"' for k, v in attributes.items())
        return f'<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06"><Image><Pixels {attrs}/></Image></OME>'

    def save_ome(self, **changes):
        info = TiffImagePlugin.ImageFileDirectory_v2()
        info[270] = self.ome(**changes)
        Image.fromarray(self.image).save(self.root / 'image.tif', tiffinfo=info)

    def test_valid_input_and_field_of_view(self):
        result = self.result()
        self.assertEqual(result.status, 'no_findings')
        self.assertEqual(result.field_width_um, 1.0)
        self.assertEqual(result.field_height_um, 1.0)

    def test_no_source_changes(self):
        manifest = self.manifest()
        before = {p: sha256(p) for p in self.root.iterdir()}
        audit_manifest(manifest)
        self.assertEqual(before, {p: sha256(p) for p in self.root.iterdir()})

    def test_missing_calibration_blocks(self):
        result = self.result(pixel_size_um='')
        self.assertIn('CALIBRATION_MISSING', self.codes(result))
        self.assertIsNone(result.pixel_size_um)

    def test_invalid_calibrations(self):
        for value in ['0', '-1', 'nan', 'inf', '1e999', 'hello']:
            with self.subTest(value=value):
                self.assertIn('CALIBRATION_MANIFEST_INVALID', self.codes(self.result(pixel_size_um=value)))

    def test_sidecar_only(self):
        (self.root / 'pixel_size_in_micrometer.txt').write_text('0.1')
        self.assertEqual(self.result(pixel_size_um='').status, 'no_findings')

    def test_huge_scale_blocks_without_nonfinite_report(self):
        manifest = self.manifest(pixel_size_um='1e308')
        results = audit_manifest(manifest)
        self.assertIn('FIELD_OF_VIEW_OVERFLOW', self.codes(results[0]))
        write_reports(results, manifest, self.parent / 'report', max_pixels=1000)

    def test_conflict_has_evidence_and_no_resolution(self):
        (self.root / 'pixel_size_in_micrometer.txt').write_text('0.2')
        result = self.result()
        self.assertIn('CALIBRATION_CONFLICT', self.codes(result))
        self.assertEqual(len(result.calibrations), 2)
        self.assertIsNone(result.pixel_size_um)

    def test_bad_sidecar_cannot_be_hidden_by_manifest(self):
        (self.root / 'pixel_size_in_micrometer.txt').write_text('oops')
        self.assertIn('CALIBRATION_SIDECAR_INVALID', self.codes(self.result()))

    def test_ome_unit_conversion(self):
        self.assertEqual(_ome_scale(self.ome(), (10, 10)), (0.1, 0.1))
        self.save_ome()
        self.assertEqual(self.result(image='image.tif').status, 'no_findings')

    def test_ome_default_unit_is_micrometer(self):
        xml = self.ome(PhysicalSizeX='0.1', PhysicalSizeY='0.1').replace('PhysicalSizeXUnit="nm"', '').replace('PhysicalSizeYUnit="nm"', '')
        self.assertEqual(_ome_scale(xml, (10, 10)), (0.1, 0.1))

    def test_ome_unknown_unit(self):
        self.save_ome(PhysicalSizeXUnit='pixel')
        self.assertIn('CALIBRATION_METADATA_INVALID', self.codes(self.result(image='image.tif')))

    def test_ome_anisotropy(self):
        self.save_ome(PhysicalSizeY='200')
        result = self.result(image='image.tif', pixel_size_um='')
        self.assertIn('PIXELS_ANISOTROPIC', self.codes(result))
        self.assertIsNone(result.field_width_um)

    def test_ome_dimensions_checked(self):
        self.save_ome(SizeX='11')
        self.assertIn('CALIBRATION_METADATA_INVALID', self.codes(self.result(image='image.tif')))

    def test_ome_channels_rejected(self):
        self.save_ome(SizeC='2')
        self.assertIn('CALIBRATION_METADATA_INVALID', self.codes(self.result(image='image.tif')))

    def test_ome_entities_rejected(self):
        with self.assertRaises(ValueError):
            _ome_scale('<!DOCTYPE OME [<!ENTITY x "y">]>' + self.ome(), (10, 10))

    def test_ome_malformed_xml(self):
        info = TiffImagePlugin.ImageFileDirectory_v2()
        info[270] = '<OME'
        Image.fromarray(self.image).save(self.root / 'bad.tif', tiffinfo=info)
        self.assertIn('CALIBRATION_METADATA_INVALID', self.codes(self.result(image='bad.tif')))

    def test_dpi_is_not_calibration(self):
        Image.fromarray(self.image).save(self.root / 'dpi.png', dpi=(300, 300))
        self.assertIn('CALIBRATION_MISSING', self.codes(self.result(image='dpi.png', pixel_size_um='')))

    def test_tolerance(self):
        (self.root / 'pixel_size_in_micrometer.txt').write_text('0.1005')
        self.assertEqual(self.result().status, 'no_findings')
        (self.root / 'pixel_size_in_micrometer.txt').write_text('0.102')
        self.assertIn('CALIBRATION_CONFLICT', self.codes(self.result()))

    def test_missing_image(self):
        self.assertIn('IMAGE_UNREADABLE', self.codes(self.result(image='missing.png')))

    def test_broken_image(self):
        (self.root / 'bad.png').write_bytes(b'not a png')
        self.assertIn('IMAGE_UNREADABLE', self.codes(self.result(image='bad.png')))

    def test_rgb_rejected(self):
        Image.new('RGB', (10, 10)).save(self.root / 'rgb.png')
        self.assertIn('IMAGE_UNREADABLE', self.codes(self.result(image='rgb.png')))

    def test_multiframe_rejected(self):
        Image.fromarray(self.image).save(self.root / 'stack.tif', save_all=True, append_images=[Image.fromarray(self.image)])
        self.assertIn('IMAGE_UNREADABLE', self.codes(self.result(image='stack.tif')))

    def test_nan_image(self):
        pixels = self.image.astype(np.float32)
        pixels[0, 0] = np.nan
        Image.fromarray(pixels).save(self.root / 'nan.tif')
        self.assertIn('IMAGE_NONFINITE', self.codes(self.result(image='nan.tif')))

    def test_constant_image_warns(self):
        Image.new('L', (10, 10)).save(self.root / 'constant.png')
        self.assertIn('IMAGE_CONSTANT', self.codes(self.result(image='constant.png')))

    def test_pixel_limit_before_decode(self):
        result = audit_manifest(self.manifest(), max_pixels=99)[0]
        self.assertIn('IMAGE_UNREADABLE', self.codes(result))

    def test_mask_shape_mismatch(self):
        Image.new('L', (9, 10), 255).save(self.root / 'mask.png')
        self.assertIn('MASK_SHAPE_MISMATCH', self.codes(self.result()))

    def test_mask_labels(self):
        Image.new('L', (10, 10), 128).save(self.root / 'mask.png')
        self.assertIn('MASK_LABELS_INVALID', self.codes(self.result()))

    def test_empty_mask_review(self):
        Image.new('L', (10, 10)).save(self.root / 'mask.png')
        self.assertEqual(self.result().status, 'review')

    def test_no_axon_review(self):
        Image.new('L', (10, 10), 127).save(self.root / 'mask.png')
        self.assertIn('MASK_NO_AXON', self.codes(self.result()))

    def test_missing_mask_review(self):
        self.assertIn('MASK_NOT_PROVIDED', self.codes(self.result(mask='')))

    def test_same_image_and_mask_rejected(self):
        self.assertIn('MASK_PATH_INVALID', self.codes(self.result(mask='image.png')))

    def test_path_traversal_absolute_and_symlink(self):
        for path in ['../outside.png', '/tmp/image.png']:
            self.assertIn('IMAGE_PATH_INVALID', self.codes(self.result(image=path)))
        (self.root / 'escape.png').symlink_to(self.parent / 'outside.png')
        self.assertIn('IMAGE_PATH_INVALID', self.codes(self.result(image='escape.png')))

    def test_duplicate_rows(self):
        path = self.manifest()
        with path.open('a') as stream:
            stream.write('image.png,mask.png,0.1\n')
        self.assertIn('IMAGE_DUPLICATED', self.codes(audit_manifest(path)[1]))

    def test_manifest_schema(self):
        path = self.root / 'manifest.csv'
        for text in ['image,mask,pixel_szie\na,b,1\n', 'image,image\na,a\n', 'image\n', 'image,mask\na,b,c\n']:
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                audit_manifest(path)

    def test_reports_are_reproducible_and_offline(self):
        manifest = self.manifest()
        results = audit_manifest(manifest)
        out = self.parent / 'report'
        write_reports(results, manifest, out, max_pixels=1000)
        first = {p.name: p.read_bytes() for p in out.iterdir()}
        write_reports(results, manifest, out, max_pixels=1000)
        self.assertEqual(first, {p.name: p.read_bytes() for p in out.iterdir()})
        data = json.loads((out / 'audit.json').read_text())
        self.assertEqual(data['results'][0]['hashes']['image'], sha256(self.root / 'image.png'))
        self.assertNotIn('https://', (out / 'report.html').read_text())

    def test_report_html_escapes_names(self):
        results = [self.result()]
        results[0].image = '<img src=x onerror=alert(1)>'
        out = self.parent / 'report'
        write_reports(results, self.root / 'manifest.csv', out, max_pixels=1000)
        self.assertIn('&lt;img', (out / 'report.html').read_text())
        self.assertNotIn('<img src=x', (out / 'report.html').read_text())

    def test_csv_formula_escape(self):
        results = [self.result()]
        results[0].image = '=HYPERLINK("bad")'
        out = self.parent / 'report'
        write_reports(results, self.root / 'manifest.csv', out, max_pixels=1000)
        with (out / 'audit.csv').open() as stream:
            self.assertTrue(next(csv.DictReader(stream))['image'].startswith("'="))

    def test_output_inside_dataset_rejected(self):
        manifest = self.manifest()
        with self.assertRaises(ValueError):
            write_reports(audit_manifest(manifest), manifest, self.root / 'reports', max_pixels=1000)

    def test_cli_exit_statuses(self):
        out = self.parent / 'reports'
        self.assertEqual(main([str(self.manifest()), '--output', str(out)]), 0)
        self.assertEqual(main([str(self.manifest(mask='')), '--output', str(out)]), 1)
        self.assertEqual(main([str(self.manifest(pixel_size_um='')), '--output', str(out)]), 2)

    def test_demo_expected_failures(self):
        results = audit_manifest(make_demo(self.parent / 'demo'))
        by_case = {Path(r.image).parts[0]: r for r in results}
        self.assertEqual(by_case['consistent'].status, 'no_findings')
        for case, code in [('scale_conflict', 'CALIBRATION_CONFLICT'), ('wrong_grid', 'MASK_SHAPE_MISMATCH'),
                           ('missing_scale', 'CALIBRATION_MISSING'), ('wrong_labels', 'MASK_LABELS_INVALID'),
                           ('empty_mask', 'MASK_EMPTY')]:
            self.assertIn(code, self.codes(by_case[case]))


if __name__ == '__main__':
    unittest.main()
