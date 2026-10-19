"""Health checks by domain: `clock` is the wall clock every statement runs under and `common` the
shared result shape; `platform` holds jobs, capacity, llm and migration_drift (wave 3); `scraping`
the scraping alarm and the coverage gauge, `publishing` and `delisting` their alarms (wave 4);
waves 5-6 add dedup, parsing and the data-quality gauges."""
