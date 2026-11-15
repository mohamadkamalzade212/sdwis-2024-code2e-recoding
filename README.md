# SDWIS 2024 code-2E indicator sensitivity — five-state replication materials

**Status:** public replication materials, archived release `v0.1.0` with DOI **[10.5281/zenodo.24214908](https://doi.org/10.5281/zenodo.24214908)**. The national EPA raw archive was processed on the author's local machine, not independently reprocessed in this repository. This repository is a research working release, not a peer-reviewed publication.

## Scope and findings

This bounded analysis uses a fixed 2026Q3 ECHO/SDWIS snapshot to measure how the **2024-onset** health-based administrative violation indicator differs when violation code `2E` is excluded but all other health-based codes are retained. States: Mississippi (MS), Alabama (AL), Louisiana (LA), Arkansas (AR), and Tennessee (TN). CWS and NTNCWS systems were selected if active in the source snapshot, not because their 2024 activity was independently verified.

- Selected active systems: **3,781**.
- 2024-onset source-classified health-based PWS flags: **646**.
- After exclusion of only code `2E`: **248**.
- Systems losing their flag: **398**.
- Systems with any qualifying 2024 `2E` event: **451**; **53** retained another health-based 2024-onset event.
- Four of five within-sample state ranks change; three of ten pairwise state rate comparisons reverse.

These are **not** national rankings, official EPA annual prevalence, actual contamination findings, or causal effects. The code-excluded indicator is not an EPA-corrected official statistic. Do not characterize a rank of 1 as objectively better drinking-water quality.

## Source and source immutability caveat

Official data portal: https://echo.epa.gov/tools/data-downloads

Source archive filename: `SDWA_latest_downloads.zip` (the **latest** endpoint is mutable).

**The exact 2026Q3 source copy used locally** had SHA-256:

```
4d92d5f46b423742d5fd608257dc63ef666a832d9211199c28434cd8f41dccb0
```

Tables: `SDWA_PUB_WATER_SYSTEMS.csv`, `SDWA_VIOLATIONS_ENFORCEMENT.csv`. The historical source ZIP is NOT included here, so exact raw-source replication requires access to the same archived ZIP or a separately obtained verified copy. Downloading the mutable `latest` ZIP at a later date may produce a different file and different output. Do not treat a checksum alone as a permanent archive.

## Repository layout

- `scripts/run_exact_recoding.py`: main 5-state data-extraction/recoding script (standard Python only).
- `scripts/run_robustness_checks.py`: three-cohort sensitivity checks (requires original raw ZIP and baseline outputs).
- `scripts/inspect_unkeyed_rows.py`: checks unkeyed rows for violation vs enforcement fields.
- `scripts/verify_derived_outputs.py`: **lightweight** independent reconciliation of supplied CSV/JSON outputs (no national ZIP required).
- `data/derived/`: system flags and aggregate/sensitivity CSVs; public-agency derived records, not individual personal data.
- `data/provenance/`: audit JSON files with source hash and quality checks.
- `tests/`: synthetic tests of recoding logic; no empirical claims from test fixtures.
- `docs/`: definitions, methodological limitations, and workflow transparency.

## Reproduce published aggregates from derived outputs

Python 3, no third-party dependencies:

```bash
python scripts/verify_derived_outputs.py
```

To test basic recoding logic:

```bash
python -m unittest discover -s tests -v
```

The second command imports `run_exact_recoding.py` and may require `PYTHONPATH=scripts` (or `set PYTHONPATH=scripts` on Windows) because source code is stored under `scripts/`. See `tests/README.md` for examples.

## Reprocess the exact raw source snapshot

Run commands from the repository root, supplying the path to the **2026Q3 ZIP whose hash matches above**:

```powershell
py -3 .\scripts\run_exact_recoding.py --zip "C:\path\to\SDWA_latest_downloads.zip" --output .\verified_output
py -3 .\scripts\run_robustness_checks.py --zip "C:\path\to\SDWA_latest_downloads.zip" --baseline .\verified_output --output .\robustness_output
py -3 .\scripts\inspect_unkeyed_rows.py --zip "C:\path\to\SDWA_latest_downloads.zip" --baseline .\verified_output --output .\unkeyed_output
```

On another operating system use `python3` and POSIX paths. Never replace 2026Q3 empirical files with results from a newer archive without explicitly treating it as a new extraction.

## Method transparency

A generative AI assistant (ChatGPT, OpenAI) supported script design, debugging, descriptive auditing, and manuscript drafting. The researcher executed the raw-data processing on a local machine. The numeric outputs here are deterministic outputs of the accompanying scripts, not values invented by a model. Human verification and sign-off of scientific claims, references and code are required before journal submission. Read `docs/ai_and_methodology.md`.

## Citation and archiving

The `v0.1.0` release was archived in Zenodo with the version DOI **[10.5281/zenodo.24214908](https://doi.org/10.5281/zenodo.24214908)**. The corresponding [GitHub release](https://github.com/mohamadkamalzade212/sdwis-2024-code2e-recoding/releases/tag/v0.1.0) is public.

**Suggested citation:** Kamalzadeh, M. (2026). *SDWIS 2024 2E indicator sensitivity: five-state replication code and derived outputs* (Version 0.1.0) [Research software and derived outputs]. Zenodo. https://doi.org/10.5281/zenodo.24214908

**Archival note:** The DOI identifies the frozen Zenodo archive of `v0.1.0`, not later changes on the GitHub `main` branch. This retrospective citation update is not part of the frozen release. New research changes should be archived in a new release. DOI registration does not imply journal peer review or independent reprocessing of the original multi-gigabyte EPA ZIP.

## Licensing

No open-source license has yet been selected. The repository is public for inspection, but no general permission to redistribute or adapt the author's code is granted by public visibility alone. The underlying U.S. EPA data have separate provenance. A software license may be added by the author later.

## Author

Mohammad Kamalzadeh · ORCID: https://orcid.org/0009-0006-9918-6467
