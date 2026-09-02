# MegaBench Validation Report

This report compares the private source trace with the public sanitized workload artifact.
Only coarse histograms and divergence values are written here.

## Build Summary

- Source files: 57
- Private records scanned: 242712
- Public workload sample records: 5000
- Published sample ratio: 0.020601
- Minimum query pattern count: 3
- Retained records before sampling: 141270
- Maximum sample size: 5000

## Histogram Divergence

| Histogram | JS divergence | Private bins | Public bins |
|---|---:|---:|---:|
| `label` | 0.000227 | 2 | 2 |
| `query_type` | 0.000227 | 2 | 2 |
| `query_length_bucket` | 0.000128 | 3 | 2 |
| `read_bytes` | 0.002820 | 7 | 7 |
| `lake_read_size` | 0.002011 | 7 | 7 |
| `lake_read_files` | 0.001575 | 8 | 8 |
| `query_duration_ms` | 0.000227 | 7 | 7 |
| `event_hour` | 0.006627 | 24 | 24 |

## Privacy Notes

- Raw SQL text is not emitted.
- Real database, table, column, user, query_id, and exception strings are not emitted.
- Runtime and IO metrics are bucketed and should be treated as oracle labels, not model inputs.
- Query patterns below the configured minimum count are dropped by default.
