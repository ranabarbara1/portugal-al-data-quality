-- Load the RNAL CSV into PostgreSQL so that checks_postgresql.sql can run.
--
-- Run with psql from the folder that contains al.csv:
--     psql -d <your_database> -f load_rnal_postgresql.sql
-- (In DBeaver, run the CREATE statements and then use "Import Data" on the table.)
--
-- Every column is loaded as text on purpose: a quality audit must see the raw
-- values, including the ones that would fail a typed column (e.g. malformed dates).

CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS rnal;

CREATE TABLE rnal (
    x                    text,
    y                    text,
    objectid             text,
    nrrnal               text,
    denominacao          text,
    dataregisto          text,
    dataaberturapublico  text,
    modalidade           text,
    nrutentes            text,
    endereco             text,
    codigopostal         text,
    localidade           text,
    latlong              text,
    fiabilidadegeo       text,
    dtmnfr               text,
    freguesia            text,
    concelho             text,
    distrito             text,
    nutsiii              text,
    nutsii               text,
    ert                  text,
    seloclean_safe       text
);

\copy rnal FROM 'al.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

SELECT COUNT(*) AS records FROM rnal;  -- expected: 112118 for the 2026-11-12 extract
