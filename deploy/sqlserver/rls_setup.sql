/* Reporting copy setup for one client database (proposal: "Security, Data Isolation and Governance").
   Run on the client's READ-ONLY REPORTING COPY (e.g. Always On readable secondary or scheduled copy),
   never on the live transaction database. Adapt table and column names during discovery.

   What it creates
   1. Schema rpt: business-friendly reporting views. The LLM sees these, never raw tables.
      Sensitive columns (national ID numbers, full account numbers) are masked in the views.
   2. Row-Level Security: a filter predicate reads the user's role, sites and entities from
      SESSION_CONTEXT, which the Report Service sets on every request (reporting_assistant/sqlserver.py).
   3. Least privilege: the Report Service login can SELECT from rpt and nothing else.
*/

CREATE SCHEMA rpt;
GO
CREATE SCHEMA security;
GO

/* ---- 1. Reporting views (examples: replace with the client's tables) ---------------------- */
CREATE VIEW rpt.stock_levels
WITH SCHEMABINDING                      -- views run as the caller; no EXECUTE AS OWNER anywhere
AS
SELECT s.snapshot_date, s.site_id, s.sku, s.qty_on_hand
FROM dbo.stock_snapshot AS s;
GO

CREATE VIEW rpt.customer_balances
WITH SCHEMABINDING
AS
SELECT c.customer_id,
       c.entity_id,
       'XXXX-' + RIGHT(c.account_number, 4) AS account_number_masked,   -- masked
       c.balance
FROM dbo.customer_accounts AS c;
GO

/* ---- 2. Row-Level Security ---------------------------------------------------------------- */
-- Roles that see every site / entity. Others only see the values in SESSION_CONTEXT.
CREATE FUNCTION security.fn_site_filter(@site_id AS nvarchar(50))
    RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS allowed
    WHERE CAST(SESSION_CONTEXT(N'ra_role') AS nvarchar(100)) IN (N'ops_lead', N'executive', N'admin')
       OR @site_id IN (SELECT value FROM STRING_SPLIT(CAST(SESSION_CONTEXT(N'ra_sites') AS nvarchar(4000)), ','));
GO

CREATE FUNCTION security.fn_entity_filter(@entity_id AS nvarchar(50))
    RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS allowed
    WHERE CAST(SESSION_CONTEXT(N'ra_role') AS nvarchar(100)) IN (N'executive', N'admin')
       OR @entity_id IN (SELECT value FROM STRING_SPLIT(CAST(SESSION_CONTEXT(N'ra_entities') AS nvarchar(4000)), ','));
GO

-- Predicates go on the base tables, so every view and query over them is filtered.
-- No SESSION_CONTEXT set (e.g. a reused connection that was cleared) means no rows.
CREATE SECURITY POLICY security.reporting_policy
    ADD FILTER PREDICATE security.fn_site_filter(site_id)     ON dbo.stock_snapshot,
    ADD FILTER PREDICATE security.fn_entity_filter(entity_id) ON dbo.customer_accounts
    WITH (STATE = ON);
GO

/* ---- 3. Least-privilege login for the Report Service ---------------------------------------- */
-- CREATE LOGIN ra_reader WITH PASSWORD = '<from the secrets vault>';   -- once per server
CREATE USER ra_reader FOR LOGIN ra_reader;
GRANT SELECT ON SCHEMA::rpt TO ra_reader;
DENY SELECT ON SCHEMA::dbo TO ra_reader;
DENY SELECT ON SCHEMA::security TO ra_reader;
DENY VIEW DEFINITION TO ra_reader;
-- Grant nothing else: no EXECUTE on user procedures, no write permissions.
GO

/* Red-team checks to run as ra_reader before go-live (all must return no rows or fail):
   EXEC sys.sp_set_session_context N'ra_role', N'warehouse_manager';
   EXEC sys.sp_set_session_context N'ra_sites', N'WH-NORTH';
   SELECT DISTINCT site_id FROM rpt.stock_levels WHERE site_id <> 'WH-NORTH';   -- expect 0 rows
   SELECT * FROM dbo.stock_snapshot;                                             -- expect permission error
   EXEC sys.sp_set_session_context N'ra_sites', NULL;
   SELECT COUNT(*) FROM rpt.stock_levels;                                        -- expect 0 (cleared role)
*/
