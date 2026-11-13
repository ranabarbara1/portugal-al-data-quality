-- One-query scorecard: number of records failing each rule, computed in PostgreSQL.
-- Compare with results/scorecard.csv (Python). Same rules as checks_postgresql.sql.
-- Requires PostgreSQL 17+ (pg_c_utf8 collation) and PostGIS, and the table from load_rnal_postgresql.sql.

SET TIME ZONE 'UTC';  -- the CSV timestamps are UTC; date comparisons must not shift

WITH base AS (
    SELECT r.*,
           NULLIF(BTRIM(r.codigopostal), '') AS cp,
           r.dataregisto::timestamptz         AS reg_ts,
           r.dataaberturapublico::timestamptz AS open_ts,
           r.nrutentes::int                   AS capacity,
           REGEXP_MATCH(r.latlong, '(-?\d+[.,]\d+)\s*;\s*(-?\d+[.,]\d+)') AS ll
    FROM rnal r
),
dup AS (
    SELECT nrrnal,
           LOWER(BTRIM(COALESCE(denominacao, ''))) IN ('', 'n/a', 'na', 'null', 'none', '-') AS name_missing,
           COUNT(*) OVER (
               PARTITION BY
                   LOWER(BTRIM(REGEXP_REPLACE(COALESCE(denominacao, ''), '\s+', ' ', 'g')) COLLATE pg_c_utf8),
                   LOWER(BTRIM(REGEXP_REPLACE(COALESCE(endereco, ''), '\s+', ' ', 'g')) COLLATE pg_c_utf8),
                   BTRIM(COALESCE(codigopostal, '')),
                   modalidade
           ) AS copies
    FROM rnal
),
cp_reference AS (
    SELECT cp, municipality
    FROM (
        SELECT cp, municipality, n, SUM(n) OVER (PARTITION BY cp) AS total
        FROM (
            SELECT cp, LEFT(dtmnfr, 4) AS municipality, COUNT(*) AS n
            FROM base
            WHERE cp ~ '^\d{4}-\d{3}$'
            GROUP BY 1, 2
        ) g
    ) t
    WHERE total >= 5 AND n::numeric / total >= 0.9
),
capacity_stats AS (
    SELECT b.modalidade, s.median,
           GREATEST(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(b.capacity - s.median)), 1) AS mad
    FROM base b
    JOIN (
        SELECT modalidade, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY capacity) AS median
        FROM base GROUP BY modalidade
    ) s USING (modalidade)
    GROUP BY b.modalidade, s.median
)
SELECT 'C01' AS rule, COUNT(*) FILTER (WHERE cp IS NULL) AS failed FROM base
UNION ALL SELECT 'C02', COUNT(*) FILTER (WHERE LOWER(BTRIM(COALESCE(denominacao, ''))) IN ('', 'n/a', 'na', 'null', 'none', '-')) FROM base
UNION ALL SELECT 'V01', COUNT(*) FILTER (WHERE cp IS NOT NULL AND cp !~ '^\d{4}-\d{3}$') FROM base
UNION ALL SELECT 'V02', COUNT(*) FILTER (WHERE modalidade NOT IN ('Apartamento', 'Moradia', 'Quartos',
                                        'EstabelecimentoHospedagem', 'EstabelecimentoHospedagemHostel')) FROM base
UNION ALL SELECT 'V03', COUNT(*) FILTER (WHERE fiabilidadegeo NOT IN ('Fiavel', 'PoucoFiavel', 'NaoFiavel')) FROM base
UNION ALL SELECT 'V04', COUNT(*) FILTER (WHERE capacity <= 0) FROM base
UNION ALL SELECT 'V05', COUNT(*) FILTER (WHERE reg_ts < '1950-01-01' OR open_ts < '1950-01-01') FROM base
UNION ALL SELECT 'V06', COUNT(*) FILTER (WHERE open_ts > '2026-11-14') FROM base
UNION ALL SELECT 'V07', COUNT(*) FILTER (WHERE COALESCE(latlong, '') !~ '^-?\d+,\d+ ; -?\d+,\d+$') FROM base
UNION ALL SELECT 'U01', COUNT(*) FROM (SELECT nrrnal FROM rnal GROUP BY nrrnal HAVING COUNT(*) > 1) d
UNION ALL SELECT 'U02', COUNT(*) FILTER (WHERE copies > 1 AND NOT name_missing) FROM dup
UNION ALL SELECT 'K01', COUNT(*) FILTER (WHERE ll IS NULL OR ST_DistanceSphere(
              ST_Transform(ST_SetSRID(ST_MakePoint(x::float8, y::float8), 3857), 4326),
              ST_SetSRID(ST_MakePoint(REPLACE(ll[2], ',', '.')::float8, REPLACE(ll[1], ',', '.')::float8), 4326)
          ) > 1000) FROM base
UNION ALL SELECT 'K02', COUNT(*) FROM base b JOIN cp_reference ref ON ref.cp = b.cp
          WHERE LEFT(b.dtmnfr, 4) <> ref.municipality
UNION ALL SELECT 'K03', COUNT(*) FILTER (WHERE open_ts::date < reg_ts::date) FROM base
UNION ALL SELECT 'A01', COUNT(*) FROM base b JOIN capacity_stats c USING (modalidade)
          WHERE b.capacity > c.median + 10 * c.mad
UNION ALL SELECT 'A02', COUNT(*) FILTER (WHERE fiabilidadegeo NOT IN ('Fiavel', 'Fiável')) FROM base
ORDER BY rule;
