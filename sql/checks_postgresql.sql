-- PostgreSQL / PostGIS version of the data quality rules in src/rules.py.
--
-- The Python pipeline is the reference implementation (it is the one that runs and
-- is tested in this repository). These queries show how the same rules would run
-- inside a database, assuming the CSV was loaded as text columns into
-- rnal(x, y, objectid, nrrnal, denominacao, dataregisto, dataaberturapublico,
--      modalidade, nrutentes, endereco, codigopostal, localidade, latlong,
--      fiabilidadegeo, dtmnfr, freguesia, concelho, distrito, ...).
-- Each query returns the records that FAIL the rule.
-- For the count of every rule in a single query, see scorecard_postgresql.sql.

SET TIME ZONE 'UTC';  -- the CSV timestamps are UTC; date comparisons must not shift

-- C01  Postal code missing
SELECT nrrnal FROM rnal
WHERE NULLIF(TRIM(codigopostal), '') IS NULL;

-- V01  Postal code not in NNNN-NNN format
SELECT nrrnal, codigopostal FROM rnal
WHERE NULLIF(TRIM(codigopostal), '') IS NOT NULL
  AND TRIM(codigopostal) !~ '^\d{4}-\d{3}$';

-- V03  Geocoding reliability outside the domain
SELECT fiabilidadegeo, COUNT(*) FROM rnal
WHERE fiabilidadegeo NOT IN ('Fiavel', 'PoucoFiavel', 'NaoFiavel')
GROUP BY fiabilidadegeo;

-- V04  Capacity zero or negative
SELECT nrrnal, nrutentes FROM rnal
WHERE nrutentes::int <= 0;

-- V05  Placeholder or implausible dates
SELECT nrrnal, dataregisto, dataaberturapublico FROM rnal
WHERE dataregisto::timestamptz < '1950-01-01'
   OR dataaberturapublico::timestamptz < '1950-01-01';

-- V06  Opening date in the future (relative to the extraction date)
SELECT nrrnal, dataaberturapublico FROM rnal
WHERE dataaberturapublico::timestamptz >= DATE '2026-11-14';

-- V07  LatLong text not in the dominant format "38,72 ; -9,13"
SELECT nrrnal, latlong FROM rnal
WHERE latlong !~ '^-?\d+,\d+ ; -?\d+,\d+$';

-- U01  Duplicated registration number
SELECT nrrnal, COUNT(*) FROM rnal
GROUP BY nrrnal
HAVING COUNT(*) > 1;

-- U02  Possible duplicate establishments (same name, address, postal code, modality)
SELECT nrrnal
FROM (
    SELECT nrrnal,
           COUNT(*) OVER (
               -- pg_c_utf8 (PostgreSQL 17+) lowercases accented letters even in a C-locale database
               PARTITION BY LOWER(BTRIM(REGEXP_REPLACE(denominacao, '\s+', ' ', 'g')) COLLATE pg_c_utf8),
                            LOWER(BTRIM(REGEXP_REPLACE(COALESCE(endereco, ''), '\s+', ' ', 'g')) COLLATE pg_c_utf8),
                            BTRIM(COALESCE(codigopostal, '')),
                            modalidade
           ) AS copies
    FROM rnal
    WHERE NULLIF(TRIM(denominacao), '') IS NOT NULL
) t
WHERE copies > 1;

-- K01  The two stored locations (X/Y in EPSG:3857 and the LatLong text) more than 1 km apart
WITH parsed AS (
    SELECT nrrnal,
           ST_SetSRID(ST_MakePoint(x::float8, y::float8), 3857) AS geom_xy,
           REPLACE((REGEXP_MATCH(latlong, '(-?\d+[.,]\d+)\s*;\s*(-?\d+[.,]\d+)'))[1], ',', '.')::float8 AS lat,
           REPLACE((REGEXP_MATCH(latlong, '(-?\d+[.,]\d+)\s*;\s*(-?\d+[.,]\d+)'))[2], ',', '.')::float8 AS lon
    FROM rnal
)
SELECT nrrnal
FROM parsed
WHERE ST_DistanceSphere(ST_Transform(geom_xy, 4326), ST_SetSRID(ST_MakePoint(lon, lat), 4326)) > 1000;

-- K02  Record in a different municipality from at least 90% of its postal code (min. 5 records)
WITH by_cp AS (
    SELECT TRIM(codigopostal) AS cp, LEFT(dtmnfr, 4) AS municipality, COUNT(*) AS n
    FROM rnal
    WHERE TRIM(codigopostal) ~ '^\d{4}-\d{3}$'
    GROUP BY 1, 2
),
reference AS (
    SELECT cp, municipality
    FROM (
        SELECT cp, municipality, n,
               SUM(n) OVER (PARTITION BY cp) AS total
        FROM by_cp
    ) t
    WHERE total >= 5 AND n::numeric / total >= 0.9
)
SELECT r.nrrnal, r.codigopostal, LEFT(r.dtmnfr, 4) AS municipality, ref.municipality AS expected
FROM rnal r
JOIN reference ref ON ref.cp = TRIM(r.codigopostal)
WHERE LEFT(r.dtmnfr, 4) <> ref.municipality;

-- K03  Opening date before registration date (rule to confirm with the data owner)
SELECT nrrnal FROM rnal
WHERE dataaberturapublico::timestamptz::date < dataregisto::timestamptz::date;

-- A01  Capacity far above the norm for its modality (median + 10 x MAD, MAD floor 1)
WITH stats AS (
    SELECT modalidade,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY nrutentes::int) AS median
    FROM rnal GROUP BY modalidade
),
mad AS (
    SELECT r.modalidade, s.median,
           GREATEST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(r.nrutentes::int - s.median)), 1) AS mad
    FROM rnal r JOIN stats s USING (modalidade)
    GROUP BY r.modalidade, s.median
)
SELECT r.nrrnal, r.modalidade, r.nrutentes
FROM rnal r JOIN mad m USING (modalidade)
WHERE r.nrutentes::int > m.median + 10 * m.mad;
