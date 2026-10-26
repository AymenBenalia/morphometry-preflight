# Prototype verification

Verified locally on 26 October 2026:

- 40 automated unittest tests passed on Python 3.12.
- Built a wheel and installed it into an isolated temporary target without dependencies or build isolation. Existing runtime dependencies were used; a fresh online dependency installation was not tested.
- Ran the six-case synthetic demo from both the source package and the installed package. Both produced four blocked inputs, one review input and one input without findings. Exit code 2 is expected because the demo contains deliberate errors.
- Opened the offline report through a loopback-only local server in Chrome. Verified the Review and Blocked filters against the visible results and inspected the rendered layout.
- Confirmed tests cover unchanged source hashes and byte-identical repeated reports in the same environment.

The first public [GitHub Actions run](https://github.com/AymenBenalia/morphometry-preflight/actions/runs/40337116676) completed successfully with the configured Python 3.11, 3.12 and 3.13 test matrix. No real laboratory data, microscope calibration reference, model inference or scientific end-user validation has been tested. The report statuses describe implemented input checks only.
