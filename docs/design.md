# Design decisions

## Intended user and moment of use

A developer or researcher reviewing 2D microscopy images and combined ADS segmentation masks before physical morphometry. The tool answers: "Are the supplied inputs internally consistent enough to proceed with manual review?" It does not answer whether the biological measurement is valid.

## Candidate selection

Three directions were considered:

1. A general segmentation evaluator. Existing packages already calculate overlap and distance metrics, export CSV and compare models. A new implementation would need a stronger differentiator.
2. A generic imaging-data validator. BIDS Validator, MRIQC and microscopy metadata tools cover substantial existing ground. Implementing a broad validator would risk shallow duplication.
3. A narrow calibration-and-mask preflight for morphometry. ADS documents physical calibration supplied separately from the image; OME may carry another calibration. An explicit comparison of these sources and the combined mask is small enough to test and explain. This direction was selected as a prototype, not as a proven unmet demand.

## Scientific assumptions

Physical length equals pixel length multiplied by physical pixel size. For an unchanged square-pixel mask, physical area equals pixel area multiplied by pixel size squared. These formulas motivate calibration checks but are not used to claim segmentation accuracy.

The 1% tolerance is a fixed, transparent engineering policy. The comparison uses max minus min relative to min for each axis. It avoids treating one arbitrary source as a ground truth. AxonDeepSeg's documented scalar calibration motivates flagging anisotropic X/Y pixels. It does not imply anisotropic data are inherently scientifically invalid.

## Deliberate exclusions

- No automatic corrections, resampling or generated ADS commands.
- No copied employer code, private datasets or fabricated research results.
- No training a neural network without a scientific task or suitable data.
- No inferred calibration from DPI or a filename.
- No silent acceptance of invalid provided metadata just because another source works.
- No claim of affiliation, adoption by NeuroPoly, or validated benefit to researchers.

## Architecture

`audit.py` owns path validation, image decoding, calibration extraction and finding rules. It returns typed results without writing inputs. `report.py` serializes that result to JSON, CSV and offline HTML. `cli.py` wires those components together and translates severity into exit codes. The synthetic demo generator is separate from the audit package.

NumPy is used for finite-value, intensity-range and label checks, pandas for tabular exports, and Pillow for decoding. The prototype has no model-runtime dependency. Tests use unittest to keep development dependencies small.

## Reproducibility

The seeded demo produces known fixtures. Reports omit wall-clock timestamps, preserve manifest order and record the audit version, dependency versions and input hashes. Repeating an audit on unchanged inputs in the same environment produces identical reports. Reading files concurrently with modifications is outside the reproducibility guarantee; run on a stable dataset snapshot.

## What to validate with a laboratory

First compare the prototype's findings against known export mistakes in a small real batch. Establish which calibration source the acquisition team regards as authoritative, whether 1% is appropriate, and whether multi-series TIFF or additional masks are necessary. Only then consider integration into ADS or another established workflow.
