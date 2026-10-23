# Collection pipeline update

The active workflow is documented in [README.md](../README.md#automatic-data-collection).

- First initialization imports all six historical CSVs with the shared observation importer.
- Startup collects after each variable's maximum historical CSV date through yesterday.
- UH Mesonet uses HCDP first and Synoptic only to fill missing summaries.
- Toa Ridge and Siufaga use the existing R-derived WRCC collection logic.
- Airport5101 uses Synoptic NSTU daily products.
- Provider requests use bounded multi-day chunks; report aggregation happens locally.
- The same runner performs yesterday-only collection at 03:00 Pacific/Pago_Pago daily.

The previous Synoptic-wide period-of-record backfill, station discovery, and
automatic sensor catalog creation are removed. Legacy collection selection and
discovery environment settings are ignored. Keep HCDP_API, SYNOPTIC_TOKEN, and
COLLECTION_TIMEZONE in the deployment environment.

## Existing deployments

Rebuild and recreate the collector with the updated image:

```bash
docker compose up --build -d
docker compose logs --tail 80 collector
```

A new database volume imports all CSVs automatically. For an existing volume
that lacks the newly included soil-moisture or stream-stage history, rerun the
idempotent metadata/history bootstrap once after rebuilding:

```bash
docker compose exec db /opt/weather/venv/bin/python /opt/weather/seed_history.py
```

This updates metadata from the bundled station/variable catalogs and skips
existing observations. Do not delete the database volume. Recreate the collector
after importing any missing historical files so its startup backfill runs against
the complete archive.

Station aliases remain explicit: Poloa=001AS, Aasu=002AS, Vaipito=003AS,
Afono=004AS, Aunuu=005AS, Airport5101=NSTU. WRCC sam1 is Toa Ridge;
sam2 is Siufaga. Additional soil sensors are not merged with the primary
historical soil-moisture variable.

Missing credentials, inaccessible provider products, incomplete daily coverage,
and missing values remain visible in logs and collection health; they are not
treated as successful zero-valued observations.
