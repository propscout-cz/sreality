"""Health checks by domain: `clock` is the wall clock every statement runs under and `common` the
shared result shape; `platform` holds jobs, capacity, llm and migration_drift (wave 3); `scraping`
the scraping alarm and the coverage gauge, `publishing` and `delisting` their alarms (wave 4);
wave 5 added dedup (the engine); wave 6 adds parsing and the data-quality gauges."""
