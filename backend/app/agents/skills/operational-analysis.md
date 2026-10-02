---
name: operational-analysis
description: Grounded operational investigation, anomaly review, maintenance and data validation.
---
Separate observed measurements, data-quality issues, hypotheses and recommendations. Start with available evidence and its time window. Identify missing measurements before inferring a process fault. Use knowledge_search for approved SOPs and list_incidents for unresolved work. For plant data, call list_data_connections, then describe_timeseries to learn exact measurement and field names and the latest reading time, then read_timeseries over a window that contains data (use aggregation for long windows). Use calculate_statistics for supplied numeric observations. A language model cannot calculate an anomaly probability without a validated model and calibration evidence. Never issue control-system writes. Cite the original source and state limits.
