-- Runs once, when the data volume is first created.
-- A separate database for the test suite, so tests never touch demo data.
CREATE DATABASE trustagent_test OWNER trustagent;

\connect trustagent
CREATE EXTENSION IF NOT EXISTS vector;

\connect trustagent_test
CREATE EXTENSION IF NOT EXISTS vector;
